from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.broker.errors import (KotakAuthError, KotakBusinessError, KotakOrderUncertainError,
                               KotakRateLimitError, KotakAPIError)
from src.execution.order_status import classify, poll_order
from src.execution.risk_manager import effective_limit, ist_day_start_utc, reference_price
from src.execution.signal import Signal, SignalAction
from src.execution.sl_tp_monitor import evaluate_exit, is_stale_intraday, realised_pnl
from src.utils.retry import _is_retryable, _is_retryable_order
from tests.fakes import ScriptedBroker

IST = ZoneInfo("Asia/Kolkata")


# ── risk helpers ──────────────────────────────────────────────────────────────
def test_user_limit_can_only_tighten_platform_limit():
    assert effective_limit(10_000, 50_000) == 10_000
    assert effective_limit(999_999, 50_000) == 50_000  # cannot exceed the platform cap
    assert effective_limit(None, 50_000) == 50_000
    assert effective_limit(0, 50_000) == 50_000


def test_reference_price_never_invents_a_price():
    sig = Signal(action=SignalAction.BUY, symbol="X", qty=1, price=0.0)
    assert reference_price(sig) is None  # used to silently become 1.0
    sig.metadata = {"entry_price": "250.5"}
    assert reference_price(sig) == 250.5
    sig.price = 99
    assert reference_price(sig) == 99


def test_ist_day_start_is_midnight_ist():
    now = datetime(2026, 9, 30, 2, 0, tzinfo=IST)
    start = ist_day_start_utc(now)
    assert start == datetime(2026, 9, 29, 18, 30, tzinfo=timezone.utc)


# ── exit logic ────────────────────────────────────────────────────────────────
def test_evaluate_exit_long_and_short():
    assert evaluate_exit("BUY", 94, 95, 110) == "STOP_LOSS"
    assert evaluate_exit("BUY", 111, 95, 110) == "TAKE_PROFIT"
    assert evaluate_exit("BUY", 100, 95, 110) is None
    assert evaluate_exit("SELL", 106, 105, 90) == "STOP_LOSS"
    assert evaluate_exit("SELL", 89, 105, 90) == "TAKE_PROFIT"
    assert evaluate_exit("BUY", 0, 95, 110) is None  # no price -> no decision


def test_realised_pnl_is_profit_not_cost():
    assert realised_pnl("BUY", 100, 110, 10) == 100.0
    assert realised_pnl("BUY", 100, 90, 10) == -100.0
    assert realised_pnl("SELL", 100, 90, 10) == 100.0


def test_stale_mis_position_is_detected():
    yday = datetime(2026, 9, 29, 10, 0, tzinfo=IST)
    today_noon = datetime(2026, 9, 30, 12, 0, tzinfo=IST)
    assert is_stale_intraday({"productType": "MIS", "executedAt": yday}, today_noon)
    assert not is_stale_intraday({"productType": "MIS", "executedAt": today_noon}, today_noon)
    assert not is_stale_intraday({"productType": "NRML", "executedAt": yday}, today_noon)
    after_close = datetime(2026, 9, 30, 15, 45, tzinfo=IST)
    assert is_stale_intraday({"productType": "MIS", "executedAt": today_noon}, after_close)


# ── retry policy ──────────────────────────────────────────────────────────────
def test_orders_are_never_retried_on_ambiguous_failures():
    import httpx
    assert _is_retryable_order(KotakRateLimitError("429"))
    assert _is_retryable_order(httpx.ConnectError("x"))
    assert not _is_retryable_order(httpx.ReadTimeout("x"))  # may already be live: must not re-send
    assert not _is_retryable_order(KotakAPIError("boom", 503))
    assert not _is_retryable_order(KotakOrderUncertainError("?"))


def test_quote_retry_ignores_business_and_auth_errors():
    assert not _is_retryable(KotakAuthError("401", 401))
    assert not _is_retryable(KotakBusinessError("nope", 400))
    assert not _is_retryable(KotakAPIError("parse error"))  # status 0
    assert _is_retryable(KotakAPIError("server", 503))
    assert _is_retryable(KotakRateLimitError("429", 429))


def test_breaker_ignores_non_outage_errors():
    from src.broker.circuit_breaker import kotak_orders_breaker
    assert not kotak_orders_breaker.is_system_error(KotakAuthError("401", 401))
    assert kotak_orders_breaker.is_system_error(KotakAPIError("server", 503))


# ── order polling ─────────────────────────────────────────────────────────────
def test_classify():
    assert classify("COMPLETE") == "filled"
    assert classify("rejected") == "rejected"
    assert classify("open pending") == "pending"


async def test_poll_reports_rejection_even_when_raw_is_empty():
    broker = ScriptedBroker({"A": {"status": "rejected", "rejection_reason": "RMS: margin"}})
    res = await poll_order(broker, "A", interval=0, max_attempts=3)
    assert res["outcome"] == "rejected" and "margin" in res["reason"]  # used to be swallowed (IndexError)


