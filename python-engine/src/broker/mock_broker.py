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
            exchange: str
    ) -> dict:
        await asyncio.sleep(0.1)
        if not self._authenticated:
            raise Exception("MockBroker: not authenticated")

            # Only fail if explicitly configured
        if self._fail_rate > 0 and random.random() < self._fail_rate:
            raise Exception(f"MockBroker: simulated rejection for {symbol}")

        order_id = f"MOCK-{random.randint(100000, 999999)}"
        if price > 0:
            slippage = price * random.uniform(-0.005, 0.005)
            fill_price = round(price + slippage, 2)
        else:
            fill_price = round(random.uniform(50, 500), 2)
        result = {
            "order_id": order_id,
            "status": "complete",
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": fill_price,
            "order_type": order_type,
            "product_type": product_type,
            "exchange": exchange,
        }
        self._orders[order_id] = result
        log.info("mock_broker.place_order", order_id=order_id, symbol=symbol, side=side, qty=qty, price=fill_price)
        return result

    async def place_order_with_sl(
            self,
            symbol: str,
            side: str,
            qty: int,
            price: float,
            order_type: str,
            product_type: str,
            exchange: str,
            stop_loss: float = None,
    ) -> dict:
        result = await self.place_order(
            symbol, side, qty, price, order_type, product_type, exchange
        )
        if stop_loss:
            sl_order_id = f"MOCK-SL-{random.randint(100000, 999999)}"
            result["sl_order_id"] = sl_order_id
            log.info("mock_broker.sl_placed", sl_order_id=sl_order_id, stop_loss=stop_loss)
        return result

    async def get_order_status(self, order_id: str) -> dict:
        await asyncio.sleep(0.05)
        order = self._orders.get(order_id, {})
        return {
            "order_id": order_id,
            "status": order.get("status", "complete"),
            "avg_price": order.get("price", 0),
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
