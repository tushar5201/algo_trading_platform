import asyncio
import time
from typing import Optional

import structlog

log = structlog.get_logger()


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
        window_start = now - self.window_sec
        member = f"{now:.6f}"

        pipe = self.redis.pipeline()
        pipe.zremrangebyscore(self.key, 0, window_start)
        pipe.zcard(self.key)
        results = await pipe.execute()

        current_count = results[1]

        if current_count < self.max_calls:
            member = f"{now:.6f}"
            await self.redis.zadd(self.key, {member: now})
            await self.redis.expire(self.key, int(self.window_sec) + 1)
            return True, 0.0

        oldest_raw = await self.redis.zrange(self.key, 0, 0, withscores=True)
        if oldest_raw:
            oldest_ts = oldest_raw[0][1]
            wait_until = oldest_ts + self.window_sec
            wait_for = max(0.01, wait_until - now)
        else:
            wait_for = 0.1
        log.debug(
            "rate_limiter.throttled",
            limiter=self.name,
            current=current_count,
            max=self.max_calls,
            wait_ms=round(wait_for * 1000),
        )
        return False, wait_for

    async def get_current_rate(self) -> int:
        now = time.time()
        window_start = now - self.window_sec
        await self.redis.zremrangebyscore(self.key, 0, window_start)
        return await self.redis.zcard(self.key)

class KotakRateLimiter:
    def __init__(self):
        self._quotes: Optional[SlidingWindowLimiter] = None
        self._orders: Optional[SlidingWindowLimiter] = None
        self._auth: Optional[SlidingWindowLimiter] = None

    def init(self, redis) -> None:
        self._quotes = SlidingWindowLimiter(
            redis=redis,
            key="rl:kotak:quotes",
            max_calls=8,
            window_sec=1.0,
            name="kotak_quotes",
        )
        self._orders = SlidingWindowLimiter(
            redis=redis,
            key="rl:kotak:orders",
            max_calls=1,
            window_sec=1.0,
            name="kotak_orders",
        )
        self._auth = SlidingWindowLimiter(
            redis=redis,
            key="rl:kotak:auth",
            max_calls=5,
            window_sec=60.0,
            name="kotak_auth",
        )
        log.info("kotak_rate_limiter:initialized", quotes="8/sec", orders="1/sec", auth="5/sec")

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
        if not self._quotes:
            return {"status": "not_initialized"}
        return {
            "status": "active",
            "quotes": await self._quotes.get_current_rate(),
            "orders": await self._orders.get_current_rate(),
            "limits": {
                "quotes": f"{self._quotes.max_calls}/sec",
                "orders": f"{self._orders.max_calls}/sec",
                "auth": f"{self._auth.max_calls}/sec",
            }
        }

    def _assert_init(self) -> None:
        if not self._quotes:
            raise RuntimeError(
                "KotakRateLimiter was not initialized properly. call .init(redis) first"
            )

kotak_limiter = KotakRateLimiter()
