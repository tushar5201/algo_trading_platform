import asyncio
import json
import structlog
from aiobreaker import CircuitBreakerError
from bson import ObjectId
from bullmq import Worker, Job

from src.broker.circuit_breaker import is_any_breaker_open
from src.db.redis_client import get_redis
from src.broker.kotak_neo import KotakNeoBroker, KotakAuthError
from src.db.mongo import get_db
from src.execution.order_manager import OrderManager
from src.strategies.base import StrategyContext
from src.strategies.registry import get_strategy
from src.utils.crypto import decrypt
from src.utils.market_data import get_market_data, init_market_data, _fetch_live_snapshot
from src.config.settings import settings

log = structlog.get_logger()

STRATEGY_QUEUE = "strategy-runner"
TRADE_QUEUE    = "trade-executor"

# Redis connection opts forwarded to bullmq Python lib (ioredis-compatible dict)
def _redis_opts() -> dict:
    return {
        "host": settings.redis_host,
        "port": settings.redis_port,
    }


class StrategyWorker:
    """
    BullMQ-native Python worker — fully compatible with Node BullMQ v5.
    Uses the official `bullmq` Python package so all Redis key formats
    (Sorted Sets, Lua scripts, job hashes) are handled automatically.
    """

    def __init__(self):
        self.order_manager = OrderManager()
        self._strategy_worker: Worker | None = None
        self._trade_worker: Worker | None = None

    async def start(self) -> None:
        log.info("worker.started", queues=[STRATEGY_QUEUE, TRADE_QUEUE])
        connection = _redis_opts()
        self._strategy_worker = Worker(
            STRATEGY_QUEUE,
            self._handle_strategy_job,
            {"connection": connection},
        )
        self._trade_worker = Worker(
            TRADE_QUEUE,
            self._handle_trade_job,
            {"connection": connection},
        )

        log.info("worker.listening", strategy_queue=STRATEGY_QUEUE, trade_queue=TRADE_QUEUE)

        # Keep alive — bullmq Workers run their own asyncio tasks internally
        try:
            while True:
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            await self.stop()

    async def stop(self) -> None:
        log.info("worker.stopping")
        if self._strategy_worker:
            await self._strategy_worker.close()
        if self._trade_worker:
            await self._trade_worker.close()

    # ------------------------------------------------------------------ #
    #  Job handlers — called by bullmq Worker with a Job instance         #
    # ------------------------------------------------------------------ #

    async def _handle_strategy_job(self, job: Job, job_token: str) -> dict:
        data: dict = job.data

        user_id       = data["userId"]
        user_email    = data["userEmail"]
        strategy_name = data["strategyName"]
        auto_trading  = data.get("autoTrading", False)
        encrypted_creds = data["encryptedCredentials"]

        log.info("strategy_job.start", job_id=job.id, user_id=user_id, strategy=strategy_name)

        # Decrypt broker credentials
        try:
            credentials = json.loads(decrypt(encrypted_creds))
        except Exception as e:
            raise Exception(f"Failed to decrypt credentials: {e}")

        # Ensure market data client is initialised
        if get_market_data() is None:
            redis = get_redis()
            session_raw = await redis.get(f"kotak:session:{user_id}")
            if session_raw:
                session_data = json.loads(session_raw)
                init_market_data(
                    access_token=credentials.get("apiKey", ""),
                    base_url=session_data.get("base_url", ""),
                )
                log.info("worker.market_data_restored", user_id=user_id)
            else:
                raise KotakAuthError(
                    "No kotak session found. complete TOTP -> MPIN login first."
                )

        # Build and run strategy
        context = StrategyContext(
            user_id=user_id,
            user_email=user_email,
            strategy_name=strategy_name,
            auto_trading=auto_trading,
            credentials=credentials,
        )
        strategy = get_strategy(strategy_name, context)

        snapshots = {}
        for symbol in strategy.symbols():
            snapshots[symbol] = await _fetch_live_snapshot(symbol)

        if is_any_breaker_open():
            log.warning(
                "strategy_job.circuit_open_skip",
                user_id=user_id,
                strategy=strategy_name,
                note="Kotak API circuit open-holding strategy until recovery"
            )
            return {"status": "hold", "reason": "Kotak circuit open - waiting for recovery"}

        try:
            signal = await strategy.run(snapshots)
        except CircuitBreakerError:
            log.warning(
                "strategy_job.circuit_tripped",
                user_id=user_id,
                strategy=strategy_name,
                note="Circuit breaker opened mid-execution — Kotak API went down",
            )
            return {"status": "hold", "reason": "Kotak circuit tripped during strategy run"}

        if not signal.is_actionable():
            log.info("strategy_job.hold", reason=signal.reason, user_id=user_id)
            return {"status": "hold", "reason": signal.reason}

        log.info("strategy_job.signal",
                 action=signal.action, symbol=signal.symbol,
                 confidence=signal.confidence, reason=signal.reason,
                 user_id=user_id)

        if not auto_trading:
            await self._log_signal_to_db(signal, user_id)
            return {"status": "signal_only", "symbol": signal.symbol, "action": str(signal.action)}

        # Auto trading ON — execute
        try:
            broker = await self._get_broker(user_id, credentials)
        except KotakAuthError as e:
            log.error("strategy_job.auth_failed", user_id=user_id, error=str(e))
            await self._notify_auth_required(user_id)
            raise  # re-raise so bullmq marks the job failed and retries

        result = await self.order_manager.execute(
            signal=signal, user_id=user_id,
            broker=broker, credentials=credentials,
        )
        log.info("strategy_job.complete", result=result, user_id=user_id, strategy=signal.strategy_name)
        return {"status": "executed", "result": result}

    async def _handle_trade_job(self, job: Job, job_token: str) -> dict:
        data: dict = job.data

        user_id         = data["userId"]
        trade_id        = data.get("tradeId")
        encrypted_creds = data["encryptedCredentials"]

        log.info("trade_job.start", job_id=job.id, user_id=user_id, trade_id=trade_id)

        db    = await get_db()
        trade = await db.trades.find_one({"_id": ObjectId(trade_id)})

        if not trade:
            raise Exception(f"Trade {trade_id} not found in DB")

        if trade["status"] != "PENDING":
            log.info("trade_job.skip", trade_id=trade_id, status=trade["status"])
            return {"status": "skipped", "reason": trade["status"]}

        try:
            credentials = json.loads(decrypt(encrypted_creds))
            broker      = await self._get_broker(user_id, credentials)
        except Exception as e:
            log.error("trade_job.broker_error", error=str(e))
            raise

        order_result = await broker.place_order(
            symbol=trade["symbol"],
            side=trade["side"],
            qty=trade["qty"],
            price=trade["price"],
            order_type="MARKET",
            product_type="MIS",
            exchange=trade.get("exchange", "nse_fo"),
        )

        await db.trades.update_one(
            {"_id": trade["_id"]},
            {"$set": {"orderId": order_result.get("orderId"), "status": "PLACED"}},
        )
        log.info("trade_job.order_placed", trade_id=trade_id, order=order_result)
        return {"status": "placed", "orderId": order_result.get("orderId")}

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    async def _log_signal_to_db(self, signal, user_id: str) -> None:
        from datetime import datetime, timezone
        db = await get_db()
        await db.signals.insert_one({
            "user_id":      ObjectId(user_id),
            "strategyName": signal.strategy_name,
            "symbol":       signal.symbol,
            "action":       signal.action.value,
            "confidence":   signal.confidence,
            "reason":       signal.reason,
            "stopLoss":     signal.stop_loss,
            "takeProfit":   signal.take_profit,
            "metadata":     signal.metadata,
            "autoTraded":   False,
            "createdAt":    datetime.now(timezone.utc).isoformat(),
        })
        log.info("signal.saved_only", user_id=user_id, symbol=signal.symbol, action=signal.action)

    async def _notify_auth_required(self, user_id: str) -> None:
        redis = get_redis()
        await redis.setex(
            f"kotak:auth:error:{user_id}",
            3600,
            "Kotak session expired. Please re-authenticate via dashboard",
        )

    async def _get_broker(self, user_id: str, credentials: dict) -> KotakNeoBroker:
        broker = KotakNeoBroker(user_id=user_id)
        authenticated = await broker.authenticate(credentials)
        
        if not authenticated:
            raise KotakAuthError(
                "No active Kotak session. User must complete TOTP → MPIN login."
            )

        log.info("worker.using_kotak_broker", user_id=user_id)
        return broker