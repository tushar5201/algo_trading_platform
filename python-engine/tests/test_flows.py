"""End-to-end flows of the money-critical code paths using in-memory fakes."""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

import src.execution.order_manager as om
import src.execution.sl_tp_monitor as mon
import src.execution.validator as validator
from src.execution.risk_manager import RiskCheckResult
from src.execution.signal import Exchange, OrderType, ProductType, Signal, SignalAction
from tests.fakes import FakeCollection, FakeDB, ScriptedBroker

USER = str(ObjectId())


def make_signal(**kw):
    base = dict(action=SignalAction.BUY, symbol="NIFTY26SEP24500CE", qty=65, price=0.0,
                order_type=OrderType.MARKET, product_type=ProductType.MIS, exchange=Exchange.NFO,
                strategy_name="oi_nifty_options", confidence=0.8, stop_loss=70.0, take_profit=160.0,
                metadata={"entry_price": 100.0})
    base.update(kw)
    return Signal(**base)


@pytest.fixture
def env(monkeypatch):
    db = FakeDB()

    async def get_db():
        return db

    async def ok_validate(signal, user_id):
        return None

    class OkRisk:
        async def check(self, signal, user_id):
            return RiskCheckResult(True, "ok")

    audits = []

    async def audit(user_id, action, metadata, target_id=None):
        audits.append(action)

    class FakeRedis:
        def __init__(self):
            self.keys = {}

        async def set(self, k, v, nx=False, ex=None):
            if nx and k in self.keys:
                return None
            self.keys[k] = v
            return True

        async def eval(self, script, n, key, token):
            self.keys.pop(key, None)

    redis = FakeRedis()
    monkeypatch.setattr(om, "get_db", get_db)
    monkeypatch.setattr(om, "validate_signal", ok_validate)
    monkeypatch.setattr(om, "write_audit", audit)
    monkeypatch.setattr(om, "get_redis", lambda: redis)
    monkeypatch.setattr(om, "get_scrip_master", lambda: None)

    real_poll = om.poll_order

    async def fast_poll(broker, order_id, **kw):
        return await real_poll(broker, order_id, interval=0, max_attempts=2)

    monkeypatch.setattr(om, "poll_order", fast_poll)
    manager = om.OrderManager()
    manager.risk_manager = OkRisk()
    return db, manager, audits, redis


async def test_entry_fill_records_no_fake_pnl_and_places_stop_after_fill(env):
    db, manager, audits, _ = env
    # broker says "filled" with avg price 0 (a real Kotak quirk)
    broker = ScriptedBroker({"ORD1": {"status": "complete", "avg_price": 0}})
    res = await manager.execute(make_signal(), USER, broker, {})
    assert res["success"], res

    trade = db.trades.docs[0]
    assert trade["status"] == "EXECUTED"
    assert trade["pnl"] is None                      # not -cost
    assert trade["price"] == 100.0                   # fell back to entry price, not 0
    assert trade["brokerSymbol"] and trade["productType"] == "MIS" and trade["exchange"] == "NFO"
    # the stop is placed AFTER the entry and with a real trigger price
    kinds = [p.get("kind", "entry") for p in broker.placed]
    assert kinds == ["entry", "sl"]
    assert broker.placed[1]["trigger_price"] == 70.0
    assert trade["slOrderId"] == res["sl_order_id"]


async def test_timeout_cancels_the_order_and_does_not_leave_a_live_one(env):
    db, manager, audits, _ = env
    broker = ScriptedBroker()  # stays 'open' forever
    res = await manager.execute(make_signal(), USER, broker, {})
    assert not res["success"]
    assert broker.cancelled == ["ORD1"]
    assert db.trades.docs[0]["status"] == "FAILED"


async def test_timeout_with_failed_cancel_needs_human_review(env):
    db, manager, audits, _ = env
    broker = ScriptedBroker(fail_cancel=True)
    res = await manager.execute(make_signal(), USER, broker, {})
    assert not res["success"]
    assert db.trades.docs[0]["status"] == "NEEDS_REVIEW"  # never silently "FAILED" while maybe live


