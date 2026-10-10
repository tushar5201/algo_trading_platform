import structlog

from src.broker.base_broker import BaseBroker
from src.broker.errors import KotakAuthError
import asyncio

log = structlog.get_logger()

FILLED_STATUSES = {"filled", "complete", "traded"}
REJECTED_STATUSES = {"rejected", "cancelled", "canceled", "cancel"}

POLL_INTERVAL_SEC = 2
POLL_MAX_ATTEMPTS = 15


def classify(status: str) -> str:
    s = (status or "").lower().strip()
    if s in FILLED_STATUSES:
        return "filled"
    if s in REJECTED_STATUSES:
        return "rejected"
    return "pending"


async def poll_order(
        broker: BaseBroker,
        order_id: str,
        *,
        interval: float = POLL_INTERVAL_SEC,
        max_attempts: int = POLL_MAX_ATTEMPTS,
) -> dict:
    for attempt in range(max_attempts):
        await asyncio.sleep(interval)
        try:
            status = await broker.get_order_status(order_id)
        except KotakAuthError as e:
            log.error("order.poll_auth_error", order_id=order_id, error=str(e))
            return {"outcome": "unknown", "avg_price": 0.0, "reason": f"Session lost while polling: {e}"}
        except Exception as e:
            log.warning("order.poll_error", attempt=attempt + 1, order_id=order_id, error=str(e))
            continue

        kind = classify(status.get("status", ""))
        log.debug("order.poll", attempt=attempt + 1, order_id=order_id, status=status.get("status"))
        if kind == "filled":
            try:
                avg = float(status.get("avg_price") or 0)
            except (TypeError, ValueError):
                avg = 0.0
            return {"outcome": "filled", "avg_price": avg, "reason": f"order filled"}

        if kind == "rejected":
            reason = status.get("rejection_reason") or "Order rejected"
            return {"outcome": "rejected", "avg_price": 0.0, "reason": reason}

    return {
        "outcome": "timeout",
        "avg_price": 0.0,
        "reason": f"Order still not filled after {max_attempts * interval:.0f}s",
    }
