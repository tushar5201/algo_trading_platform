import structlog
from datetime import datetime, timezone

from src.broker.circuit_breaker import is_any_breaker_open, get_all_breaker_states
from src.db.mongo import get_db
from src.db.redis_client import get_redis
from src.utils.rate_limiter import kotak_limiter

log = structlog.get_logger()


async def _queue_counts(redis, name: str) -> dict:
    prefix = f"bull:{name}"
    return {
        "waiting": await redis.llen(f"{prefix}:wait"),
        "active": await redis.llen(f"{prefix}:active"),
        "delayed": await redis.zcard(f"{prefix}:delayed"),
        "failed": await redis.zcard(f"{prefix}:failed"),
    }


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
        health["status"] = "unhealthy"
        log.error("health.mongodb_down", error=str(e))

    # ── Redis ─────────────────────────────────────────────────────────────
    try:
        redis = get_redis()
        await redis.ping()
        health["checks"]["redis"] = {"status": "up"}
    except Exception as e:
        health["checks"]["redis"] = {"status": "down", "error": str(e)}
        health["status"] = "unhealthy"
        log.error("health.redis_down", error=str(e))

    # ── Queue depths ──────────────────────────────────────────────────────
    try:
        redis = get_redis()
        strategy = await _queue_counts(redis, "strategy-runner")
        queue_health = {"status": "up", "strategy_runner": strategy}
        if strategy["failed"] > 5:
            queue_health["warning"] = f"High failure count:strategy={strategy['failed']}"
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

    # ── Outbound rate limiter ──────────────────────────────────────────────────
    try:
        health["checks"]["outbound_rate_limiter"] = await kotak_limiter.get_rates()
    except Exception as e:
        health["checks"]["outbound_rate_limiter"] = {"status": "error", "error": str(e)}

    # ── Circuit breakers ──────────────────────────────────────────────────
    try:
        breaker_states = get_all_breaker_states()
        any_open = is_any_breaker_open()

        health["checks"]["circuit_breakers"] = {
            "status": "degraded" if any_open else "up",
            "breakers": breaker_states,
        }

        if any_open:
            if health["status"] == "healthy":
                health["status"] = "degraded"
            log.error(
                "health.circuit_breaker_open",
                open_breakers=[n for n, i in breaker_states.items() if i["state"] == "open"]
            )
    except Exception as e:
        health["checks"]["circuit_breakers"] = {"status": "error", "error": str(e)}

    return health
