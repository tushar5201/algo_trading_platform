import importlib
import inspect
import pkgutil

import structlog

import src.strategies as _pkg
from src.strategies.base import BaseStrategy, StrategyContext

log = structlog.get_logger()

_SKIP_MODULES = {"base", "registry"}

def _probe_context() -> StrategyContext:
    return StrategyContext(user_id="", user_email="", strategy_name="", auto_trading=False, credentials={})

def _discover() -> dict[str, type[BaseStrategy]]:
    found: dict[str, type[BaseStrategy]] = {}
    for info in pkgutil.iter_modules(_pkg.__path__):
        if info.name in _SKIP_MODULES or info.name.startswith("_"):    
            continue
        module = importlib.import_module(f"src.strategies.{info.name}")
        
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module.__name__ or not issubclass(cls, BaseStrategy) or inspect.isabstract(cls):
                continue
            try:
                key = str(cls(_probe_context()).name).lower()
            except Exception as exc:
                log.error("strategy_discovery_failed", module=info.name, cls=cls.__name__, error=str(exc))
                continue
            if key in found:
                raise RuntimeError(f"Duplicate strategy name '{key}': {found[key].__name__} and {cls.__name__}")
            found[key] = cls
    return found

STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = _discover()
STRATEGY_REGISTERY = STRATEGY_REGISTRY

AVAILABLE_STRATEGIES_KEY = "engine:strategies"

async def publish_available_strategies(redis) -> list[str]:
    import json
    names = sorted(STRATEGY_REGISTRY)
    await redis.set(AVAILABLE_STRATEGIES_KEY, json.dumps(names))
    log.info("strategies.published", strategies=names)
    return names

def get_strategy(name: str, context: StrategyContext) -> BaseStrategy:
    cls = STRATEGY_REGISTRY.get(name.lower())
    if cls is None:
        raise ValueError(
            f"Strategy {name} not found"
            f"Available strategies: {list(STRATEGY_REGISTRY)}"
        )
    return cls(context)
