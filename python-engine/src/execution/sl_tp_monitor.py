import asyncio
from typing import Optional
from zoneinfo import ZoneInfo

import structlog
from datetime import datetime, timezone, timedelta

from src.broker.base_broker import BaseBroker
from src.broker.errors import KotakAuthError, KotakOrderUncertainError
from src.broker.factory import load_user_credentials, create_broker
from src.db.audit import write_audit
from src.execution.order_status import classify, poll_order
from src.db.mongo import get_db
from src.utils.market_data import bind_market_data_user, has_market_data, fetch_ltp

log = structlog.get_logger()
IST = ZoneInfo("Asia/Kolkata")

MONITOR_INTERVAL_SEC = 10  # check every 10 seconds
MAX_EXIT_ATTEMPTS = 3
WARN_EVERY_SEC = 600  # repeat the same warning for the same user at most every 10 minutes
EXIT_PENDING_STALE_AFTER = timedelta(minutes=5)


def evaluate_exit(side: str, ltp: float, stop_loss: Optional[float], take_profit: Optional[float]) -> Optional[str]:
    """Pure decision: 'STOP_LOSS', 'TAKE_PROFIT' or None. Works for long (BUY) and short (SELL)."""
    if not ltp or ltp <= 0:
        return None
    if side == "BUY":
        if stop_loss and ltp <= stop_loss:
            return "STOP_LOSS"
        if take_profit and ltp >= take_profit:
            return "TAKE_PROFIT"
    else:
        if stop_loss and ltp >= stop_loss:
            return "STOP_LOSS"
        if take_profit and ltp <= take_profit:
            return "TAKE_PROFIT"
    return None


def realised_pnl(side: str, entry_price: float, exit_price: float, qty: int) -> float:
    direction = 1 if side == "BUY" else -1
    return round((exit_price - entry_price) * qty * direction, 2)


def is_stale_intraday(trade: dict, now: Optional[datetime] = None) -> bool:
    """An MIS position is squared off by the broker at end of day. If our record is still open
    on a LATER day (or after the close), it no longer exists at the broker: sending an exit
    order now would open a naked position in the opposite direction."""
    if trade.get("productType") != "MIS":
        return False
    now = (now or datetime.now(IST)).astimezone(IST)
    opened = trade.get("executedAt") or trade.get("createdAt")
    if not opened:
        return False
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    opened_ist = opened.astimezone(IST)
    if opened_ist.date() < now.date():
        return True
    return now.hour * 60 + now.minute >= 15 * 60 + 30  # after the 15:30 close


