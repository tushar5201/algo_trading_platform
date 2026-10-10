import dataclasses
from abc import abstractmethod, ABC
from datetime import datetime
from zoneinfo import ZoneInfo

import structlog

from src.db.redis_client import get_redis
from src.execution.signal import Signal, SignalAction
from src.utils.market_data import MarketSnapshot

log = structlog.get_logger()

IST = ZoneInfo('Asia/Kolkata')


class RetryLater(Exception):
    """Raised by a strategy when a run could not be completed for a transient reason (e.g. the data
    source is rate-limiting us). The schedule slot is released so a later tick can try again."""


@dataclasses.dataclass
class StrategyContext:
    """Everything a strategy needs to make a decision"""
    user_id: str
    user_email: str
    strategy_name: str
    auto_trading: bool
    credentials: dict  # decrypted kotak credentials
    # True for an admin's manual trigger: skip the schedule gate (kill switches, risk checks,
    # the market-hours guard inside a strategy and the order lock still apply)
    force: bool = False


class BaseStrategy(ABC):
    """
    Abstract base strategy class for all trading strategies
    subclass implement
        - symbols() -> list of market symbols to fetch data for
        - analyze() -> core strategy logic, returns a signal
    """

    run_times: list[str] = []
    run_interval_minutes: int | None = None
    max_open_positions: int | None = None
    SLOT_GRACE_MINUTES = 3

    def __init__(self, context: StrategyContext):
        self.context = context
        self.log = structlog.get_logger().bind(
            strategy=context.strategy_name,
            user_id=self.context.user_id,
        )

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique slug matching the DB strategy name"""
        ...

    def current_slot(self, now: datetime | None = None) -> str | None:
        now = (now or datetime.now(IST)).astimezone(IST)
        minutes_now = now.hour * 60 + now.minute
        if self.run_times:
            for t in self.run_times:
                hh, mm = (int(x) for x in t.split(":"))
                elapsed = minutes_now - (hh * 60 + mm)
                if 0 <= elapsed < self.SLOT_GRACE_MINUTES:
                    return f"{now.strftime('%Y-%m-%d')}:{t}"
            return None
        if self.run_interval_minutes:
            n = self.run_interval_minutes
            if minutes_now % n < min(self.SLOT_GRACE_MINUTES, n):
                return f"{now.strftime('%Y-%m-%d')}:{minutes_now // n}"
            return None
        return f"{now.strftime('%Y-%m-%d:%H:%M')}"

    def is_scheduled_to_run(self, now: datetime | None = None) -> bool:
        return self.current_slot(now) is not None

    async def _claim_schedule_slot(self, slot: str) -> bool:
        try:
            from src.db.redis_client import get_redis
            redis = get_redis()
            slot_key = f"schedule_lock:{self.name}:{self.context.user_id}:{slot}"
            acquired = await redis.set(slot_key, "1", nx=True, ex=6 * 3600)
            return bool(acquired)
        except Exception as e:
            self.log.warning("strategy.schedule_lock_error", error=str(e))
            return True

    async def _release_schedule_slot(self, slot: str) -> None:
        try:
            await get_redis().delete(f"schedule_lock:{self.name}:{self.context.user_id}:{slot}")
        except Exception:
            pass

    @abstractmethod
    def symbols(self) -> list[str]:
        """Market symbols to fetch data for"""
        ...

    @abstractmethod
    async def analyze(self, snapshots: dict[str, MarketSnapshot]) -> Signal:
        """
        core strategy logic
        Receives pre-fetched market snapshot data, returns a signal
        Never place orders here - just analyze & sigal
        """
        ...

    async def analyze_many(self, snapshots: dict[str, MarketSnapshot]) -> list[Signal] | None:
        return None

    async def run(self, snapshots: dict[str, MarketSnapshot]) -> list[Signal]:
        """
        Gates execution on is_scheduled_to_run() so strategies with a
        specific run_times/run_interval_minutes schedule don't pay the
        cost of analyze() on every 1-minute tick.
        """
        slot = self.current_slot()
        if slot is None and self.context.force:
            slot = f"manual:{datetime.now(IST).strftime('%Y-%m-%d:%H:%M:%S')}"
        if slot is None:
            return [Signal(action=SignalAction.HOLD, symbol="", qty=0, price=0.0, strategy_name=self.name,
                           reason="Not Scheduled to run at this time")]

        # claimed for interval strategies too (previously only run_times strategies were protected)
        if not await self._claim_schedule_slot(slot):
            return [Signal(action=SignalAction.HOLD, symbol="", qty=0, price=0.0, strategy_name=self.name,
                           reason="Already executed for this scheduled slot")]
        self.log.info("strategy.run.start", slot=slot)
        try:
            many = await self.analyze_many(snapshots)
            if many is not None:
                signals = many
            else:
                signals = [await self.analyze(snapshots)]

            for signal in signals:
                if signal.max_open_positions is None:
                    signal.max_open_positions = self.max_open_positions
                self.log.info(
                    "strategy.run.complete",
                    action=signal.action,
                    symbol=signal.symbol,
                    reason=signal.reason,
                    confidence=signal.confidence,
                )
            return signals
        except RetryLater as e:
            self.log.warning("strategy.run.retry_later", reason=str(e))
            await self._release_schedule_slot(slot)
            return [Signal(action=SignalAction.HOLD, symbol="", qty=0, price=0.0, strategy_name=self.name,
                           reason=str(e))]
        except Exception as e:
            self.log.error(
                "strategy.run.error",
                error=str(e),
                exc_info=True
            )
            await self._release_schedule_slot(slot)
            raise
