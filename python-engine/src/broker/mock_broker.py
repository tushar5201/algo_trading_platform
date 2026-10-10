import asyncio
import random

import structlog

from src.broker.errors import KotakBusinessError
from src.broker.base_broker import BaseBroker

log = structlog.get_logger()


class MockBroker(BaseBroker):
    """
    Simulates broker responses for local development and testing.
    Randomely fails 10% of orders to test errors handliing
    """

    def __init__(self, fail_rate: float = 0.0):
        self._fail_rate = fail_rate
        self._authenticated = False
        self._orders: dict[str, dict] = {}

    async def authenticate(self, credentials: dict) -> bool:
        await asyncio.sleep(0.05)  # simulate network
        self._authenticated = True
        log.info("mock_broker.authenticated", client_id=credentials.get("clientId", "mock"))
        return True

    async def place_order(
            self,
            symbol: str,
            side: str,
            qty: int,
            price: float,
            order_type: str,
            product_type: str,
            exchange: str,
            trigger_price: float=0.0
    ) -> dict:
        await asyncio.sleep(0.1)
        if not self._authenticated:
            raise Exception("MockBroker: not authenticated")

            # Only fail if explicitly configured
        if self._fail_rate > 0 and random.random() < self._fail_rate:
            raise KotakBusinessError(f"MockBroker: simulated rejection for {symbol}")

        order_id = f"MOCK-{random.randint(100000, 999999)}"
        if price > 0:
            fill_price = round(price * (1 + random.uniform(-0.005, 0.005)), 2)
        else:
            fill_price = round(random.uniform(50, 500), 2)
        is_stop = order_type.upper() in ("SL", "SL-M")
        result = {
            "order_id": order_id,
            "status": "open" if is_stop else "complete",
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": fill_price,
            "trigger_price": trigger_price,
            "order_type": order_type,
            "product_type": product_type,
            "exchange": exchange
        }
        self._orders[order_id] = result
        log.info("mock_broker.place_order", order_id=order_id, symbol=symbol, side=side, qty=qty, price=fill_price)
        return result

    async def place_sl_order(
            self,
            symbol,
            position_side,
            qty,
            trigger_price,
            product_type,
            exchange,
        ) -> dict:
        sl_side = "SELL" if position_side == "BUY" else "BUY"
        return await self.place_order(
            symbol, sl_side, qty, 0.0, "SL-M", product_type, exchange, trigger_price
        )

    async def get_order_status(self, order_id: str) -> dict:
        await asyncio.sleep(0.05)
        order = self._orders.get(order_id, {})
        return {
            "order_id": order_id,
            "status": order.get("status", "complete"),
            "avg_price": order.get("price", 0) if order.get("status", "complete") == "complete" else 0,
            "rejection_reason": "",
            "raw": [order],
        }

    async def cancel_order(self, order_id: str) -> bool:
        await asyncio.sleep(0.05)
        if order_id in self._orders:
            self._orders[order_id]["status"] = "cancelled"
            log.info("mock_broker.order_cancelled", order_id=order_id)
            return True
        return False

    async def check_margin(self, *args, **kwargs) -> dict:
        """Always return sufficient margin in mock."""
        return {
            "avlCash": "999999.00",
            "avlMrgn": "999999.00",
            "insufFund": "0.00",
            "rmsVldtd": "OK",
            "ordMrgn": "1000.00",
            "reqdMrgn": "1000.00",
            "stat": "Ok",
        }
