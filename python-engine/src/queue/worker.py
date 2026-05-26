import asyncio
import json
import structlog
from bson import ObjectId
from pymongo.auth import authenticate
from redis import Redis

from src.broker.base_broker import BaseBroker
from src.broker.kotak_neo import KotakNeoBroker, KotakAuthError
from src.config.settings import settings
from src.broker.mock_broker import MockBroker
from src.db.mongo import get_db
from src.execution.order_manager import OrderManager
from src.strategies.base import StrategyContext
from src.strategies.registry import get_strategy
from src.utils.crypto import decrypt
from src.utils.market_data import get_market_snapshot

log = structlog.get_logger()

# BullMQ redis key patterns
STRATEGY_QUEUE = "strategy-runner"
TRADE_QUEUE = "trade-executor"


class StrategyWorker:
    """
    Polls redis for bullmq jobs & processes them.
    Compatible with Bullmq job format without the Nodejs bullmq library.
    """

    def __init__(self, redis: Redis):
        self.redis = redis
        self.running = True
        self.order_manager = OrderManager()

    async def start(self) -> None:
        log.info("worker.started", queues=[STRATEGY_QUEUE, TRADE_QUEUE])
        await asyncio.gather(
            self._poll_queue(STRATEGY_QUEUE, self._handle_strategy_job),
            self._poll_queue(TRADE_QUEUE, self._handle_trade_job),
        )

    async def _poll_queue(self, queue: str, handler) -> None:
        """
        Polls a BullMQ queue using BRPOPLPUSH pattern.
        Moves job form 'wait' list -> 'active' list atomically
        """
        wait_key = f"bull:{queue}:wait"
        active_key = f"bull:{queue}:active"

        log.info("worker.polling", queue=queue)

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
                    log.warning("worker.job_missing", job_id=job_id)
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
        log.info("strategy_job.signal", action=signal.action, symbol=signal.symbol, confidence=signal.confidence, reason=signal.reason, user_id=user_id)
        if not auto_trading:
            await self._log_signal_to_db(signal, user_id)
            return

        # Auto trading ON -> execute the trade
        # broker = MockBroker()
        # await broker.authenticate(credentials)
        try:
            broker = await self._get_broker(user_id, credentials)
        except KotakAuthError as e:
            log.error("strategy_job.auth_failed", user_id=user_id, error=str(e))
            await self._notify_auth_required(user_id)
            return

        result = await self.order_manager.execute(signal=signal, user_id=user_id, broker=broker, credentials=credentials)
        log.info("strategy_job.complete", result=result, user_id=user_id, strategy=signal.strategy_name)

    async def _handle_trade_job(self, data: dict, job_id: str) -> None:
        """
        Process a TRADE_EXECUTE job (future use for deferred trades).
        """
        user_id = data["userId"]
        trade_id = data.get("tradeId")
        encrypted_creds = data["encryptedCredentials"]
        log.info("trade_job.start", user_id=user_id, trade_id=trade_id)
        db = get_db()
        trade = await db.trades.find_one({"_id": ObjectId(trade_id)})

        if not trade:
            log.error("trade_job.not_found", trade_id=trade_id)
            return
        if trade["status"] != "PENDING":
            log.info("trade_job.skip", trade_id=trade_id, status=trade["status"])
            return
        try:
            credentials = json.loads(decrypt(encrypted_creds))
            broker = await self._get_broker(user_id, credentials)
        except Exception as e:
            log.error(f"trade_job.broker_error", error=str(e))
            return

        try:
            order_result = await broker.place_order(
                symbol=trade["symbol"],
                side=trade["side"],
                qty=trade["qty"],
                price=trade["price"],
                order_type="MARKET",
                product_type="MIS",
                exchange=trade.get("exchange", "nse_fo")
            )
            await db.trades.update_one(
                {"_id": trade["_id"]},
                {"$set": {"orderId": order_result.get("orderId"), "status": "PENDING"}},
            )
            log.info("trade_job.order_placed", trade_id=trade_id)
        except Exception as e:
            log.error(f"trade_job.place_failed", trade_id=trade_id, error=str(e))

    async def _log_signal_to_db(self, signal, user_id: str) -> None:
        """Log signal-only(no trade) to DB for dashboard display."""
        db = get_db()
        from bson import ObjectId
        from datetime import datetime, timezone
        await db.signals.insert_one({
            "user_id": ObjectId(user_id),
            "strategyName": signal.strategy_name,
            "symbol": signal.symbol,
            "action": signal.action.value,
            "confidence": signal.confidence,
            "reason": signal.reason,
            "stopLoss": signal.stopLoss,
            "takeProfit": signal.takeProfit,
            "metadata": signal.metadata,
            "autoTraded": False,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })
        log.info("signal.saved_only", user_id=user_id, symbol=signal.symbol, action=signal.action)

    async def _notify_auth_required(self, user_id: str) -> None:
        await self.redis.setex(
            f"kotak:auth:error:{user_id}",
            3600,
            "Kotak session expired. Please re-authenticate via dashboard"
        )

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
        if settings.use_mock_broker:
            broker = MockBroker(fail_rate=0.0)  # 0% failure in dev
            await broker.authenticate(credentials)
            log.info("worker.using_mock_broker", user_id=user_id)
            return broker

        broker = KotakNeoBroker(user_id=user_id)
        authenticated = await broker.authenticate(credentials)

        if not authenticated:
            raise KotakAuthError(
                "No active Kotak session. User must complete TOTP → MPIN login."
            )

        log.info("worker.using_kotak_broker", user_id=user_id)
        return broker
