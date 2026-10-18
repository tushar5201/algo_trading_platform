"""A stop-loss derived from a stale scan price must not open a position already below its stop."""
import pytest

from src.execution.order_manager import rebase_exit_levels
from src.execution.sl_tp_monitor import evaluate_exit
from tests.test_flows import USER, ScriptedBroker, env, make_signal  # noqa: F401  (fixture reuse)

# numbers from the real MFML trade: scan price 21.25 -> SL 20.19 / TP 23.38, but Kotak filled at 19.83
SCAN, FILL = 21.25, 19.83


def mfml_signal():
    return make_signal(symbol="MFML", stop_loss=round(SCAN * 0.95, 2), take_profit=round(SCAN * 1.10, 2),
                       metadata={"entry_price": SCAN})


def test_without_rebasing_the_trade_would_be_stopped_out_at_once():
    s = mfml_signal()
    assert s.stop_loss == 20.19 and s.stop_loss > FILL                       # the bug
    assert evaluate_exit("BUY", FILL, s.stop_loss, s.take_profit) == "STOP_LOSS"


def test_rebase_keeps_the_percentages_but_anchors_them_to_the_fill():
    s = mfml_signal()
    rebase_exit_levels(s, FILL)
    assert s.stop_loss == pytest.approx(FILL * 0.95, abs=0.011)       # +-1 paisa: the strategy's own levels are rounded
    assert s.take_profit == pytest.approx(FILL * 1.10, abs=0.011)
    assert s.stop_loss < FILL < s.take_profit
    assert evaluate_exit("BUY", FILL, s.stop_loss, s.take_profit) is None   # no instant exit any more
    assert evaluate_exit("BUY", 18.0, s.stop_loss, s.take_profit) == "STOP_LOSS"   # real stop still works


def test_rebase_is_a_no_op_without_a_reference_price_or_with_no_levels():
    s = make_signal(stop_loss=70.0, take_profit=160.0, metadata={})
    rebase_exit_levels(s, 95.0)
    assert (s.stop_loss, s.take_profit) == (70.0, 160.0)
    s2 = make_signal(stop_loss=None, take_profit=None, metadata={"entry_price": 100.0})
    rebase_exit_levels(s2, 95.0)
    assert (s2.stop_loss, s2.take_profit) == (None, None)
    s3 = make_signal(metadata={"entry_price": 100.0})
    rebase_exit_levels(s3, 0)                                                # unknown fill: leave as is
    assert (s3.stop_loss, s3.take_profit) == (70.0, 160.0)


async def test_filled_trade_stores_and_protects_with_the_rebased_levels(env):  # noqa: F811
    db, manager, audits, _ = env
    broker = ScriptedBroker({"ORD1": {"status": "complete", "avg_price": FILL}})
    res = await manager.execute(mfml_signal(), USER, broker, {})
    assert res["success"], res
    trade = db.trades.docs[0]
    assert trade["price"] == FILL
    assert trade["stopLoss"] == pytest.approx(FILL * 0.95, abs=0.011)
    assert trade["takeProfit"] == pytest.approx(FILL * 1.10, abs=0.011)
    assert broker.placed[1]["trigger_price"] == trade["stopLoss"]            # the broker-side stop uses it too
    assert evaluate_exit("BUY", FILL, trade["stopLoss"], trade["takeProfit"]) is None
