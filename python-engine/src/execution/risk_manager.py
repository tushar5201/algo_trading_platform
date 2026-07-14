import dataclasses
from zoneinfo import ZoneInfo

import structlog
from datetime import datetime, timezone
from bson import ObjectId

from src.execution.signal import Signal
from src.db.mongo import get_db


log = structlog.get_logger()
IST = ZoneInfo("Asia/Kolkata")

@dataclasses.dataclass
class RiskCheckResult:
    passed: bool
    reason: str
    daily_loss: float = 0.0
    daily_trades: int = 0


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

    def _reference_price(self, signal: Signal) -> float:
        if signal.price and signal.price > 0:
            return signal.price
        meta = signal.metadata or {}
        for key in ("entry_price", "ltp"):
            val = meta.get(key)
            if val:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    pass

        if signal.stop_loss:
            estimate = float(signal.stop_loss) / 0.70
            if estimate > 0:
                return estimate

        log.warning(
            "risk.order_value_estimate_missing",
            symbol = signal.symbol,
            strategy=signal.strategy_name,
            note="No price/entry_price/ltp/stop_loss on signal — "
                 "order value check may not catch an oversized order",
        )
        return 1.0

    async def check(self, signal: Signal, user_id: str) -> RiskCheckResult:
        """
        Run all risk checks & return RiskCheckResult
        If passed=False, do NOT place the trade
        """
        db = await get_db()
        user = await db.users.find_one({"_id": ObjectId(user_id)})
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
        reference_price = self._reference_price(signal)
        order_value = signal.qty * reference_price
        if order_value > self.PLATFORM_MAX_ORDER_VALUE:
            return RiskCheckResult(
                passed=False,
                reason=f"Order value {order_value} exceeds platform max {self.PLATFORM_MAX_ORDER_VALUE}",
            )

        # 3. Daily trade count
        today_start = datetime.now(IST).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(timezone.utc)
        daily_trades = await db.trades.count_documents(
            {
                "userId": ObjectId(user_id),
                "createdAt": {"$gte": today_start},
                "status": {"$in": ["EXECUTED", "PENDING"]},
            }
        )

        max_trades = user.get("riskLimits", {}).get(
            "maxDailyTrades", self.PLATFORM_MAX_DAILY_TRADES
        )

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
                        "userId": ObjectId(user_id),
                        "createdAt": {"$gte": today_start},
                        "status": "EXECUTED",
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

        max_daily_loss = user.get("riskLimits", {}).get(
            "maxDailyLoss", self.PLATFORM_MAX_DAILY_LOSS
        )
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
