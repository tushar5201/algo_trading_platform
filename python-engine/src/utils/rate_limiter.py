import asyncio
import time
from typing import Optional
import uuid
import structlog

log = structlog.get_logger()

_ACQUIRE_LUA = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local max    = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count < max then
    redis.call('ZADD', key, now, member)
    redis.call('EXPIRE', key, math.ceil(window) + 1)
    return {1, '0'}
end
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local wait = tonumber(oldest[2]) + window - now
return {0, tostring(wait)}
"""

class SlidingWindowLimiter:
    def __init__(self, redis, key: str, max_calls: int, window_sec: float, name: str = ""):
        self.redis = redis
        self.key = key
        self.max_calls = max_calls
        self.window_sec = window_sec
        self.name = name or key

    async def acquire(self, timeout_sec: float = 5.0) -> None:
        deadline = time.time() + timeout_sec
        attempts = 0
        while True:
            allowed, wait_for = await self._check_and_record()
            if allowed:
                if attempts > 0:
                    log.debug(
                        "rate_limiter.acquired_after_wait",
                        limiter=self.name,
                        attempts=attempts,
                    )
                return

            if time.time() + wait_for > deadline:
                raise TimeoutError(
                    f"Rate limiter '{self.name}' timeout: "
                    f"could not acquire slot within {timeout_sec}s. "
                    f"Current rate: {self.max_calls} per {self.window_sec}s"
                )

            if attempts == 0:
                log.warning(
                    "rate_limiter.waiting",
                    limiter=self.name,
                    wait_ms=round(wait_for * 1000),
                    max_calls=self.max_calls,
                    window_sec=self.window_sec,
                )
            await asyncio.sleep(wait_for)
            attempts += 1

    async def _check_and_record(self) -> tuple[bool, float]:
        now = time.time()
        member = f"{now:.6f}-{uuid.uuid4().hex[:8]}"
        allowed, wait_raw = await self.redis.eval(
            _ACQUIRE_LUA, 1, self.key, now, self.window_sec, self.max_calls, member
        )
        if int(allowed) == 1:
            return True, 0.0
        wait_for = max(0.01, float(wait_raw))
        log.debug("rate-limiter.throttled", limiter=self.name, max=self.max_calls, wait_ms=round(wait_for * 1000))
        return False, wait_for

    async def get_current_rate(self) -> int:
        now = time.time()
        await self.redis.zremrangebyscore(self.key, 0, now - self.window_sec)
        return await self.redis.zcard(self.key)

class KotakRateLimiter:
    def __init__(self):
        self._quotes: Optional[SlidingWindowLimiter] = None
        self._orders: Optional[SlidingWindowLimiter] = None
        self._auth: Optional[SlidingWindowLimiter] = None

    def init(self, redis) -> None:
        self._quotes = SlidingWindowLimiter(redis, "rl:kotak:quotes", 8, 1.0, "kotak_quotes")
        self._orders = SlidingWindowLimiter(redis, "rl:kotak:orders", 1, 1.0, "kotak_orders")
        self._auth = SlidingWindowLimiter(redis, "rl:kotak:auth", 5, 60.0, "kotak_auth")
        log.info("kotak_rate_limiter.initialized", quotes="8/sec", orders="1/sec", auth="5/min")

    async def acquire_quotes(self) -> None:
        self._assert_init()
        await self._quotes.acquire(timeout_sec=5.0)

    async def acquire_orders(self) -> None:
        self._assert_init()
        await self._orders.acquire(timeout_sec=10.0)

    async def acquire_auth(self) -> None:
        self._assert_init()
        await self._auth.acquire(timeout_sec=10.0)

    async def get_rates(self) -> dict:
        if not (self._quotes and self._orders and self._auth and self._auth):
            return {"status": "not_initialized"}
        return {
            "status": "active",
            "quotes": await self._quotes.get_current_rate(),
            "orders": await self._orders.get_current_rate(),
            "limits": {
                "quotes": f"{self._quotes.max_calls}/{self._quotes.window_sec:g}s",
                "orders": f"{self._orders.max_calls}/{self._orders.window_sec:g}s",
                "auth": f"{self._auth.max_calls}/{self._auth.window_sec:g}s",
            }
        }

    def _assert_init(self) -> None:
        if not (self._quotes and self._orders and self._auth):
            raise RuntimeError(
                "KotakRateLimiter was not initialized properly. call .init(redis) first"
            )


kotak_limiter = KotakRateLimiter()