async def test_uncertain_order_is_not_marked_failed(env):
    from src.broker.errors import KotakOrderUncertainError
    db, manager, audits, _ = env

    class B(ScriptedBroker):
        async def place_order(self, **kw):
            raise KotakOrderUncertainError("timeout after send")

    res = await manager.execute(make_signal(), USER, B(), {})
    assert res["stage"] == "uncertain"
    assert db.trades.docs[0]["status"] == "NEEDS_REVIEW"


async def test_concurrent_run_for_same_user_strategy_is_blocked(env):
    db, manager, audits, redis = env
    redis.keys[f"order_lock:{USER}:oi_nifty_options"] = "someone-else"
    res = await manager.execute(make_signal(), USER, ScriptedBroker(), {})
    assert res["stage"] == "lock" and not db.trades.docs


async def test_lock_is_released_after_execution(env):
    db, manager, audits, redis = env
    await manager.execute(make_signal(), USER, ScriptedBroker({"ORD1": {"status": "complete", "avg_price": 99}}), {})
    assert not redis.keys


# ── validator: kill-switch collection name ────────────────────────────────────
async def test_validator_reads_the_collection_mongoose_writes(monkeypatch):
    ks = FakeCollection([{"scope": "global", "isActive": True, "reason": "halt"}])
    db = FakeDB(killswitches=ks)

    async def get_db():
        return db

    monkeypatch.setattr(validator, "get_db", get_db)
    monkeypatch.setattr(validator, "market_open_reason", lambda *a, **k: None)  # test must not depend on the clock
    with pytest.raises(validator.ValidationError, match="Global kill switch"):
        await validator.validate_signal(make_signal(), USER)
    assert "killswitches" in db.accessed and "kill_switches" not in db.accessed


# ── SL/TP monitor ─────────────────────────────────────────────────────────────
class MonDB:
    """FakeDB + find_one_and_update for the monitor's atomic claim."""

    def __init__(self, trade):
        self.trades = FakeCollection([trade])

        async def find_one_and_update(query, update, **kw):
            for d in self.trades.docs:
                if all(d.get(k) == v for k, v in query.items()):
                    before = dict(d)
                    d.update(update["$set"])
                    return before
            return None

        self.trades.find_one_and_update = find_one_and_update


@pytest.fixture
def monitor(monkeypatch):
    audits = []

    async def audit(user_id, action, metadata, target_id=None):
        audits.append(action)

    real_poll = mon.poll_order

    async def fast_poll(broker, order_id, **kw):
        return await real_poll(broker, order_id, interval=0, max_attempts=2)

    monkeypatch.setattr(mon, "write_audit", audit)
    monkeypatch.setattr(mon, "poll_order", fast_poll)
    return audits


def open_trade(**kw):
    t = {"_id": ObjectId(), "userId": ObjectId(USER), "symbol": "NIFTY26SEP24500CE",
         "brokerSymbol": "NIFTY26SEP24500CE", "side": "BUY", "qty": 65, "price": 100.0,
         "status": "EXECUTED", "stopLoss": 70.0, "takeProfit": 160.0, "productType": "MIS",
         "exchange": "NFO", "exchangeSegment": "nse_fo", "symbolToken": "60517",
         "slOrderId": "SL9", "exitAttempts": 0,
         "executedAt": datetime.now(timezone.utc)}
    t.update(kw)
    return t


async def test_tp_exit_cancels_broker_stop_first_then_records_realised_pnl(monitor):
    trade = open_trade()
    db = MonDB(trade)
    broker = ScriptedBroker({"SL9": {"status": "open"}, "ORD1": {"status": "complete", "avg_price": 162.0}})
    m = mon.SLTPMonitor()
    await m._exit_position(db, broker, trade, 161.0, "TAKE_PROFIT")

    assert broker.cancelled == ["SL9"]                      # stop cancelled BEFORE the exit
    assert [p["side"] for p in broker.placed] == ["SELL"]   # exactly one exit
    assert broker.placed[0]["symbol"] == "NIFTY26SEP24500CE" and broker.placed[0]["exchange"] == "NFO"
    doc = db.trades.docs[0]
    assert doc["status"] == "CLOSED" and doc["pnl"] == round((162 - 100) * 65, 2)