async def test_poll_timeout_and_fill():
    assert (await poll_order(ScriptedBroker(), "A", interval=0, max_attempts=2))["outcome"] == "timeout"
    filled = await poll_order(ScriptedBroker({"A": {"status": "complete", "avg_price": "0"}}), "A", interval=0, max_attempts=1)
    assert filled["outcome"] == "filled" and filled["avg_price"] == 0.0


async def test_poll_aborts_on_auth_error_without_assuming_failure():
    class B(ScriptedBroker):
        async def get_order_status(self, order_id):
            raise KotakAuthError("session gone")
    assert (await poll_order(B(), "A", interval=0, max_attempts=3))["outcome"] == "unknown"


# ── indicators / strategies ───────────────────────────────────────────────────
def test_wilder_rsi_bounds_and_flat_series():
    from src.strategies.ema_61_rsi_60 import wilder_rsi
    up = pd.Series(range(1, 60), dtype=float)
    assert wilder_rsi(up).iloc[-1] == 100.0  # no losses -> 100, not NaN/inf
    flat = pd.Series([10.0] * 40)
    assert wilder_rsi(flat).iloc[-1] == 50.0
    mixed = pd.Series([10, 11, 10.5, 12, 11.5, 13, 12.2, 14, 13, 15, 14, 16, 15, 17, 16, 18, 17, 19, 18, 20], dtype=float)
    r = wilder_rsi(mixed).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def _series(jump):
    closes = [100.0]
    for i in range(1, 130):
        closes.append(closes[-1] - 0.05 + (0.6 if i % 2 else -0.6))
    closes.append(closes[-1] + jump)
    return pd.DataFrame({"Close": closes})


def test_ema_breakout_detection():
    from src.strategies.ema_61_rsi_60 import evaluate_frame
    assert evaluate_frame("X", pd.DataFrame({"Close": [1.0] * 10})) is None  # too short
    hit = evaluate_frame("X", _series(2.5))  # last bar closes above EMA61 with RSI crossing 60
    assert hit and hit["symbol"] == "X" and hit["rsi"] > 60 and hit["pct_change"] > 0
    assert evaluate_frame("X", _series(0.2)) is None  # small move: no breakout


def test_oi_strategy_helpers():
    from src.strategies.base import StrategyContext
    from src.strategies.oi_nifty_options import OINiftyOptionsStrategy, compute_pcr, in_entry_window
    from src.utils.market_data import OptionChainEntry, _candidate_expiry_tags

    s = OINiftyOptionsStrategy(StrategyContext("u", "e", "oi_nifty_options", False, {}))
    chain = [OptionChainEntry(24500, 100, 200, 50.0, 0.0, 0, 0, "26SEP")]
    assert compute_pcr(chain) == 2.0
    assert s._get_ltp(chain, 24500, "CE") == 50.0
    assert s._get_ltp(chain, 24500, "PE") is None  # zero price -> None, no invented 100.0
    assert s._get_ltp(chain, 99999, "CE") is None
    assert s._build_symbol("NIFTY", 24600, "CE", "26SEP") == "NIFTY26SEP24600CE"
    assert _candidate_expiry_tags(datetime(2026, 12, 15)) == ["26DEC", "27JAN"]
    assert not in_entry_window(datetime(2026, 9, 26, 11, 0, tzinfo=IST))  # Saturday
    assert in_entry_window(datetime(2026, 9, 30, 11, 0, tzinfo=IST))
    assert not in_entry_window(datetime(2026, 9, 30, 15, 10, tzinfo=IST))
    assert s.max_open_positions == 1 and s.run_interval_minutes == 5


def test_schedule_slots_tolerate_delay_and_dedupe():
    from src.strategies.base import StrategyContext
    from src.strategies.ema_61_rsi_60 import Ema61Rsi60
    s = Ema61Rsi60(StrategyContext("u", "e", "61-ema-60-rsi", False, {}))
    assert s.run_times == ["15:20"]
    assert s.current_slot(datetime(2026, 9, 30, 15, 20, tzinfo=IST))
    assert s.current_slot(datetime(2026, 9, 30, 15, 22, tzinfo=IST))  # delayed pickup still counts
    assert s.current_slot(datetime(2026, 9, 30, 15, 25, tzinfo=IST)) is None
    a = s.current_slot(datetime(2026, 9, 30, 15, 20, tzinfo=IST))
    b = s.current_slot(datetime(2026, 9, 30, 15, 21, tzinfo=IST))
    assert a == b  # same slot id -> the Redis claim makes it run once


def test_crypto_roundtrip_and_node_compat_shape():
    from src.utils.crypto import decrypt, encrypt
    import json
    payload = encrypt("secret")
    assert set(json.loads(payload)) == {"iv", "tag", "data"}
    assert decrypt(payload) == "secret"
