import dataclasses
import random

import structlog

log = structlog.get_logger()

@dataclasses.dataclass
class OptionChainEntry:
    strike: float
    call_oi: int
    put_oi: int
    call_ltp: int
    put_ltp: int
    call_iv: int
    put_iv: int

@dataclasses.dataclass
class MarketSnapshot:
    symbol: str
    ltp: float
    open: float
    high: float
    low: float
    volume: int
    option_chain: list[OptionChainEntry]

async def get_market_snapshot(symbol: str) -> MarketSnapshot:
    """
    Mock market data. Replace with live kotak websocket feed
    produce realistic data for strategy testing
    """
    log.debug("market_data.fetch", symbol=symbol)
    base_price = 22_400.0 + random.uniform(-300, 300)

    # Generate option chain around ATM strike
    atm = round(base_price / 50) * 50 # nearest 50
    strikes = [atm + (i*50) for i in range(-5, 6)]

    option_chain=[]
    for strike in strikes:
        moneyness = base_price - strike
        call_oi = int(abs(random.gauss(500_000, 200_000)) * (1 + moneyness/1000))
        put_oi = int(abs(random.gauss(500_000, 200_000)) * (1 - moneyness/1000))
        option_chain.append(OptionChainEntry(
            strike=float(strike),
            call_oi = max(call_oi, 10_000),
            put_oi = max(put_oi, 10_000),
            call_ltp = max(0.5, round(random.uniform(10, 300), 2)),
            put_ltp = max(0.5, round(random.uniform(10, 300), 2)),
            call_iv = round(random.uniform(12, 25), 2),
            put_iv=round(random.uniform(12, 25), 2),
        ))

    return MarketSnapshot(
        symbol=symbol,
        ltp=round(base_price, 2),
        open=round(base_price - random.uniform(-100, 100), 2),
        high=round(base_price + random.uniform(0, 200), 2),
        low=round(base_price - random.uniform(0, 200), 2),
        volume=random.randint(50_000, 500_000),
        option_chain=option_chain,
    )