async def test_broker_stop_that_already_filled_is_not_sold_twice(monitor):
    trade = open_trade()
    db = MonDB(trade)
    broker = ScriptedBroker({"SL9": {"status": "complete", "avg_price": 70.0}})
    await mon.SLTPMonitor()._exit_position(db, broker, trade, 69.0, "STOP_LOSS")
    assert broker.placed == []                               # no second SELL -> no accidental short
    doc = db.trades.docs[0]
    assert doc["status"] == "CLOSED" and doc["pnl"] == round((70 - 100) * 65, 2)


async def test_exit_is_claimed_atomically(monitor):
    trade = open_trade(status="EXIT_PENDING")               # someone else already claimed it
    db = MonDB(trade)
    broker = ScriptedBroker()
    await mon.SLTPMonitor()._exit_position(db, broker, trade, 50.0, "STOP_LOSS")
    assert broker.placed == [] and broker.cancelled == []


async def test_unconfirmed_exit_goes_to_review_instead_of_retrying(monitor):
    trade = open_trade(slOrderId=None)
    db = MonDB(trade)
    broker = ScriptedBroker()                                # exit never fills
    await mon.SLTPMonitor()._exit_position(db, broker, trade, 50.0, "STOP_LOSS")
    assert len(broker.placed) == 1                           # never re-sent
    assert db.trades.docs[0]["status"] == "NEEDS_REVIEW"
    assert "TRADE_NEEDS_REVIEW" in monitor


async def test_stale_mis_trade_closed_without_sending_an_order(monitor):
    yday = datetime.now(timezone.utc) - timedelta(days=1)
    trade = open_trade(executedAt=yday)
    db = MonDB(trade)
    await mon.SLTPMonitor()._close_without_order(db, trade, "MIS_AUTO_SQUAREOFF")
    doc = db.trades.docs[0]
    assert doc["status"] == "CLOSED" and doc["pnl"] is None


async def test_failed_exit_retries_then_escalates(monitor):
    trade = open_trade(slOrderId=None)
    db = MonDB(trade)

    class Rejecting(ScriptedBroker):
        async def place_order(self, **kw):
            raise RuntimeError("exchange closed")

    m = mon.SLTPMonitor()
    await m._exit_position(db, Rejecting(), trade, 50.0, "STOP_LOSS")
    assert db.trades.docs[0]["status"] == "EXECUTED"         # will be retried next cycle
    trade2 = open_trade(slOrderId=None, exitAttempts=2)
    db2 = MonDB(trade2)
    await m._exit_position(db2, Rejecting(), trade2, 50.0, "STOP_LOSS")
    assert db2.trades.docs[0]["status"] == "NEEDS_REVIEW"    # no longer silently abandoned


# ── expired Kotak session ─────────────────────────────────────────────────────
async def test_expired_session_removes_the_stored_session(env):
    from src.broker.errors import KotakAuthError
    db, manager, audits, redis = env

    class DeadSession(ScriptedBroker):
        invalidated = False

        async def place_order(self, **kw):
            raise KotakAuthError("Session expired - re-authenticate")

        async def invalidate_session(self):
            self.invalidated = True

    b = DeadSession()
    res = await manager.execute(make_signal(), USER, b, {})
    assert res["stage"] == "auth" and b.invalidated
    assert db.trades.docs[0]["status"] == "FAILED"


async def test_worker_stops_after_the_first_auth_failure(monkeypatch):
    from src.queue.worker import StrategyWorker
    w = StrategyWorker()
    seen, notified = [], []

    class FakeOM:
        async def execute(self, signal, user_id, broker, credentials):
            seen.append(signal.symbol)
            return {"success": False, "stage": "auth", "reason": "Session expired"}

    async def notify(user_id):
        notified.append(user_id)

    w.order_manager = FakeOM()
    monkeypatch.setattr(w, "_notify_auth_required", notify)
    sigs = [make_signal(symbol=s) for s in ("BROOKS", "FINOPB", "UNIINFO")]
    results = await w._execute_signals(sigs, USER, object(), {})
    assert seen == ["BROOKS"] and len(results) == 1      # was: 3 attempts, 3 FAILED trade rows
    assert notified == [USER]                            # the dashboard now shows "re-authenticate"
