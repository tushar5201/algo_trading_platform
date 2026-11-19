import json

from src.strategies import registry


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def set(self, k, v):
        self.store[k] = v


async def test_engine_publishes_the_names_of_strategies_that_have_code():
    r = FakeRedis()
    names = await registry.publish_available_strategies(r)
    assert names == sorted(registry.STRATEGY_REGISTRY)
    assert json.loads(r.store["engine:strategies"]) == names
    assert {"61-ema-60-rsi", "oi_nifty_options"} <= set(names)


def test_discovery_keys_are_lowercase_strings():
    assert all(isinstance(k, str) and k == k.lower() for k in registry.STRATEGY_REGISTRY)
