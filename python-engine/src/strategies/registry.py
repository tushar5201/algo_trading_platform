from src.strategies.base import BaseStrategy, StrategyContext
from src.strategies.oi_nifty_options import OINiftyOptionsStrategy

STRATEGY_REGISTERY: dict[str, type[BaseStrategy]] = {
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
