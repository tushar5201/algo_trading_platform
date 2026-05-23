from abc import abstractmethod, ABC


class BaseBroker(ABC):
    """
    Abstract broker interface. All brokers must implement this interface.
    """

    @abstractmethod
    async def authenticate(self, credentials: dict) -> bool:
        # Authenticates with broker & return True if successful
        ...

    @abstractmethod
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
        # place order
        ...

    @abstractmethod
    async def get_order_status(self, order_id: str) -> dict:
        # Fetch current status of order
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> None:
        # Cancel a pending order
        ...
