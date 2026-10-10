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
            exchange: str,
            trigger_price: float = 0.0,
    ) -> dict:
        # place order
        ...

    @abstractmethod
    async def place_sl_order(
            self,
            symbol: str,
            position_side: str,
            qty: int,
            trigger_price: float,
            product_type: str,
            exchange: str,
        ) -> dict:
            # protective stop on the opposite side of an OPEN position
            ...

    @abstractmethod
    async def get_order_status(self, order_id: str) -> dict:
        # Fetch current status of order
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> None:
        # Cancel a pending order
        ...

    async def close(self) -> None:
        """Release network resources. Safe to call more than once."""
        return None
