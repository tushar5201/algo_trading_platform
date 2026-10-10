from src.strategies.base import BaseStrategy, StrategyContext
from src.strategies.oi_nifty_options import OINiftyOptionsStrategy
from src.strategies.ema_61_rsi_60 import Ema61Rsi60

STRATEGY_REGISTERY: dict[str, type[BaseStrategy]] = {
    "61-ema-60-rsi": Ema61Rsi60,  # ← add this
    "oi_nifty_options": OINiftyOptionsStrategy,
}

STRATEGY_REGISTERY = STRATEGY_REGISTERY

def get_strategy(name: str, context: StrategyContext) -> BaseStrategy:
    cls = STRATEGY_REGISTERY.get(name.lower())
    if cls is None:
        raise ValueError(
            f"Strategy {name} not found"
            f"Available strategies: {list(STRATEGY_REGISTERY.keys())}"
        )
    return cls(context)
