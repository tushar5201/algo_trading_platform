import asyncio
import json
import structlog
from datetime import datetime, timezone
from bson import ObjectId

from src.db.mongo import get_db
from src.config.settings import settings

log = structlog.get_logger()

MONITOR_INTERVAL_SEC = 10  # check every 10 seconds


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

    async def start(self) -> None:
        log.info("sl_tp_monitor.started", interval_sec=MONITOR_INTERVAL_SEC)
        while self.running:
            try:
                await self._check_all_positions()
            except Exception as e:
                log.error("sl_tp_monitor.loop_error", error=str(e), exc_info=True)
            await asyncio.sleep(MONITOR_INTERVAL_SEC)

    def stop(self) -> None:
        self.running = False
        log.info("sl_tp_monitor.stopped")

    # ── Main check ───────────────────────────────────────────────────────────

    MONITOR_INTERVAL_SEC = 10
    MAX_EXIT_ATTEMPTS = 3  # stop trying after 3 failed exits

    async def _check_all_positions(self) -> None:
        db = await get_db()

        cursor = db.trades.find(
            {
                "status": "EXECUTED",
                "side": "BUY",
                "$and": [
                    {
                        "$or": [
                            {"stopLoss": {"$ne": None, "$exists": True}},
                            {"takeProfit": {"$ne": None, "$exists": True}},
                        ]
                    },
                    # Skip trades that have failed to exit too many times
                    {
                        "$or": [
                            {"exitAttempts": {"$exists": False}},
                            {"exitAttempts": {"$lt": 3}},
                        ]
                    },
                ],
            }
        )

        trades = await cursor.to_list(length=100)

        if not trades:
            return

        log.debug("sl_tp_monitor.checking", count=len(trades))

        # Check in parallel but limit concurrency
        sem = asyncio.Semaphore(5)

        async def check_with_sem(trade):
            async with sem:
                await self._check_trade(trade)

        await asyncio.gather(
            *[check_with_sem(t) for t in trades],
            return_exceptions=True,
        )

    async def _exit_position(self, trade: dict, exit_price: float, reason: str) -> None:
        db = await get_db()
        trade_id = str(trade["_id"])
        user_id = str(trade["userId"])

        # Atomically mark as EXIT_PENDING
        result = await db.trades.update_one(
            {"_id": trade["_id"], "status": "EXECUTED"},
            {"$set": {"status": "EXIT_PENDING"}},
        )

        if result.modified_count == 0:
            return

        try:
            broker = await self._get_broker(user_id)
            order_result = await broker.place_order(
                symbol=trade["symbol"],
                side="SELL",
                qty=int(trade["qty"]),
                price=0.0,
                order_type="MARKET",
                product_type=trade.get("productType", "MIS"),
                exchange=trade.get("exchange", "nse_fo"),
            )

            entry_price = float(trade.get("price", 0) or 0)
            final_pnl = round((exit_price - entry_price) * int(trade["qty"]), 2)

            await db.trades.update_one(
                {"_id": trade["_id"]},
                {
                    "$set": {
                        "status": "CLOSED",
                        "exitPrice": exit_price,
                        "exitOrderId": order_result.get("order_id"),
                        "exitReason": reason,
                        "pnl": final_pnl,
                        "closedAt": datetime.now(timezone.utc),
                    }
                },
            )

            log.info(
                "sl_tp.exit_complete",
                trade_id=trade_id,
                pnl=final_pnl,
                reason=reason,
            )

        except Exception as e:
            log.error("sl_tp.exit_failed", trade_id=trade_id, error=str(e))

            # Count failed attempts — stop retrying after MAX_EXIT_ATTEMPTS
            await db.trades.update_one(
                {"_id": trade["_id"]},
                {
                    "$set": {"status": "EXECUTED"},
                    "$inc": {"exitAttempts": 1},
                },
            )

    # ── Per-trade check ──────────────────────────────────────────────────────

    async def _check_trade(self, trade: dict) -> None:
        trade_id = str(trade["_id"])
        user_id = str(trade["userId"])
        symbol = trade.get("symbol", "")
        entry_price = float(trade.get("price", 0) or 0)
        stop_loss = trade.get("stopLoss")
        take_profit = trade.get("takeProfit")
        qty = int(trade.get("qty", 0) or 0)

        if not entry_price or not qty or not symbol:
            return

        # Get current LTP
        ltp = await self._get_ltp(symbol, user_id)
        if ltp is None or ltp <= 0:
            return

        # Calculate live PnL
        live_pnl = (ltp - entry_price) * qty

        log.debug(
            "sl_tp.check",
            trade_id=trade_id,
            symbol=symbol,
            entry=entry_price,
            ltp=ltp,
            live_pnl=round(live_pnl, 2),
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        # Determine if exit needed
        should_exit = False
        exit_reason = ""

        if stop_loss is not None and ltp <= float(stop_loss):
            should_exit = True
            exit_reason = f"Stop loss hit: LTP {ltp} ≤ SL {stop_loss}"
            log.warning(
                "sl_tp.stop_loss_triggered",
                trade_id=trade_id,
                symbol=symbol,
                ltp=ltp,
                stop_loss=stop_loss,
                live_pnl=round(live_pnl, 2),
            )

        elif take_profit is not None and ltp >= float(take_profit):
            should_exit = True
            exit_reason = f"Take profit hit: LTP {ltp} ≥ TP {take_profit}"
            log.info(
                "sl_tp.take_profit_triggered",
                trade_id=trade_id,
                symbol=symbol,
                ltp=ltp,
                take_profit=take_profit,
                live_pnl=round(live_pnl, 2),
            )

        if should_exit:
            await self._exit_position(trade, ltp, exit_reason)

    # ── LTP fetch ────────────────────────────────────────────────────────────

    async def _get_ltp(self, symbol: str, user_id: str) -> float | None:
        """
        Gets current Last Traded Price for a symbol.
        Uses live Kotak Quotes API in production.
        Uses mock random price in development.
        """
        if settings.use_mock_broker:
            return await self._mock_ltp(symbol)

        try:
            from src.utils.market_data import get_market_data, _map_symbol

            md = get_market_data()
            if md is None:
                # No live market data client — try to restore
                log.warning(
                    "sl_tp.no_market_data",
                    symbol=symbol,
                    user_id=user_id,
                )
                return None

            exchange, kotak_symbol = _map_symbol(symbol)

            # For options symbols like NIFTY26MAY22400CE
            # use the symbol directly as kotak_symbol
            if symbol.endswith("CE") or symbol.endswith("PE"):
                exchange = "nse_fo"
                kotak_symbol = symbol

            quote = await md.get_quote(exchange, kotak_symbol)
            ltp = float(quote.get("ltp", 0) or 0)

            log.debug(
                "sl_tp.ltp_fetched",
                symbol=symbol,
                ltp=ltp,
            )
            return ltp if ltp > 0 else None

        except Exception as e:
            log.warning(
                "sl_tp.ltp_fetch_failed",
                symbol=symbol,
                error=str(e),
            )
            return None

    async def _mock_ltp(self, symbol: str) -> float:
        """
        Returns realistic mock option PREMIUM price (not index price).
        Options trade between ₹5 and ₹500 typically.
        """
        import random

        # For option symbols (CE/PE) return option premium range
        if symbol.endswith("CE") or symbol.endswith("PE"):
            # Simulate small price movement around a base premium
            # Use symbol hash for consistent base price per symbol
            base = (hash(symbol) % 400) + 50  # 50 to 450
            movement = random.uniform(0.92, 1.08)
            return round(base * movement, 2)

        # For index/equity symbols return index price
        index_prices = {
            "NIFTY": 22_400.0,
            "BANKNIFTY": 48_000.0,
            "SENSEX": 73_000.0,
            "RELIANCE": 2_800.0,
            "TCS": 3_500.0,
            "INFY": 1_600.0,
        }
        base = index_prices.get(symbol, 500.0)
        return round(base * random.uniform(0.98, 1.02), 2)

    # ── Broker factory ───────────────────────────────────────────────────────

    async def _get_broker(self, user_id: str):
        """
        Returns the appropriate broker for this user.
        Restores Kotak session from Redis cache.
        """
        from src.broker.mock_broker import MockBroker
        from src.broker.kotak_neo import KotakNeoBroker, KotakAuthError
        from src.utils.crypto import decrypt
        from src.db.mongo import get_db

        if settings.use_mock_broker:
            broker = MockBroker(fail_rate=0.0)
            await broker.authenticate({})
            return broker

        # Get user credentials from MongoDB
        db = await get_db()
        user = await db.users.find_one({"_id": ObjectId(user_id)})

        if not user or not user.get("encryptedCredentials"):
            raise Exception(f"No credentials found for user {user_id}")

        credentials = json.loads(decrypt(user["encryptedCredentials"]))

        broker = KotakNeoBroker(user_id=user_id)
        authenticated = await broker.authenticate(credentials)

        if not authenticated:
            raise KotakAuthError(
                f"No active Kotak session for user {user_id}. "
                "User must complete TOTP → MPIN login."
            )

        return broker
