import structlog

from src.db.mongo import get_db
from src.execution.signal import Signal
from bson import ObjectId

log = structlog.get_logger()


class ValidationError(Exception):
    pass


async def validate_signal(signal: Signal, user_id: str) -> None:
    """
    Raises ValidationError if the signal should not be traded.
    Checks: Kill switches, Signal sanity, user state.
    """
    if not signal.is_actionable():
        raise ValidationError("Signal is HOLD - not actionable")
    if signal.qty <= 0:
        raise ValidationError(f"Invalid qty {signal.qty}")
    if not signal.symbol:
        raise ValidationError(f"Empty symbol")
    if signal.confidence < 0.5:
        raise ValidationError(f"Invalid confidence {signal.confidence}")

    # 1. Global kill switch (Redis- Fastest check)
    db = await get_db()

    global_kill = await db.kill_switches.find_one({"scope": "global", "isActive": True})
    if global_kill:
        raise ValidationError(
            f"Global kill switch active: {global_kill.get('reason', '')}"
        )

    # 2. Per User kill switch
    user_kill = await db.kill_switches.find_one(
        {"scope": "user", "userId": ObjectId(user_id), "isActive": True}
    )
    if user_kill:
        raise ValidationError(f"User trading halted by admin")

    # 3. User still approved & active
    user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise ValidationError(f"User {user_id} not found")
    if user.get("status") != "APPROVED":
        raise ValidationError("User account is not approved")
    if not user.get("isActive", True):
        raise ValidationError("User account is not active")
    if not user.get("autoTrading", False):
        raise ValidationError("Auto-trading is disabled for this user")

    log.info(
        "signal.validated", user_id=user_id, symbol=signal.symbol, action=signal.action
    )
