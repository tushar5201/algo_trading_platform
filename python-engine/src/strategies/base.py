import dataclasses
from abc import abstractmethod, ABC
from datetime import datetime
from zoneinfo import ZoneInfo

import structlog

from src.execution.signal import Signal, SignalAction
from src.utils.market_data import MarketSnapshot

log = structlog.get_logger()

IST = ZoneInfo('Asia/Kolkata')


@dataclasses.dataclass
class StrategyContext:
    """Everything a strategy needs to make a decision"""
    user_id: str
    user_email: str
    strategy_name: str
    auto_trading: bool
    credentials: dict  # decrypted kotak credentials


class BaseStrategy(ABC):
    """
    Abstract base strategy class for all trading strategies
    subclass implement
        - symbols() -> list of market symbols to fetch data for
        - analyze() -> core strategy logic, returns a signal
    """

    run_times: list[str] = []
    run_interval_minutes: int | None = None

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

    def is_scheduled_to_run(self, now: datetime | None = None) -> bool:
        now = (now or datetime.now(IST)).astimezone(IST)
        if self.run_times:
            return now.strftime("%H:%M") in self.run_times
        if self.run_interval_minutes:
            return now.minute % self.run_interval_minutes == 0
        return True

    async def _claim_schedule_slot(self, now: datetime) -> bool:
        try:
            from src.db.redis_client import get_redis
            redis = get_redis()
            if redis is None:
                return True
            slot_key = slot_key = f"schedule_lock:{self.name}:{self.context.user_id}:{now.strftime('%Y-%m-%d:%H:%M')}"
            acquired = await redis.set(slot_key, "1", nx=True, ex=90)
            return bool(acquired)
        except Exception as e:
            self.log.warning("strategy.schedule_lock_error", error=str(e))
            return True

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
        now = datetime.now(IST)
        if not self.is_scheduled_to_run(now):
            return [Signal(action=SignalAction.HOLD, symbol="", qty=0, price=0.0, strategy_name=self.name,
                          reason="Not Scheduled to run at this time")]

        if self.run_times and not await self._claim_schedule_slot(now):
            return [Signal(action=SignalAction.HOLD, symbol="", qty=0, price=0.0, strategy_name=self.name,
                          reason="Already executed for this scheduled slot")]
        self.log.info("strategy.run.start")
        try:
            many = await self.analyze_many(snapshots)
            if many is not None:
                signals = many
            else:
                signals = [await self.analyze(snapshots)]

            for signal in signals:
                self.log.info(
                    "strategy.run.complete",
                    action=signal.action,
                    symbol=signal.symbol,
                    reason=signal.reason,
                    confidence=signal.confidence,
                )
            return signals
        except Exception as e:
            self.log.error(
                "strategy.run.error",
                error=str(e),
                exc_info=True
            )
            raise
