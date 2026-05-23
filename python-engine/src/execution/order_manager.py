from datetime import datetime, timezone
import structlog
from bson import ObjectId

from src.broker.base_broker import BaseBroker
from src.db.mongo import get_db
from src.execution.signal import Signal
from src.execution.validator import validate_signal, ValidationError

log = structlog.get_logger()


async def execute_signal(signal: Signal, user_id: str, broker: BaseBroker) -> dict:
    """
    Full trade lifecycle:
        1. Validate Signal
        2. Create trade doc (status=PENDING)
        3. Call Broker
        4. Update trade doc (EXECUTED or FAILED)
        5. Return result
    """
    db = await get_db()

    # 1. Validate
    try:
        await validate_signal(signal, user_id)
    except ValidationError as e:
        log.warning("signal.blocked", reason=str(e), user_id=user_id)
        return {"success": False, "reason": str(e)}

    # 2. Create Trade document(PENDING)
    trade_doc = {
        "userId": ObjectId(user_id),
        "strategyName": signal.strategy_name,
        "symbol": signal.symbol,
        "side": signal.action.value,
        "qty": signal.qty,
        "price": signal.price,
        "orderId": None,
        "status": "PENDING",
        "pnl": None,
        "stopLoss": signal.stop_loss,
        "takeProfit": signal.take_profit,
        "errorMessage": None,
        "executedAt": None,
        "metadata": signal.metadata,
        "createdAt": datetime.now(timezone.utc),
    }
    result = await db.trades.insert_one(trade_doc)
    trade_id = str(result.inserted_id)
    log.info("trade.created", trade_id=trade_id, symbol=signal.symbol)

    # 3. Place order via broker

    try:
        if hasattr(broker, "place_order_with_sl_tp") and signal.stop_loss:
            order_result = await broker.place_order_with_sl_tp(
                symbol=signal.symbol,
                side=signal.action.value,
                qty=signal.qty,
                price=signal.price,
                order_type=signal.order_type.value,
                product_type=signal.product_type.value,
                exchange=signal.exchange.value,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
            )
        else:
            order_result = await broker.place_order(
                symbol=signal.symbol,
                side=signal.action.value,
                qty=signal.qty,
                price=signal.price,
                order_type=signal.order_type.value,
                product_type=signal.product_type.value,
                exchange=signal.exchange.value,
            )
        # 4a. SUCCCESS -> update to EXECUTED
        await db.trades.update_one(
            {"_id": ObjectId(trade_id)},
            {
                "$set": {
                    "status": "EXECUTED",
                    "orderId": order_result.get("order_id"),
                    "price": order_result.get("price", signal.price),
                    "executedAt": datetime.now(timezone.utc),
                }
            }
        )
        log.info("trade.executed", trade_id=trade_id, order_id=order_result.get("order_id"), symbol=signal.symbol)
        return {"success": True, "trade_id": trade_id, "order": order_result}
    except Exception as e:
        # 4b. FAILURE ->  update to FAILED
        await db.trades.update_one(
            {"_id": ObjectId(trade_id)},
            {
                "$set": {
                    "status": "FAILED",
                    "errorMessage": str(e),
                }
            }
        )
        log.error("trade.failed", trade_id=trade_id, error=str(e), exc_info=True)
        return {"success": False, "trade_id": trade_id, "error": str(e)}
