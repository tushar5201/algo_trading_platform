import asyncio
from datetime import datetime, timezone
import time
from typing import Callable, Awaitable


class DailyCapReached(Exception):
    pass


class CallGate:
    def __init__(self, get_redis: Callable, cap: int, min_interval: float, *,
                 sleep: Callable[[float], Awaitable] = asyncio.sleep,
                 monotonic: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 ):
        self._get_redis, self.cap, self.min_interval = get_redis, cap, min_interval
        self._sleep, self._monotonic, self._now = sleep, monotonic, now
        self._lock = asyncio.Lock()
        self._last = -1e12

    @staticmethod
    def key_for(day: datetime) -> str:
        return f"ai:calls:{day:%Y-%m-%d}"

    async def acquire(self) -> None:
        redis = self._get_redis()
        key = self.key_for(self._now())
        used = await redis.incr(key)
        if used == 1:
            await redis.expire(key, 3*86400)
        if used > self.cap:
            await redis.decr(key)
            raise DailyCapReached(f"daily AI cap reached ({self.cap})")
        async with self._lock:
            wait = self._last + self.min_interval - self._monotonic()
            if wait > 0:
                await self._sleep(wait)
            self._last = self._monotonic()


