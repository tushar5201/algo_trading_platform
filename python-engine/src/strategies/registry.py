from src.execution.signal import Signal, SignalAction
from src.strategies.base import BaseStrategy, StrategyContext
from src.strategies.oi_nifty_options import OINiftyOptionsStrategy
from src.utils.market_data import MarketSnapshot


class EmaRsiStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "61-ema-60-rsi"

    def symbols(self) -> list[str]:
        return ["NIFTY"]

    async def analyze(self, snapshots: dict[str, MarketSnapshot]) -> Signal:
        # Placeholder — implement your EMA/RSI logic here
        return Signal(
            action        = SignalAction.HOLD,
            symbol        = "",
            qty           = 0,
            price         = 0.0,
            strategy_name = self.name,
            reason        = "EMA-RSI strategy not implemented yet",
        )

STRATEGY_REGISTERY: dict[str, type[BaseStrategy]] = {
    "61-ema-60-rsi": EmaRsiStrategy,  # ← add this
    "oi_nifty_options": OINiftyOptionsStrategy,
}

def get_strategy(name: str, context: StrategyContext) -> BaseStrategy:
    cls = STRATEGY_REGISTERY.get(name)
    if cls is None:
        raise ValueError(
            f"Strategy {name} not found"
            f"Available strategies: {list(STRATEGY_REGISTERY.keys())}"
        )
    return cls(context)
