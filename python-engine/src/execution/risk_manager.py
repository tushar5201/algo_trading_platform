import dataclasses
from typing import Optional
from zoneinfo import ZoneInfo

import structlog
from datetime import datetime, timezone
from bson import ObjectId

from src.execution.signal import Signal
from src.db.mongo import get_db

log = structlog.get_logger()
IST = ZoneInfo("Asia/Kolkata")

COUNTED_STATUSES = ["PENDING", "EXECUTED", "EXIT_PENDING", "CLOSED", "NEEDS_REVIEW"]
OPEN_STATUSES = ["PENDING", "EXECUTED", "EXIT_PENDING", "NEEDS_REVIEW"]


@dataclasses.dataclass
class RiskCheckResult:
    passed: bool
    reason: str
    daily_loss: float = 0.0
    daily_trades: int = 0


def effective_limit(user_value: Optional[float], platform_max: float) -> float:
    if user_value is None or user_value <= 0:
        return platform_max
    return min(user_value, platform_max)


def reference_price(signal: Signal) -> Optional[float]:
    if signal.price and signal.price > 0:
        return float(signal.price)
    meta = signal.metadata or {}
    for key in ("entry_price", "ltp"):
        val = meta.get(key)
        if val:
            try:
                f = float(val)
                if f > 0:
                    return f
            except (ValueError, TypeError):
                pass
    return None


def ist_day_start_utc(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(IST)
    return now.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


class RiskManager:
    """
    Per-User daily risk limits
    All limits stored in MongoDB users collection
    """

    # Platform wide hard limits
    PLATFORM_MAX_DAILY_LOSS: float = 50_000.0
    PLATFORM_MAX_DAILY_TRADES: int = 50
    PLATFORM_MAX_QTY = 10_000
    PLATFORM_MAX_ORDER_VALUE = 500_000

    async def check(self, signal: Signal, user_id: str) -> RiskCheckResult:
        """
        Run all risk checks & return RiskCheckResult
        If passed=False, do NOT place the trade
        """
        db = await get_db()
        uid = ObjectId(user_id)
        user = await db.users.find_one({"_id": uid})
        if not user:
            return RiskCheckResult(passed=False, reason="User not found")

        # 1. Basic signal sanity checks
        if not signal.is_actionable():
            return RiskCheckResult(
                passed=False, reason="Signal is HOLD - not actionable"
            )
        if signal.qty <= 0:
            return RiskCheckResult(passed=False, reason=f"Invalid qty {signal.qty}")
        if signal.qty > self.PLATFORM_MAX_QTY:
            return RiskCheckResult(
                passed=False,
                reason=f"Qty {signal.qty} exceeds platform max {self.PLATFORM_MAX_QTY}",
            )

        # 2. order value check
        ref_price = reference_price(signal)
        if ref_price is None:
            return RiskCheckResult(
                passed=False,
                reason="No reference price on signal - cannot verify order value",
            )
        order_value = signal.qty * ref_price
        if order_value > self.PLATFORM_MAX_ORDER_VALUE:
            return RiskCheckResult(
                passed=False,
                reason=f"Order value {order_value:,.0f} exceeds platform max {self.PLATFORM_MAX_ORDER_VALUE:,}",
            )

        open_same_symbol = await db.trades.count_documents({
            "userId": uid,
            "symbol": signal.symbol,
            "status": {"$in": OPEN_STATUSES}
        })

        if open_same_symbol > 0:
            return RiskCheckResult(
                passed=False,
                reason=f"A position in {signal.symbol} is already open or pending"
            )
        if signal.max_open_positions is not None:
            open_for_strategy = await db.trades.count_documents({
                "userId": uid,
                "strategyName": signal.strategy_name,
                "status": {"$in": OPEN_STATUSES},
            })
            if open_for_strategy >= signal.max_open_positions:
                return RiskCheckResult(
                    passed=False,
                    reason=(f"Strategy {signal.strategy_name} already has {open_for_strategy} open "
                            f"position(s) (max {signal.max_open_positions})"),
                )

        limits = user.get("riskLimits") or {}

        # 3. Daily trade count
        today_start = ist_day_start_utc()
        daily_trades = await db.trades.count_documents({
            "userId": uid,
            "createdAt": {"$gte": today_start},
            "status": {"$in": COUNTED_STATUSES},
        })

        max_trades = effective_limit(limits.get("maxDailyTrades"), self.PLATFORM_MAX_DAILY_TRADES)
        if daily_trades >= max_trades:
            return RiskCheckResult(
                passed=False,
                reason=f"Daily trades limit exceeded ({daily_trades}/{max_trades})",
                daily_trades=daily_trades,
            )

        # 4. Daily loss limit
        loss_result = await db.trades.aggregate(
            [
                {
                    "$match": {
                        "userId": uid,
                        "closedAt": {"$gte": today_start},
                        "status": "CLOSED",
                        "pnl": {"$ne": None},
                    }
                },
                {
                    "$group": {
                        "_id": None,
                        "totalPnl": {"$sum": "$pnl"},
                    }
                },
            ]
        ).to_list(1)
        daily_pnl = loss_result[0]["totalPnl"] if loss_result else 0.0
        daily_loss = abs(min(daily_pnl, 0))

        max_daily_loss = effective_limit(limits.get("maxDailyLoss"), self.PLATFORM_MAX_DAILY_LOSS        )
        if daily_loss >= max_daily_loss:
            return RiskCheckResult(
                passed=False,
                reason=f"Daily loss limit exceeded ({daily_loss:,.0f}/{max_daily_loss:,.0f})",
                daily_loss=daily_loss,
                daily_trades=daily_trades,
            )
        log.info(
            "risk.passed",
            user_id=user_id,
            symbol=signal.symbol,
            order_value=order_value,
            daily_trades=daily_trades,
            daily_loss=daily_loss,
        )
        return RiskCheckResult(
            passed=True,
            reason="All risk checks passed",
            daily_trades=daily_trades,
            daily_loss=daily_loss,
        )
