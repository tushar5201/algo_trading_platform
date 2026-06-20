import structlog
import redis.asyncio as aioredis

from src.config.settings import settings
from src.utils.rate_limiter import kotak_limiter

log = structlog.getLogger()
_redis: aioredis.Redis | None = None

async def connect_redis() -> None:
    global _redis
    _redis = aioredis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        decode_responses=True
    )
    await _redis.ping()
    kotak_limiter.init(_redis)
    log.info("Connected to Redis", host=settings.redis_host)

async def disconnect_redis() -> None:
    global _redis
    if _redis:
        await _redis.aclose()

def get_redis() -> aioredis.Redis:
    if _redis is None:
        raise RuntimeError("Redis not connected")
    return _redis
