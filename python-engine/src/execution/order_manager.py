import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId

from src.broker.errors import KotakOrderUncertainError
from src.db.audit import write_audit
from src.db.redis_client import get_redis
from src.execution.order_status import poll_order, classify
from src.execution.risk_manager import reference_price
from src.broker.base_broker import BaseBroker
from src.broker.kotak_neo import KotakAuthError, KotakAPIError
from src.db.mongo import get_db
from src.execution.validator import ValidationError, validate_signal
from src.execution.risk_manager import RiskManager
from src.execution.signal import Signal, SignalAction
import structlog

from src.utils.market_data import get_scrip_master
from src.utils.market_data import fetch_ltp

log = structlog.get_logger()

LOCK_TTL_SEC = 180
_RELEASE_LUA = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


class OrderManager:
    def __init__(self) -> None:
        self.risk_manager = RiskManager()

    async def _acquire_lock(self, key: str) -> Optional[str]:
        token = uuid.uuid4().hex
        ok = await get_redis().set(key, token, nx=True, ex=LOCK_TTL_SEC)
        return token if ok else None

    async def _release_lock(self, key: str, token: str) -> None:
        try:
            await get_redis().eval(_RELEASE_LUA, 1, key, token)
        except Exception as e:
            log.warning("order.lock_release_failed", key=key, error=str(e))

    async def execute(
            self, signal: Signal, user_id: str, broker: BaseBroker, credentials: dict
    ) -> dict:
        lock_key = f"order_lock:{user_id}:{signal.strategy_name}"
        token = await self._acquire_lock(lock_key)
        if token is None:
            log.warning("order.locked", user_id=user_id, strategy=signal.strategy_name)
            return {"success": False, "stage": "lock",
                    "reason": "Another order for this user/strategy is in progress"}
        try:
            return await self._execute_locked(signal, user_id, broker, credentials)
        finally:
            await self._release_lock(lock_key, token)

    async def _execute_locked(
            self, signal: Signal, user_id: str, broker: BaseBroker, credentials: dict
    ) -> dict:
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
            await write_audit(
                user_id,
                "TRADE_RISK_BLOCKED",
                {"reason": risk.reason, "symbol": signal.symbol},
            )
            return {"success": False, "stage": "risk", "reason": risk.reason}

        broker_symbol, segment, token = self._resolve_instrument(signal)
        if token:
            signal.metadata = {**(signal.metadata or {}), "symbol_token": token}

        if not await self._check_margin(broker, signal):
            log.warning("order.insufficient_margin", user_id=user_id, symbol=signal.symbol)
            return {"success": False, "stage": "margin", "reason": "Insufficient margin for this order"}

        # 4. Create trade doc
        trade_id = await self._create_trade_doc(db, signal, user_id, broker_symbol, segment, token)
        log.info("order.trade_doc_created", trade_id=trade_id, symbol=signal.symbol)

        # 5. Place Order
        try:
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
        except KotakOrderUncertainError as e:
            await self._set(db, trade_id, {"status": "NEEDS_REVIEW", "errorMessage": str(e)})
            await write_audit(
                user_id,
                "TRADE_ORDER_UNCERTAIN",
                {
                    "trade_id": trade_id,
                    "symbol": signal.symbol,
                    "error": str(e)
                }
            )
            return {"success": False, "stage": "uncertain", "reason": str(e), "trade_id": trade_id}
        except KotakAuthError as e:
            await self._mark_failed(db, trade_id, str(e))
            invalidate = getattr(broker, "invalidate_session", None)
            if invalidate:
                await invalidate()

            return {
                "success": False,
                "stage": "auth",
                "reason": str(e),
                "trade_id": trade_id,
            }
        except KotakAPIError as e:
            await self._mark_failed(db, trade_id, str(e))
            await write_audit(
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
            log.error("order.unexpected_error", error=str(e), exc_info=True)
            await self._set(db, trade_id, {"status": "NEEDS_REVIEW", "errorMessage": f"Unexpected: {e}"})
            return {
                "success": False,
                "stage": "unknown",
                "reason": str(e),
                "trade_id": trade_id,
            }

        await self._set(db, trade_id, {"orderId": order_id})
        log.info("order.place", trade_id=trade_id, symbol=signal.symbol, order_id=order_id)

        # 6. Poll order status
        fill = await poll_order(broker, order_id)
        if fill["outcome"] == "timeout":
            fill = await self._resolve_timeout(broker, order_id)

        if fill["outcome"] == "unknown":
            await self._set(db, trade_id, {"status": "NEEDS_REVIEW", "errorMessage": fill["reason"]})
            await write_audit(user_id, "TRADE_FILL_UNKNOWN", {
                "trade_id": trade_id,
                "orderId": order_id,
                "reason": fill["reason"],
            })
            return {"success": False, "stage": "fill", "reason": fill["reason"],
                    "trade_id": trade_id, "order_id": order_id}

        if fill["outcome"] != "filled":
            await self._mark_failed(db, trade_id, fill["reason"])
            await write_audit(user_id, "TRADE_FILL_FAILED",
                              {"trade_id": trade_id, "order_id": order_id, "reason": fill["reason"]})
            return {"success": False, "stage": "fill", "reason": fill["reason"],
                    "trade_id": trade_id, "order_id": order_id}

        # 7. Calculate PnL
        fill_price = await self._determine_fill_price(fill["avg_price"], signal, segment, token)
        if fill_price is None:
            msg = "Order filled but the fill price could not be determined"
            await self._set(db, trade_id, {"status": "NEEDS_REVIEW", "errorMessage": msg})
            await write_audit(user_id, "TRADE_FILL_PRICE_UNKNOWN", {
                "trade_id": trade_id,
                "orderId": order_id
            })
            return {"success": False, "stage": "fill", "reason": msg, "trade_id": trade_id, "order_id": order_id}

        await self._set(db, trade_id, {
            "status": "EXECUTED",
            "orderId": order_id,
            "price": fill_price,
            "pnl": None,
            "executedAt": datetime.now(timezone.utc),
            "metadata": {**(signal.metadata or {}), "fillResult": fill},
        })

        sl_order_id = await self._place_protective_stop(db, broker, signal, user_id, trade_id, broker_symbol)
        await write_audit(
            user_id,
            "TRADE_EXECUTED",
            {
                "trade_id": trade_id,
                "order_id": order_id,
                "symbol": signal.symbol,
                "side": signal.action.value,
                "qty": signal.qty,
                "fill_price": fill_price,
                "sl_order_id": sl_order_id
            })

        log.info(
            "order.complete",
            trade_id=trade_id,
            order_id=order_id,
            fill_price=fill_price,
            sl_order_id=sl_order_id
        )
        return {
            "success": True,
            "trade_id": trade_id,
            "order_id": order_id,
            "fill_price": fill_price,
            "sl_order_id": sl_order_id,
            "symbol": signal.symbol,
            "side": signal.action.value,
            "qty": signal.qty,
        }

    def _resolve_instrument(self, signal: Signal) -> tuple[str, Optional[str], Optional[str]]:
        scrip = get_scrip_master()
        if not scrip or not getattr(scrip, "_loaded", False):
            return signal.symbol, None, None

        trading_symbol = scrip.resolve_trading_symbol(signal.symbol)
        if not trading_symbol and scrip.get_token(signal.symbol):
            trading_symbol = signal.symbol
        if not trading_symbol:
            log.warning(
                "order.symbol_not_resolved",
                symbol=signal.symbol,
                note="No matching pTrdSymbol in scrip master; sending as-is, order will likely be rejected.",
            )
            return signal.symbol, None, None
        pair = scrip.resolve(trading_symbol)
        if pair:
            return trading_symbol, pair[0], pair[1]
        return trading_symbol, None, None

    async def _determine_fill_price(
            self, avg_price: float, signal: Signal, segment: Optional[str], token: Optional[str]
    ) -> Optional[float]:
        if avg_price and avg_price > 0:
            return float(avg_price)
        ref = reference_price(signal)
        if ref:
            log.warning("order.fill_price_fallback", source="signal_reference", price=ref)
            return ref
        if segment and token:
            try:
                ltp = await fetch_ltp(segment, token)
                if ltp:
                    log.warning("order.fill_price_fallback", source="ltp", ltp=ltp)
                    return ltp
            except Exception as e:
                log.warning("order.fill_price_ltp_failed", error=str(e))
        return None

    async def _resolve_timeout(self, broker: BaseBroker, order_id: str) -> dict:
        """Entry not filled in time. Cancel it, then re-check - it may have filled in the meantime."""
        cancelled = False
        try:
            cancelled = bool(await broker.cancel_order(order_id))
        except Exception as e:
            log.warning("order.cancel_failed", order_id=order_id, error=str(e))

        try:
            status = await broker.get_order_status(order_id)
            kind = classify(status.get("status", ""))
            if kind == "filled":
                return {"outcome": "filled", "avg_price": float(status.get("avg_price") or 0),
                        "reason": "Order filled while cancelling"}
            if kind == "rejected":
                return {"outcome": "rejected", "avg_price": 0.0, "reason": "Order cancelled after timeout"}
        except Exception as e:
            log.warning("order.recheck_failed", order_id=order_id, error=str(e))

        if cancelled:
            return {"outcome": "rejected", "avg_price": 0.0, "reason": "Order not filled in time; cancelled"}
        return {"outcome": "unknown", "avg_price": 0.0,
                "reason": "Order not filled in time and could not be cancelled - check the broker order book"}

    async def _place_protective_stop(
            self, db, broker: BaseBroker, signal: Signal, user_id: str, trade_id: str, broker_symbol: str
    ) -> Optional[str]:
        if not signal.stop_loss or signal.stop_loss <= 0:
            return None
        try:
            result = await broker.place_sl_order(
                symbol=broker_symbol,
                position_side=signal.action.value,
                qty=signal.qty,
                trigger_price=float(signal.stop_loss),
                product_type=signal.product_type.value,
                exchange=signal.exchange.value,
            )
            sl_order_id = result.get("order_id") or None
            await self._set(db, trade_id, {"slOrderId": sl_order_id})
            return sl_order_id
        except Exception as e:
            # Not fatal: the SL/TP monitor still watches the price. But never silent.
            log.error("order.sl_placement_failed", trade_id=trade_id, error=str(e))
            await write_audit(user_id, "TRADE_SL_PLACEMENT_FAILED",
                              {"trade_id": trade_id, "error": str(e)})
            return None

    # Helpers
    async def _check_margin(self, broker: BaseBroker, signal: Signal) -> bool:
        """Returns True if margin is sufficient. Non-blocking on error (the broker re-checks)."""
        try:
            if not hasattr(broker, "check_margin"):
                return True
            symbol_token = (signal.metadata or {}).get("symbol_token", "")
            if not symbol_token:
                log.warning("order.margin_check_skipped", reason="No symbol token in metadata")
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
                # Log everything Kotak returned so it can be compared with the Funds screen
                # (wallet balance, available margin and margin used are three different numbers).
                log.warning(
                    "order.insufficient_margin",
                    insuf_fund=insuf_fund,
                    avl_margin=result.get("avlMrgn"),
                    avl_cash=result.get("avlCash"),
                    order_margin=result.get("ordMrgn"),
                    required_margin=result.get("reqdMrgn"),
                    margin_used=result.get("totMrgnUsd"),
                    rms=rms_validated,
                    qty=signal.qty,
                    symbol=signal.symbol,
                    raw=result,
                )
                return False
            return True
        except Exception as e:
            log.warning("order.margin_check_error", error=str(e))
            return True

    async def _create_trade_doc(
            self, db, signal: Signal, user_id: str,
            broker_symbol: str, segment: Optional[str], token: Optional[str],
    ) -> str:
        now = datetime.now(timezone.utc)
        doc = {
            "userId": ObjectId(user_id),
            "strategyName": signal.strategy_name,
            "symbol": signal.symbol,
            "brokerSymbol": broker_symbol,
            "side": signal.action.value,
            "qty": signal.qty,
            "price": signal.price or reference_price(signal) or 0.0,
            "orderId": None,
            "status": "PENDING",
            "pnl": None,
            "exchange": signal.exchange.value,
            "exchangeSegment": segment,
            "productType": signal.product_type.value,
            "symbolToken": token,
            "stopLoss": signal.stop_loss,
            "takeProfit": signal.take_profit,
            "slOrderId": None,
            "exitPrice": None,
            "exitOrderId": None,
            "exitReason": None,
            "exitAttempts": 0,
            "errorMessage": None,
            "executedAt": None,
            "closedAt": None,
            "confidence": signal.confidence,
            "reason": signal.reason,
            "metadata": signal.metadata,
            "createdAt": now,
            "updatedAt": now,
        }
        result = await db.trades.insert_one(doc)
        return str(result.inserted_id)

    async def _set(self, db, trade_id: str, fields: dict) -> None:
        fields = {**fields, "updatedAt": datetime.now(timezone.utc)}
        await db.trades.update_one({"_id": ObjectId(trade_id)}, {"$set": fields})

    async def _mark_failed(self, db, trade_id: str, error: str) -> None:
        await self._set(db, trade_id, {"status": "FAILED", "errorMessage": error})
        log.error("order.mark_failed", trade_id=trade_id, error=error)
