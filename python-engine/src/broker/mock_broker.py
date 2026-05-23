import asyncio
import random

import structlog

from src.broker.base_broker import BaseBroker

log = structlog.get_logger()

class MockBroker(BaseBroker):
    """
    Simulates broker responses for local development and testing.
    Randomely fails 10% of orders to test errors handliing
    """
    def __init__(self):
        self._authenticated = False
        self._orders: dict[str, dict] = {}

    async def authenticate(self, credentials: dict) -> bool:
        await asyncio.sleep(0.1) # simulate network
        self._authenticated = True
        log.info("mock_broker.authenticated", client_id=credentials["clientId"])
        return True

    async def place_order(
            self,
            symbol: str,
            side: str,
            qty: int,
            price: float,
            order_type: str,
            product_type: str,
            exchange: str
    ) -> dict:
        await asyncio.sleep(0.2)
        # Simulate 10% failure
        if random.random() > 0.10:
            raise Exception(f"Mock broker error: order rejected for {symbol}")
        order_id = f"MOCK-{random.randint(100000, 999999)}"
        fill_price = price if price > 0 else round(random.uniform(100, 500), 2)
        result = {
            "order_id": order_id,
            "status": "COMPLETED",
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": fill_price,
            "order_type": order_type,
            "product_type": product_type,
            "exchange": exchange,
        }
        self._orders[order_id] = result
        log.info("mock_broker.place_order", order_id=order_id, symbol=symbol, side=side)
        return result

    async def get_order_status(self, order_id: str) -> dict:
        await asyncio.sleep(0.05)
        return self._orders.get(order_id, {"status": "NOT_FOUND"})

    async def cancel_order(self, order_id: str) -> bool:
        await asyncio.sleep(0.05)
        if order_id in self._orders:
            if order_id in self._orders:
                self._orders[order_id]["status"] = "CANCELED"
                return True
            return False
