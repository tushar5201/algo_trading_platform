import asyncio
from datetime import datetime, timezone

from bson import ObjectId

from src.broker.base_broker import BaseBroker
from src.broker.kotak_neo import KotakAuthError, KotakAPIError
from src.db.mongo import get_db
from src.execution.validator import ValidationError, validate_signal
from src.execution.risk_manager import RiskManager
from src.execution.signal import Signal, SignalAction
import structlog

from src.utils.market_data import get_scrip_master

log = structlog.get_logger()

# Order status polling config
POLL_INTERVAL_SEC = 2
POLL_MAX_ATTEMPTS = 15

# Kotak order status values
FILLED_STATUSES = {"filled", "complete", "traded"}
REJECTED_STATUSES = {"rejected", "cancelled", "cancel"}
PENDING_STATUSES = {
    "open",
    "pending",
    "open pending",
    "after market order req received",
}


class OrderManager:
    def __init__(self) -> None:
        self.risk_manager = RiskManager()

    async def execute(
            self, signal: Signal, user_id: str, broker: BaseBroker, credentials: dict
    ) -> dict:
        """
        Full trade lifecycle:
            1. Validate signal
            2. Risk check
            3. Margin check
            4. Create trade doc (PENDING)
            5. Place order
            6. Poll until filled / rejected / timeout
            7. Calculate PnL
            8. Update Trade doc
            9. Write audit log
        Returns result dict.
        """
        db = await get_db()

        # 1. validate
        try:
            await validate_signal(signal, user_id)
        except ValidationError as e:
            log.warning("order.validation_blocked", user_id=user_id, reason=str(e))
            return {"success": False, "stage": "validation", "reason": str(e)}

        # 2. Risk check
        risk = await self.risk_manager.check(signal, user_id)
        if not risk.passed:
            log.warning("order.risk_blocked", user_id=user_id, reason=risk.reason)
            await self._write_audit(
                db,
                user_id,
                "TRADE_RISK_BLOCKED",
                {"reason": risk.reason, "symbol": signal.symbol},
            )
            return {"success": False, "stage": "risk", "reason": risk.reason}

        broker_symbol = signal.symbol
        scrip = get_scrip_master()
        if scrip:
            resolve = scrip.resolve_trading_symbol(signal.symbol)
            if resolve:
                broker_symbol = resolve
                resolve_pair = scrip.resolve(resolve)
                if resolve_pair:
                    _, token = resolve_pair
                    signal.metadata = {**(signal.metadata or {}), "symbol_token": token}
            else:
                log.warning(
                    "order.symbol_not_resolved",
                    symbol=signal.symbol,
                    note="No matching pTrdSymbol found in scrip master;"
                         "sending symbol as-is, order will likely be rejected."
                )

        # 3. Margin check
        margin_ok = await self._check_margin(broker, signal, credentials)
        if not margin_ok:
            log.warning(
                "order.insufficient_margin", user_id=user_id, symbol=signal.symbol
            )
            return {
                "success": False,
                "stage": "margin",
                "reason": "Insufficient margin for this order",
            }

        # 4. Create trade doc
        trade_id = await self._create_trade_doc(db, signal, user_id)
        log.info("order.trade_doc_created", trade_id=trade_id, symbol=signal.symbol)

        # 5. Place Order
        try:
            if signal.stop_loss and hasattr(broker, "place_order_with_sl"):
                order_result = await broker.place_order_with_sl(
                    symbol=broker_symbol,
                    side=signal.action.value,
                    qty=signal.qty,
                    price=signal.price,
                    order_type=signal.order_type.value,
                    product_type=signal.product_type.value,
                    exchange=signal.exchange.value,
                    stop_loss=signal.stop_loss,
                )
            else:
                order_result = await broker.place_order(
                    symbol=broker_symbol,
                    side=signal.action.value,
                    qty=signal.qty,
                    price=signal.price,
                    order_type=signal.order_type.value,
                    product_type=signal.product_type.value,
                    exchange=signal.exchange.value,
                )
            order_id = order_result.get("order_id", "")
        except KotakAuthError as e:
            await self._mark_failed(db, trade_id, str(e))
            return {
                "success": False,
                "stage": "auth",
                "reason": str(e),
                "trade_id": trade_id,
            }
        except KotakAPIError as e:
            await self._mark_failed(db, trade_id, str(e))
            await self._write_audit(
                db,
                user_id,
                "TRADE_ORDER_FAILED",
                {"trade_id": trade_id, "symbol": signal.symbol, "error": str(e)},
            )
            return {
                "success": False,
                "stage": "broker",
                "reason": str(e),
                "trade_id": trade_id,
            }
        except Exception as e:
            await self._mark_failed(db, trade_id, str(e))
            log.error("order.unexpected_error", error=str(e), exc_info=True)
            return {
                "success": False,
                "stage": "unknown",
                "reason": str(e),
                "trade_id": trade_id,
            }

        await db.trades.update_one(
            {"_id": ObjectId(trade_id)}, {"$set": {"orderId": order_id}}
        )
        log.info("order.place", trade_id=trade_id, symbol=signal.symbol)

        # 6. Poll order status
        fill_result = await self._poll_order_status(broker, order_id, trade_id)
        if not fill_result["filled"]:
            await self._mark_failed(db, trade_id, fill_result["reason"])
            await self._write_audit(
                db,
                user_id,
                "TRADE_FILL_FAILED",
                {
                    "trade_id": trade_id,
                    "order_id": order_id,
                    "reason": fill_result["reason"],
                },
            )
            return {
                "success": False,
                "stage": "fill",
                "reason": fill_result["reason"],
                "trade_id": trade_id,
                "order_id": order_id,
            }

        # 7. Calculate PnL
        fill_price = fill_result.get("avg_price", signal.price)
        pnl = self._calculate_pnl(signal, fill_price)

        # 8. Update Trade doc -> EXECUTED
        await db.trades.update_one(
            {"_id": ObjectId(trade_id)},
            {
                "$set": {
                    "status": "EXECUTED",
                    "orderId": order_id,
                    "price": fill_price,
                    "pnl": pnl,
                    "executedAt": datetime.now(timezone.utc),
                    "metadata": {
                        **signal.metadata,
                        "slOrderId": order_result.get("sl_order_id"),
                        "fillResult": fill_result,
                    },
                }
            },
        )

        # 9. Audit log
        await self._write_audit(
            db,
            user_id,
            "TRADE_EXECUTED",
            {
                "trade_id": trade_id,
                "order_id": order_id,
                "symbol": signal.symbol,
                "side": signal.action.value,
                "qty": signal.qty,
                "fill_price": fill_price,
                "pnl": pnl,
            },
        )

        log.info(
            "order.complete",
            trade_id=trade_id,
            order_id=order_id,
            fill_price=fill_price,
            pnl=pnl,
        )
        return {
            "success": True,
            "trade_id": trade_id,
            "order_id": order_id,
            "fill_price": fill_price,
            "pnl": pnl,
            "symbol": signal.symbol,
            "side": signal.action.value,
            "qty": signal.qty,
        }

    # Helpers
    async def _check_margin(
            self, broker: BaseBroker, signal: Signal, credentials: dict
    ) -> bool:
        """
        Returns True if margin is sufficient. Non-blocking on error.
        """
        try:
            if not hasattr(broker, "check_margin"):
                return True
            symbol_token = signal.metadata.get("symbol_token", "")
            if not symbol_token:
                log.warning(
                    "order.margin_check_skipped", reason="No symbol token in metadata"
                )
                return True
            result = await broker.check_margin(
                exchange=signal.exchange.value,
                symbol_token=symbol_token,
                price=signal.price,
                qty=signal.qty,
                side=signal.action.value,
                order_type=signal.order_type.value,
                product_type=signal.product_type.value,
            )
            rms_validated = result.get("rmsVldtd", "NOT_OK")
            insuf_fund = float(result.get("insufFund", "0") or "0")
            if rms_validated == "NOT_OK" and insuf_fund > 0:
                log.warning(
                    "order.insufficient_margin",
                    insuf_fund=insuf_fund,
                    avl_margin=result.get("avlMrgn"),
                )
                return False
            return True
        except Exception as e:
            log.warning("order.margin_check_error", error=str(e))
            return True

    async def _poll_order_status(
            self, broker: BaseBroker, order_id: str, trade_id: str
    ) -> dict:
        """
        Poll kotak order status until filled / rejected / timeout
        Returns {filled: bool, reason: str, avg_price: float }
        """
        log.info("order.pollling_start", order_id=order_id)
        for attempt in range(POLL_MAX_ATTEMPTS):
            await asyncio.sleep(POLL_INTERVAL_SEC)
            try:
                status = await broker.get_order_status(order_id)
                order_status = status.get("status", "").lower().strip()
                log.debug(
                    "order.poll",
                    attempt=attempt + 1,
                    order_id=order_id,
                    status=order_status,
                )
                if order_status in FILLED_STATUSES:
                    avg_price = float(status.get("avg_price") or 0)
                    log.info("order.filled", order_id=order_id, avg_price=avg_price)
                    return {
                        "filled": True,
                        "reason": "Order filled",
                        "avg_price": avg_price,
                        "status": order_status,
                    }

                if order_status in REJECTED_STATUSES:
                    reason = status.get("raw", [{}])[0].get("rejRsn", "Order rejected")
                    log.warning("order.rejected", order_id=order_id, reason=reason)
                    return {
                        "filled": False,
                        "reason": reason,
                        "avg_price": 0,
                    }

            except Exception as e:
                log.warning("order.poll_error", attempt=attempt, error=str(e))
        log.warning("order.poll_timeout", order_id=order_id)
        return {
            "filled": False,
            "reason": f"Order timeout after {POLL_MAX_ATTEMPTS * POLL_INTERVAL_SEC}s",
            "avg_price": 0,
        }

    async def _create_trade_doc(self, db, signal: Signal, user_id: str) -> str:
        doc = {
            "userId": ObjectId(user_id),
            "strategyName": signal.strategy_name,
            "symbol": signal.symbol,
            "side": signal.action.value,
            "qty": signal.qty,
            "price": signal.price,
            "orderId": None,
            "status": "PENDING",
            "pnl": None,
            "stopLoss": signal.stop_loss,
            "takeProfit": signal.take_profit,
            "errorMessage": None,
            "executedAt": None,
            "confidence": signal.confidence,
            "reason": signal.reason,
            "metadata": signal.metadata,
            "createdAt": datetime.now(timezone.utc),
        }
        result = await db.trades.insert_one(doc)
        return str(result.inserted_id)

    def _calculate_pnl(self, signal: Signal, fill_price: float) -> float:
        """
        PnL for the entry order itself.
        For BUY: negative(cost). PnL realized on exit.
        For SELL: positive(cost).
        Full PnL calculation requires tracking position exit - done in STEP 7
        """
        value = fill_price * signal.qty
        if signal.action == SignalAction.BUY:
            return round(-value, 2)
        return round(value, 2)

    async def _mark_failed(self, db, trade_id: str, error: str) -> None:
        await db.trades.update_one(
            {"_id": ObjectId(trade_id)},
            {"$set": {"status": "FAILED", "errorMessage": error}},
        )
        log.error("order.mark_failed", trade_id=trade_id, error=error)

    async def _write_audit(self, db, user_id: str, action: str, metadata: dict) -> None:
        try:
            await db.audit_logs.insert_one(
                {
                    "actorId": ObjectId(user_id),
                    "actorRole": "system",
                    "action": action,
                    "targetId": None,
                    "metadata": metadata,
                    "createdAt": datetime.now(timezone.utc),
                }
            )
        except Exception as e:
            log.error("order.audit_write_failed", error=str(e))
