from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import structlog

from config.settings import settings
from src.db.mongo import get_db
from src.execution.signal import Signal
from bson import ObjectId

log = structlog.get_logger()

KILL_SWITCH_COLLECTION = "killswitches"

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = (9, 15)
MARKET_CLOSE = (15, 30)

class ValidationError(Exception):
    pass

def market_open_reason(now: Optional[datetime] = None) -> Optional[str]:
    now = (now or datetime.now(IST)).astimezone(IST)
    if now.weekday() >= 5:
        return f"Market is closed (weekend: {now:%A})"
    if not (MARKET_OPEN <= (now.hour, now.minute) < MARKET_CLOSE):
        return f"Market is closed ({now:%H:%M} IST; NSE trades Mon-Fri 09:15-15:30 IST)"
    return None

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
        raise ValidationError(f"Confidence {signal.confidence} is below the 0.5 minimum")

    if not settings.use_mock_broker and not settings.allow_after_hours_orders:
        closed = market_open_reason()
        if closed:
            raise ValidationError(closed)

    # 1. Global kill switch (Redis- Fastest check)
    db = await get_db()
    kill_switches = db[KILL_SWITCH_COLLECTION]

    global_kill = await db.killswitches.find_one({"scope": "global", "isActive": True})
    if global_kill:
        raise ValidationError(
            f"Global kill switch active: {global_kill.get('reason', '')}"
        )

    # 2. Per User kill switch
    user_kill = await kill_switches.find_one(
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

async def halted_reason(user_id: str) -> str | None:
    """Cheap kill-switch check used to skip a job (and its expensive market scan) up front."""
    db = await get_db()
    g = await db[KILL_SWITCH_COLLECTION].find_one({"scope": "global", "isActive": True})
    if g:
        return f"Global kill switch active: {g.get('reason', '')}"
    u = await db[KILL_SWITCH_COLLECTION].find_one({"scope": "user", "userId": ObjectId(user_id), "isActive": True})
    if u:
        return "User trading halted by admin"
    return None
