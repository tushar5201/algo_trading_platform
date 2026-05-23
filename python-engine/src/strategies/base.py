import dataclasses
from abc import abstractmethod, ABC

import structlog

from src.execution.signal import Signal
from src.utils.market_data import MarketSnapshot

log = structlog.get_logger()


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

    async def run(self, snapshots: dict[str, MarketSnapshot]) -> Signal:
        """
        Called by the worker. Wraps analyze() with logging & err handle
        """
        self.log.info("strategy.run.start")
        try:
            signal = await self.analyze(snapshots)
            self.log.info(
                "strategy.run.complete",
                action=signal.action,
                symbol=signal.symbol,
                reason=signal.reason,
                confidence=signal.confidence,
            )
            return signal
        except Exception as e:
            self.log.error(
                "strategy.run.error",
                error=str(e),
                exc_info=True
            )
            raise
