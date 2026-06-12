import structlog
from datetime import datetime, timezone

from src.broker.circuit_breaker import is_any_breaker_open, get_all_breaker_states
from src.db.mongo import get_db
from src.db.redis_client import get_redis

log = structlog.get_logger()


async def get_health() -> dict:
    """
    Returns health status of all dependencies.
    Called by the aiohttp health endpoint in main.py.
    """
    health = {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {},
    }

    # ── MongoDB ───────────────────────────────────────────────────────────
    try:
        db = await get_db()
        await db.command("ping")
        health["checks"]["mongodb"] = {"status": "up"}
    except Exception as e:
        health["checks"]["mongodb"] = {"status": "down", "error": str(e)}
        health["status"] = "degraded"
        log.error("health.mongodb_down", error=str(e))

    # ── Redis ─────────────────────────────────────────────────────────────
    try:
        redis = get_redis()
        await redis.ping()
        health["checks"]["redis"] = {"status": "up"}
    except Exception as e:
        health["checks"]["redis"] = {"status": "down", "error": str(e)}
        health["status"] = "degraded"
        log.error("health.redis_down", error=str(e))

    # ── Queue depths ──────────────────────────────────────────────────────
    try:
        redis = get_redis()

        strategy_waiting = await redis.llen("bull:strategy-runner:wait")
        strategy_active = await redis.llen("bull:strategy-runner:active")
        strategy_failed = await redis.llen("bull:strategy-runner:failed")
        trade_waiting = await redis.llen("bull:trade-executor:wait")
        trade_active = await redis.llen("bull:trade-executor:active")
        trade_failed = await redis.llen("bull:trade-executor:failed")

        # Dead letter count from MongoDB
        try:
            db = await get_db()
            dlq_count = await db.dead_letter_jobs.count_documents({"status": "DEAD"})
        except Exception:
            dlq_count = -1

        queue_health = {
            "status": "up",
            "strategy_runner": {
                "waiting": strategy_waiting,
                "active": strategy_active,
                "failed": strategy_failed,
            },
            "trade_executor": {
                "waiting": trade_waiting,
                "active": trade_active,
                "failed": trade_failed,
            },
            "dead_letter": dlq_count,
        }

        # Warn if DLQ is growing
        if dlq_count > 10:
            queue_health["warning"] = (
                f"{dlq_count} jobs in dead letter queue — check /admin/dlq"
            )

        # Warn if failed jobs accumulating
        if strategy_failed > 5 or trade_failed > 5:
            queue_health["warning"] = (
                f"High failure count: strategy={strategy_failed} trade={trade_failed}"
            )

        health["checks"]["queues"] = queue_health

    except Exception as e:
        health["checks"]["queues"] = {"status": "error", "error": str(e)}
        log.error("health.queue_check_failed", error=str(e))

    # ── Market data ───────────────────────────────────────────────────────
    try:
        from src.utils.market_data import get_market_data
        from src.config.settings import settings

        if settings.use_mock_broker:
            health["checks"]["market_data"] = {
                "status": "mock",
                "note": "Using mock data — set USE_MOCK_BROKER=false for live",
            }
        elif get_market_data() is not None:
            health["checks"]["market_data"] = {"status": "live"}
        else:
            health["checks"]["market_data"] = {
                "status": "not_initialized",
                "note": "No active Kotak session — complete TOTP → MPIN auth",
            }
    except Exception as e:
        health["checks"]["market_data"] = {"status": "error", "error": str(e)}

    # ── Active Kotak sessions ─────────────────────────────────────────────
    try:
        redis = get_redis()
        session_keys = [k async for k in redis.scan_iter("kotak:session:*")]
        health["checks"]["kotak_sessions"] = {
            "status": "up",
            "active": len(session_keys),
        }
    except Exception as e:
        health["checks"]["kotak_sessions"] = {"status": "error", "error": str(e)}

    # ── Circuit breakers ──────────────────────────────────────────────────
    try:
        breaker_states = get_all_breaker_states()
        any_open = is_any_breaker_open()

        health["checks"]["circuit_breakers"] = {
            "status": "degraded" if any_open else "up",
            "brekers": breaker_states,
        }

        if any_open:
            health["status"] = "degraded"
            open_names = [
                name for name, info in breaker_states.items()
                if info["state"] == "open"
            ]
            log.error(
                "health.circuit_breaker_open",
                open_breakers=open_names,
            )
    except Exception as e:
            health["checks"]["circuit_breakers"] = {"status": "error", "error": str(e)}

    return health