class SLTPMonitor:
    """
    Background task that monitors all open (EXECUTED) trades
    that have a stopLoss or takeProfit set.

    Every MONITOR_INTERVAL_SEC it:
      1. Fetches all eligible open trades from MongoDB
      2. Gets current LTP for each symbol
      3. If SL or TP is breached → places exit order
      4. Updates trade to CLOSED with final PnL

    Uses MockBroker in development, KotakNeoBroker in production.
    """

    def __init__(self):
        self.running = True
        self._stop_event = asyncio.Event()
        self._last_warned: dict[str, float] = {}

    def _should_warn(self, key: str, now: float | None = None) -> bool:
        """Throttle repeated warnings: this loop runs every 10 s, so a user without a session
        used to produce a log line every 10 s forever."""
        import time
        now = time.monotonic() if now is None else now
        if now - self._last_warned.get(key, -1e12) >= WARN_EVERY_SEC:
            self._last_warned[key] = now
            return True
        return False

    async def start(self) -> None:
        log.info("sl_tp_monitor.started", interval_sec=MONITOR_INTERVAL_SEC)
        while self.running:
            try:
                await self._check_all_positions()
            except Exception as e:
                log.error("sl_tp_monitor.loop_error", error=str(e), exc_info=True)
            try:
                await asyncio.sleep(MONITOR_INTERVAL_SEC)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self.running = False
        self._stop_event.set()
        log.info("sl_tp_monitor.stopped")

    async def _check_all_positions(self) -> None:
        db = await get_db()
        await self._recover_stale_exits(db)

        by_user: dict[str, list[dict]] = {}
        cursor = db.trades.find({"status": "EXECUTED"})
        async for trade in cursor:
            by_user.setdefault(str(trade["userId"]), []).append(trade)

        for user_id, trades in by_user.items():
            try:
                await self._check_user(db, user_id, trades)
            except Exception as e:
                log.error("sl_tp_monitor.user_error", user_id=user_id, error=str(e), exc_info=True)

    async def _check_user(self, db, user_id: str, trades: list[dict]) -> None:
        bind_market_data_user(user_id)

        broker: Optional[BaseBroker] = None
        try:
            for trade in trades:
                # stale MIS: settled by the broker already, no order needed
                if is_stale_intraday(trade):
                    await self._close_without_order(db, trade, "MIS_AUTO_SQUAREOFF")
                    continue

                if not trade.get("stopLoss") and not trade.get("takeProfit") and not trade.get("slOrderId"):
                    continue

                if broker is None:
                    try:
                        credentials = await load_user_credentials(user_id)
                        broker = await create_broker(user_id, credentials)
                    except KotakAuthError as e:
                        if self._should_warn(f"no_session:{user_id}"):
                            log.warning("sl_tp_monitor.no_session", user_id=user_id, open_trades=len(trades),
                                        error=str(e),
                                        hint="open positions exist but the user has no usable broker session/credentials")
                        return
                await self._check_trade(db, broker, trade)
        finally:
            if broker is not None:
                await broker.close()

    async def _check_trade(self, db, broker: BaseBroker, trade: dict) -> None:
        trade_id = trade["_id"]

        # 1. Did the broker-side stop already execute?
        if trade.get("slOrderId"):
            handled = await self._reconcile_sl_order(db, broker, trade)
            if handled:
                return

        # 2. Price check
        ltp = await self._get_ltp(trade)
        if ltp is None:
            return
        reason = evaluate_exit(trade["side"], ltp, trade.get("stopLoss"), trade.get("takeProfit"))
        if reason is None:
            return

        log.warning("sl_tp_monitor.triggered", trade_id=str(trade_id), symbol=trade["symbol"],
                    reason=reason, ltp=ltp)
        await self._exit_position(db, broker, trade, ltp, reason)

    async def _reconcile_sl_order(self, db, broker: BaseBroker, trade: dict) -> bool:
        """Returns True when the trade was closed / needs no further monitoring this cycle."""
        try:
            status = await broker.get_order_status(trade["slOrderId"])
        except Exception as e:
            log.warning("sl_tp_monitor.sl_status_failed", trade_id=str(trade["_id"]), error=str(e))
            return False

        kind = classify(status.get("status", ""))
        if kind == "filled":
            exit_price = float(status.get("avg_price") or 0) or float(trade.get("stopLoss") or 0)
            await self._finalise(db, trade, exit_price, "BROKER_SL", trade["slOrderId"])
            return True
        if kind == "rejected":
            # the stop is gone: the software monitor is now the only protection
            log.error("sl_tp_monitor.sl_order_dead", trade_id=str(trade["_id"]))
            await db.trades.update_one({"_id": trade["_id"]}, {
                "$set": {"slOrderId": None, "updatedAt": datetime.now(timezone.utc)}})
            trade["slOrderId"] = None
            await write_audit(str(trade["userId"]), "TRADE_SL_ORDER_LOST",
                              {"trade_id": str(trade["_id"]), "order_id": status.get("order_id")})
        return False

    async def _exit_position(self, db, broker: BaseBroker, trade: dict, ltp: float, reason: str) -> None:
        trade_id = trade["_id"]
        user_id = str(trade["userId"])

        # Atomic claim: only one process/cycle may exit a given trade.
        claimed = await db.trades.find_one_and_update(
            {"_id": trade_id, "status": "EXECUTED"},
            {"$set": {"status": "EXIT_PENDING", "exitStartedAt": datetime.now(timezone.utc),
                      "exitReason": reason, "updatedAt": datetime.now(timezone.utc)}},
        )
        if not claimed:
            return

        # Cancel the resting broker stop BEFORE exiting, or both could fire and leave us short.
        sl_order_id = trade.get("slOrderId")
        if sl_order_id:
            try:
                await broker.cancel_order(sl_order_id)
            except Exception as e:
                log.warning("sl_tp_monitor.sl_cancel_failed", order_id=sl_order_id, error=str(e))
            # cancellation can lose a race with a fill: check before selling again
            try:
                st = await broker.get_order_status(sl_order_id)
                if classify(st.get("status", "")) == "filled":
                    exit_price = float(st.get("avg_price") or 0) or ltp
                    await self._finalise(db, trade, exit_price, "BROKER_SL", sl_order_id)
                    return
            except Exception as e:
                # cannot tell whether the stop filled -> do not risk a second sell
                await self._needs_review(db, trade, f"Could not verify broker SL order state: {e}")
                return

        exit_side = "SELL" if trade["side"] == "BUY" else "BUY"
        try:
            order = await broker.place_order(
                symbol=trade.get("brokerSymbol") or trade["symbol"],
                side=exit_side,
                qty=int(trade["qty"]),
                price=0.0,
                order_type="MARKET",
                product_type=trade.get("productType") or "MIS",
                exchange=trade.get("exchange") or trade.get("exchangeSegment") or "NSE",
            )
        except KotakOrderUncertainError as e:
            await self._needs_review(db, trade, f"Exit order outcome unknown: {e}")
            return
        except KotakAuthError as e:
            # nothing was sent; try again next cycle without burning an attempt
            await self._revert_to_executed(db, trade_id, increment=False)
            log.warning("sl_tp_monitor.exit_auth_error", trade_id=str(trade_id), error=str(e))
            return
        except Exception as e:
            attempts = int(trade.get("exitAttempts") or 0) + 1
            log.error("sl_tp_monitor.exit_failed", trade_id=str(trade_id), attempt=attempts, error=str(e))
            if attempts >= MAX_EXIT_ATTEMPTS:
                await self._needs_review(db, trade, f"Exit failed {attempts}x: {e}")
            else:
                await self._revert_to_executed(db, trade_id, increment=True)
            return

        exit_order_id = order.get("order_id", "")
        await db.trades.update_one({"_id": trade_id}, {"$set": {
            "exitOrderId": exit_order_id, "updatedAt": datetime.now(timezone.utc)}})

        fill = await poll_order(broker, exit_order_id)
        if fill["outcome"] == "filled":
            exit_price = fill["avg_price"] if fill["avg_price"] > 0 else ltp
            await self._finalise(db, trade, exit_price, reason, exit_order_id)
        elif fill["outcome"] == "rejected":
            log.error("sl_tp_monitor.exit_rejected", trade_id=str(trade_id), reason=fill["reason"])
            attempts = int(trade.get("exitAttempts") or 0) + 1
            if attempts >= MAX_EXIT_ATTEMPTS:
                await self._needs_review(db, trade, f"Exit rejected {attempts}x: {fill['reason']}")
            else:
                await self._revert_to_executed(db, trade_id, increment=True)
        else:
            await self._needs_review(db, trade, f"Exit order {exit_order_id}: {fill['reason']}")

    async def _finalise(self, db, trade: dict, exit_price: float, reason: str, exit_order_id: str) -> None:
        entry = float(trade.get("price") or 0)
        pnl = realised_pnl(trade["side"], entry, exit_price, int(trade["qty"])) if entry > 0 else None
        now = datetime.now(timezone.utc)
        await db.trades.update_one({"_id": trade["_id"]}, {"$set": {
            "status": "CLOSED",
            "exitPrice": exit_price,
            "exitOrderId": exit_order_id,
            "exitReason": reason,
            "pnl": pnl,
            "closedAt": now,
            "updatedAt": now,
        }})
        await write_audit(str(trade["userId"]), "TRADE_CLOSED", {
            "trade_id": str(trade["_id"]), "symbol": trade["symbol"], "reason": reason,
            "entry": entry, "exit": exit_price, "pnl": pnl,
        })
        log.info("sl_tp_monitor.closed", trade_id=str(trade["_id"]), reason=reason, pnl=pnl)

    async def _close_without_order(self, db, trade: dict, reason: str) -> None:
        """Position no longer exists at the broker. P&L is unknown, so it is left null
        (excluded from P&L totals) rather than guessed."""
        now = datetime.now(timezone.utc)
        await db.trades.update_one({"_id": trade["_id"], "status": "EXECUTED"}, {"$set": {
            "status": "CLOSED", "exitReason": reason, "pnl": None,
            "closedAt": now, "updatedAt": now,
            "errorMessage": "Closed by broker (intraday square-off); realised P&L not recorded",
        }})
        await write_audit(str(trade["userId"]), "TRADE_CLOSED_BY_BROKER",
                          {"trade_id": str(trade["_id"]), "symbol": trade["symbol"], "reason": reason})
        log.warning("sl_tp_monitor.stale_mis_closed", trade_id=str(trade["_id"]), symbol=trade["symbol"])

    async def _needs_review(self, db, trade: dict, message: str) -> None:
        await db.trades.update_one({"_id": trade["_id"]}, {"$set": {
            "status": "NEEDS_REVIEW", "errorMessage": message,
            "updatedAt": datetime.now(timezone.utc)}})
        await write_audit(str(trade["userId"]), "TRADE_NEEDS_REVIEW",
                          {"trade_id": str(trade["_id"]), "symbol": trade["symbol"], "message": message})
        log.error("sl_tp_monitor.needs_review", trade_id=str(trade["_id"]), message=message)

    async def _revert_to_executed(self, db, trade_id, increment: bool) -> None:
        update: dict = {"$set": {"status": "EXECUTED", "updatedAt": datetime.now(timezone.utc)}}
        if increment:
            update["$inc"] = {"exitAttempts": 1}
        await db.trades.update_one({"_id": trade_id, "status": "EXIT_PENDING"}, update)

    async def _recover_stale_exits(self, db) -> None:
        """A crash between 'claim' and 'finish' would leave a trade in EXIT_PENDING forever."""
        cutoff = datetime.now(timezone.utc) - EXIT_PENDING_STALE_AFTER
        async for trade in db.trades.find({"status": "EXIT_PENDING", "exitStartedAt": {"$lt": cutoff}}):
            if trade.get("exitOrderId"):
                # an exit order exists: a human must confirm what happened
                await self._needs_review(db, trade, "Exit interrupted after the order was placed")
            else:
                await self._revert_to_executed(db, trade["_id"], increment=False)
                log.warning("sl_tp_monitor.exit_reverted", trade_id=str(trade["_id"]))

    async def _get_ltp(self, trade: dict) -> Optional[float]:
        """LTP via the token stored on the trade (options need the numeric token; a
        trading-symbol string returns nothing from the quotes API)."""
        segment, token = trade.get("exchangeSegment"), trade.get("symbolToken")
        if not (segment and token):
            log.warning("sl_tp_monitor.no_token", trade_id=str(trade["_id"]), symbol=trade["symbol"])
            return None
        if not has_market_data(str(trade["userId"])):
            return None
        try:
            return await fetch_ltp(segment, token)
        except Exception as e:
            log.warning("sl_tp_monitor.ltp_failed", symbol=trade["symbol"], error=str(e))
            return None