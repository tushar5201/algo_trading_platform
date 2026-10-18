import asyncio
import json
from datetime import datetime, timezone

import structlog
from aiobreaker import CircuitBreakerError
from bson import ObjectId
from bullmq import Worker, Job

from src.broker.factory import create_broker
from src.execution.validator import halted_reason
from src.broker.circuit_breaker import is_any_breaker_open
from src.db.redis_client import get_redis
from src.broker.kotak_neo import KotakAuthError
from src.db.mongo import get_db
from src.execution.order_manager import OrderManager
from src.strategies.base import StrategyContext
from src.strategies.registry import get_strategy
from src.utils.crypto import decrypt
from src.utils.market_data import init_market_data, _fetch_live_snapshot
from src.config.settings import settings
from src.utils.market_data import has_market_data, bind_market_data_user
from src.ai.jobs import enqueue_review

log = structlog.get_logger()

STRATEGY_QUEUE = "strategy-runner"

STRATEGY_CONCURRENCY = 5

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
        self._stop_event= asyncio.Event()

    async def start(self) -> None:
        self._strategy_worker = Worker(
            STRATEGY_QUEUE,
            self._handle_strategy_job,
            {"connection": _redis_opts(), "concurrency": STRATEGY_CONCURRENCY},
        )

        log.info("worker.listening", strategy_queue=STRATEGY_QUEUE, concurrency=STRATEGY_CONCURRENCY)
        await self._stop_event.wait()
        await self._close_worker()

    async def stop(self) -> None:
        log.info("worker.stopping")
        self._stop_event.set()

    async def _close_worker(self) -> None:
        if self._strategy_worker:
            try:
                await self._strategy_worker.close()
            finally:
                self._strategy_worker = None

    # ------------------------------------------------------------------ #
    #  Job handlers — called by bullmq Worker with a Job instance         #
    # ------------------------------------------------------------------ #

    async def _handle_strategy_job(self, job: Job, job_token: str) -> dict:
        data: dict = job.data

        user_id = data["userId"]
        user_email = data["userEmail"]
        strategy_name = data["strategyName"]

        log.info("strategy_job.start", job_id=job.id, user_id=user_id, strategy=strategy_name)

        halted = await halted_reason(user_id)
        if halted:
            log.warning("strategy_job.halted", user_id=user_id, reason=halted)
            return {"status": "hold", "reason": halted}

        auto_trading = await self._current_auto_trading(user_id, data.get("autoTrading", False))
        # an admin's manual trigger runs now, regardless of the strategy's schedule
        force = data.get("triggeredBy") == "ADMIN"

        try:
            credentials = json.loads(decrypt(data["encryptedCredentials"]))
        except Exception as e:
            raise Exception(f"Failed to decrypt credentials: {e}")

        strategy = get_strategy(
            strategy_name,
            StrategyContext(
                user_id=user_id,
                user_email=user_email,
                strategy_name=strategy_name,
                auto_trading=auto_trading,
                credentials=credentials,
                force=force,
            )
        )

        if not force and not strategy.is_scheduled_to_run():
            log.debug("strategy_job.not_scheduled", user_id=user_id, strategy=strategy_name)
            return {"status": "hold", "reason": "Not scheduled to run at this time"}

        if not settings.use_mock_broker:
            await self._ensure_market_data(user_id, credentials)
            bind_market_data_user(user_id)

        if is_any_breaker_open():
            log.warning("strategy_job.circuit_open_skip", user_id=user_id, strategy_name=strategy_name)
            return {"status": "hold", "reason": "Kotak circuit open - waiting for recovery"}

        try:
            snapshots = {}
            for symbol in strategy.symbols():
                snapshots[symbol] = await _fetch_live_snapshot(symbol)
            signals = await strategy.run(snapshots)
        except (CircuitBreakerError, RuntimeError) as e:
            log.warning(
                "strategy_job.market_data_unavailable",
                user_id=user_id,
                strategy=strategy_name,
                error=str(e)
            )
            return {"status": "hold", "reason": f"Market data unavailable: {e}"}

        actionable = [s for s in signals if s.is_actionable()]

        if not actionable:
            reason = signals[0].reason if signals else "No Signal produced"
            log.info("strategy_job.hold", reason=reason, user_id=user_id)
            return {"status": "hold", "reason": reason}

        for signal in actionable:
            log.info("strategy_job.signal", action=signal.action, symbol=signal.symbol, confidence=signal.confidence,
                     reason=signal.reason, user_id=user_id)

        if not auto_trading:
            for signal in actionable:
                await self._log_signal_to_db(signal, user_id)
            return {
                "status": "signal_only",
                "signals": [{"symbol": s.symbol, "action": str(s.action)} for s in actionable]
            }

        # Auto trading ON — execute
        try:
            broker = await create_broker(user_id, credentials)
        except KotakAuthError as e:
            log.error("strategy_job.auth_failed", user_id=user_id, error=str(e))
            await self._notify_auth_required(user_id)
            raise  # re-raise so bullmq marks the job failed and retries

        try:
            result = await self._execute_signals(actionable, user_id, broker, credentials)
        finally:
            await broker.close()
        return {"status": "executed", "result": result}

    async def _execute_signals(self, actionable, user_id: str, broker, credentials: dict) -> list:
        results = []
        for signal in actionable:
            result = await self.order_manager.execute(
                signal=signal, user_id=user_id,
                broker=broker, credentials=credentials
            )
            log.info("strategy_job.complete", result=result, user_id=user_id, strategy=signal.strategy_name)
            results.append(result)
            if result.get("stage") == "auth":
                # Kotak rejected the session: every remaining order would fail the same way and each
                # would leave another FAILED row behind. Stop, and tell the user to log in again.
                await self._notify_auth_required(user_id)
                break
            if result.get("stage") == "ip_blocked":
                log.error("strategy_job.ip_blocked", user_id=user_id, reason=result.get("reason"))
                break
        return results

    async def _current_auto_trading(self, user_id:str, fallback:bool) -> bool:
        try:
            db = await get_db()
            user = await db.users.find_one({"_id": ObjectId(user_id)}, {"autoTrading": 1})
            return bool(user.get("autoTrading", False)) if user else False
        except Exception as e:
            log.warning("worker.auto_trading_lookup_failed", error=str(e))
            return bool(fallback)

    async def _ensure_market_data(self, user_id: str, credentials: dict) -> None:
        """Every user gets their own quotes client, built from THEIR session. (It used to be a
        single global client created from whichever user logged in first.)"""
        if has_market_data(user_id):
            return
        session_raw = await get_redis().get(f"kotak:session:{user_id}")
        if not session_raw:
            raise KotakAuthError("No kotak session found. complete TOTP -> MPIN login first.")
        session_data = json.loads(session_raw)
        init_market_data(
            access_token=credentials.get("apiKey", ""),
            base_url=session_data.get("base_url", ""),
            user_id=user_id,
        )
        log.info("worker.market_data_restored", user_id=user_id)

    async def _log_signal_to_db(self, signal, user_id: str) -> None:
        db = await get_db()
        inserted = await db.signals.insert_one({
            "userId": ObjectId(user_id),
            "strategyName": signal.strategy_name,
            "symbol": signal.symbol,
            "action": signal.action.value,
            "confidence": signal.confidence,
            "reason": signal.reason,
            "stopLoss": signal.stop_loss,
            "takeProfit": signal.take_profit,
            "metadata": signal.metadata,
            "autoTraded": False,
            "createdAt": datetime.now(timezone.utc)
        })
        log.info("signal.saved_only", user_id=user_id, symbol=signal.symbol, action=signal.action)
        try:
            await enqueue_review("signal", str(inserted.inserted_id))
        except Exception as e:
            log.warning("ai.enqueue_skipped", error=str(e))

    async def _notify_auth_required(self, user_id: str) -> None:
        redis = get_redis()
        await redis.setex(
            f"kotak:auth:error:{user_id}",
            3600,
            "Kotak session expired. Please re-authenticate via dashboard",
        )

