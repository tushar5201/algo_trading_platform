
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from bson import ObjectId
from bullmq import Job, Queue, Worker

from src.ai.groq_client import GroqClient
from src.ai.limits import CallGate
from src.ai.schemas import DONE, FAILED, PENDING, ReviewRequest
from src.ai.trade_analyst import TradeAnalyst
from src.config.settings import settings
from src.db.audit import write_audit
from src.db.mongo import get_db
from src.db.redis_client import get_redis

log = structlog.get_logger()

AI_QUEUE = "ai-analysis"
PENDING_STALE_AFTER = timedelta(minutes=15)   # same rule as the Node API
_COLLECTION = {"trade": "trades", "signal": "signals"}
_queue: Optional[Queue] = None


def _redis_opts() -> dict:
    return {"host": settings.redis_host, "port": settings.redis_port}


def _get_queue() -> Queue:
    global _queue
    if _queue is None:
        _queue = Queue(AI_QUEUE, {"connection": _redis_opts()})
    return _queue


def review_job_id(doc_id: str, attempt: int = 0) -> str:
    return f"review-{doc_id}" if attempt == 0 else f"review-{doc_id}-r{attempt}"


async def enqueue_review(kind: str, doc_id: str, *, attempt: int = 0, delay_ms: int = 0) -> bool:
    """Queue a review. True if a job was queued. Never raises: AI problems must not touch trading."""
    if not settings.ai_enabled:
        return False
    try:
        req = ReviewRequest(kind=kind, id=str(doc_id), attempt=attempt)
        coll, oid = (await get_db())[_COLLECTION[kind]], ObjectId(req.id)
        if attempt == 0:   # first enqueue: skip if already done / already waiting, else mark PENDING
            doc = await coll.find_one({"_id": oid})
            if not doc:
                return False
            old = doc.get("aiReview") or {}
            created = old.get("createdAt")
            if isinstance(created, datetime) and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            fresh = isinstance(created, datetime) and datetime.now(timezone.utc) - created < PENDING_STALE_AFTER
            if old.get("status") == DONE or (old.get("status") == PENDING and fresh):
                return False
            await coll.update_one({"_id": oid}, {"$set": {"aiReview": {
                "status": PENDING, "createdAt": datetime.now(timezone.utc), "bullets": [], "riskNotes": [],
                "tokensIn": 0, "tokensOut": 0, "error": None}}})
        try:
            await _get_queue().add("review", req.model_dump(), {
                "jobId": review_job_id(req.id, attempt), "attempts": 1, "delay": int(delay_ms),
                "removeOnComplete": {"age": 3600, "count": 500}, "removeOnFail": {"age": 86400, "count": 500}})
        except Exception:
            if attempt == 0:   # don't leave the document stuck in PENDING
                await coll.update_one({"_id": oid, "aiReview.status": PENDING}, {"$unset": {"aiReview": ""}})
            raise
        log.info("ai.enqueued", kind=kind, doc_id=req.id, attempt=attempt, delay_ms=delay_ms)
        return True
    except Exception as e:
        log.warning("ai.enqueue_failed", kind=kind, doc_id=str(doc_id), error=str(e))
        return False

def build_analyst() -> TradeAnalyst:
    client = GroqClient(settings.groq_api_key.get_secret_value(), settings.groq_model,
                        settings.ai_timeout_sec, settings.ai_max_tokens)
    gate = CallGate(get_redis, settings.ai_daily_call_cap, settings.ai_min_seconds_between_calls)
    return TradeAnalyst(client, gate, get_db=get_db, audit=write_audit)


async def process_review_job(data: dict, analyst: TradeAnalyst) -> dict:
    """Handle one job payload (a plain function so tests need no BullMQ)."""
    req = ReviewRequest.model_validate(data)
    outcome = await analyst.review(req)
    if outcome.retry_after is None:
        return {"status": outcome.status.lower(), "error": outcome.error or None}
    if await enqueue_review(req.kind, req.id, attempt=req.attempt + 1, delay_ms=int(outcome.retry_after * 1000)):
        return {"status": "requeued", "retry_after": outcome.retry_after}
    # could not re-queue: end the review instead of leaving it PENDING forever
    await (await get_db())[_COLLECTION[req.kind]].update_one(
        {"_id": ObjectId(req.id), "aiReview.status": PENDING},
        {"$set": {"aiReview.status": FAILED, "aiReview.error": "could not re-queue after rate limit"}})
    return {"status": "failed"}


class AIWorker:

    def __init__(self):
        self._worker: Optional[Worker] = None
        self._analyst: Optional[TradeAnalyst] = None
        self._stop = asyncio.Event()

    async def _handle(self, job: Job, token: str) -> dict:
        return await process_review_job(job.data, self._analyst)

    async def start(self) -> None:
        if not settings.ai_enabled:
            log.info("ai.worker.disabled")
            return
        try:
            self._analyst = build_analyst()
            self._worker = Worker(AI_QUEUE, self._handle, {"connection": _redis_opts(), "concurrency": 1})
            log.info("ai.worker.listening", queue=AI_QUEUE, model=settings.groq_model)
            await self._stop.wait()
        except Exception as e:
            log.error("ai.worker.failed", error=str(e), exc_info=True)
        finally:
            try:
                if self._worker:
                    await self._worker.close()
                if self._analyst:
                    await self._analyst.client.aclose()
            except Exception as e:
                log.warning("ai.worker.close_error", error=str(e))

    def stop(self) -> None:
        self._stop.set()
