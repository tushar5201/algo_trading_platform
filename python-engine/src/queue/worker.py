import asyncio
import json
import structlog
from redis import Redis

from src.broker.base_broker import BaseBroker
from src.broker.kotak_neo import KotakNeoBroker, KotakAuthError
from src.config.settings import settings
from src.broker.mock_broker import MockBroker
from src.db.mongo import get_db
from src.execution.order_manager import execute_signal
from src.strategies.base import StrategyContext
from src.strategies.registry import get_strategy
from src.utils.crypto import decrypt
from src.utils.market_data import get_market_snapshot

log = structlog.get_logger()

# BullMQ redis key patterns
STRATEGY_QUEUE = "bull:strategy-runner:wait"
TRADE_QUEUE = "bull:trade-executor:wait"


class StrategyWorker:
    """
    Polls redis for bullmq jobs & processes them.
    Compatible with Bullmq job format without the Nodejs bullmq library.
    """

    def __init__(self, redis: Redis):
        self.redis = redis
        self.running = True

    async def start(self) -> None:
        log.info("worker.started", queues=["strategy-runner", "trade-executor"])
        await asyncio.gather(
            self._poll_queue("strategy-runner", self._handle_strategy_job),
            self._poll_queue("trade-executor", self._handle_trade_job),
        )

    async def _poll_queue(self, queue: str, handler) -> None:
        """
        Polls a BullMQ queue using BRPOPLPUSH pattern.
        Moves job form 'wait' list -> 'active' list atomically
        """
        wait_key = f"bull:{queue}:wait"
        active_key = f"bull:{queue}:active"

        log.info("worket.polling", queue=queue)

        while self.running:
            try:
                # Block until a job arrives(timeout 5s -> loop again)
                job_id = await self.redis.brpoplpush(wait_key, active_key, timeout=5)
                if job_id is None:
                    continue

                # fetch job data
                job_key = f"bull:{queue}:{job_id}"
                job_data = await self.redis.hgetall(job_key)

                if not job_data:
                    log.warning("worker.job_missing", queue=queue, job_id=job_id)
                    await self.redis.lrem(active_key, 1, job_id)
                    continue

                data = json.loads(job_data.get("data", "{}"))
                log.info("worker.job_received", job_id=job_id, queue=queue)
                try:
                    await handler(data, job_id)
                    # Move to completed
                    await self._complete_job(queue, job_id, job_key, active_key)
                except Exception as e:
                    log.error("worker.job_failed", job_id=job_id, error=str(e), exc_info=True)
                    await self._fail_job(queue, job_id, job_key, active_key, str(e))
            except Exception as e:
                log.error("worker.poll_error", queue=queue, error=str(e))
                await asyncio.sleep(2)

    async def _handle_strategy_job(self, data: dict, job_id: str) -> None:
        # Process a STRATEGY_RUN job
        user_id = data["userId"]
        user_email = data["userEmail"]
        strategy_name = data["strategyName"]
        auto_trading = data.get("autoTrading", False)
        encrypted_creds = data["encryptedCredentials"]

        log.info(
            "strategy_job.start",
            user_id=user_id,
            strategy=strategy_name,
            auto_trading=auto_trading,
        )

        # Decrypt broker credentials
        try:
            credentials = json.loads(decrypt(encrypted_creds))
        except Exception as e:
            raise Exception(f"Failed to decrypt credentials: {e}")

        # Build strategy context
        context = StrategyContext(
            user_id=user_id,
            user_email=user_email,
            strategy_name=strategy_name,
            auto_trading=auto_trading,
            credentials=credentials,
        )

        # Get strategy instance from registry
        strategy = get_strategy(strategy_name, context)

        # Fetch market data for all symbols this strategy needs
        snapshots = {}
        for symbol in strategy.symbols():
            snapshots[symbol] = await get_market_snapshot(symbol)

        # Run strategy analysis -> get signal
        signal = await strategy.run(snapshots)
        if not signal.is_actionable():
            log.info("strategy_job.hold", reason=signal.reason, user_id=user_id)
            return
        if not auto_trading:
            log.info("strategy_job.signal_only", reason="Auto trading OFF", action=signal.action, symbol=signal.symbol)
            await self._log_signal_to_db(signal, user_id)
            return

        # Auto trading ON -> execute the trade
        # broker = MockBroker()
        # await broker.authenticate(credentials)
        broker = await self._get_broker(user_id, credentials)

        result = await execute_signal(signal, user_id, broker)
        log.info("strategy_job.complete", result=result, user_id=user_id)

    async def _handle_trade_job(self, data: dict, job_id: str) -> None:
        """
        Process a TRADE_EXECUTE job (future use for deferred trades).
        """
        log.info("trade_job.received", data=data)

    async def _log_signal_to_db(self, signal, user_id: str) -> None:
        """Log signal-only(no trade) to DB for dashboard display."""
        db = await get_db()
        from bson import ObjectId
        from datetime import datetime, timezone
        await db.signals.insert_one({
            "user_id": user_id,
            "strategyName": signal.strategy_name,
            "symbol": signal.symbol,
            "action": signal.action.value,
            "confidence": signal.confidence,
            "reason": signal.reason,
            "metadata": signal.metadata,
            "autoTraded": False,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })

    async def _complete_job(self, queue: str, job_id: str, job_key: str, active_key: str) -> None:
        completed_key = f"bull:{queue}:completed"
        async with self.redis.pipeline() as pipe:
            pipe.lrem(active_key, 1, job_id)
            pipe.lpush(completed_key, job_key)
            pipe.hset(job_key, "returnvalue", json.dumps({"success": True}))
            await pipe.execute()

    async def _fail_job(self, queue: str, job_id: str, job_key: str, active_key: str, reason: str) -> None:
        failed_key = f"bull:{queue}:failed"
        async with self.redis.pipeline() as pipe:
            pipe.lrem(active_key, 1, job_id)
            pipe.lpush(failed_key, job_key)
            pipe.hset(job_key, "failedReason", reason)
            await pipe.execute()

    def stop(self) -> None:
        self.running = False
        log.info("worker.stopping")

    async def _get_broker(self, user_id: str, credentials: dict) -> BaseBroker:
        """
        Returns KotakNeoBroker in production, MockBroker in development.
        KotakNeoBroker restores session from Redis cache - no OTP needed
        if session is still valid.
        """
        if settings.node_env == "development":
            broker = MockBroker()
            await broker.authenticate(credentials)
            return broker

        broker = KotakNeoBroker(user_id)
        try:
            authenticated = await broker.authenticate(credentials)
            if not authenticated:
                raise KotakAuthError("Authentication returned False")
            return broker
        except KotakAuthError as e:
            log.error("worker.broker_auth_failed", user_id=user_id, error=str(e))
            raise Exception(
                "Broker sessoin expired or not initialized."
                "User must submit OTP via dashboard."
            )
