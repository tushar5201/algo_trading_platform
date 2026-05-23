import dataclasses
from dataclasses import field
from enum import Enum
from typing import Optional


class SignalAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class ProductType(str, Enum):
    MIS = "MIS"  # intraday'
    NRML = "NRML"  # positional


class Exchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"
    NFO = "NFO"


@dataclasses.dataclass
class Signal:
    action: SignalAction
    symbol: str
    qty: int
    price: float
    order_type: OrderType = OrderType.MARKET
    product_type: ProductType = ProductType.MIS
    exchange: Exchange = Exchange.NSE
    strategy_name: str = ""
    confidence: float = 1.0
    reason: str = ""
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    metadata: dict = field(default_factory=dict)

    def is_actionable(self) -> bool:
        return self.action != SignalAction.HOLD