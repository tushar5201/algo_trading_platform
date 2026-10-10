"""Regression tests for bugs that unit tests with a stubbed RiskManager / no real imports missed."""
import asyncio
import inspect

import pytest
from bson import ObjectId

import src.execution.risk_manager as rm
from src.execution.signal import Signal, SignalAction
from tests.fakes import FakeCollection, FakeDB
from tests.test_flows import USER, make_signal


def test_every_module_imports_via_src_package():
    # bare `from broker...` imports only work inside an IDE source root, not in Docker
    import pkgutil, importlib, src
    for m in pkgutil.walk_packages(src.__path__, "src."):
        importlib.import_module(m.name)


async def test_real_risk_manager_runs_and_honours_user_limits(monkeypatch):
    uid = ObjectId()

    class Trades(FakeCollection):
        async def count_documents(self, q):
            return 3 if "createdAt" in q and "symbol" not in q and "strategyName" not in q else 0

        def aggregate(self, p):
            class C:
                async def to_list(s, n):
                    return []
            return C()

    db = FakeDB(users=FakeCollection([{"_id": uid, "riskLimits": {"maxDailyTrades": 3, "maxDailyLoss": 1000}}]), trades=Trades())

    async def get_db():
        return db

    monkeypatch.setattr(rm, "get_db", get_db)
    sig = Signal(action=SignalAction.BUY, symbol="X", qty=1, price=100.0, strategy_name="s")
    res = await rm.RiskManager().check(sig, str(uid))  # used to raise AttributeError (_reference_price)
    assert not res.passed and "Daily trades limit" in res.reason  # user cap (3) honoured, not platform 50


def test_reference_price_falls_through_to_ltp():
    sig = Signal(action=SignalAction.BUY, symbol="X", qty=1, price=0.0, metadata={"entry_price": "bad", "ltp": 55})
    assert rm.reference_price(sig) == 55.0


def test_monitor_calls_methods_that_exist():
    from src.execution.sl_tp_monitor import SLTPMonitor
    assert hasattr(SLTPMonitor, "_recover_stale_exits") and not hasattr(SLTPMonitor, "_recover_stale_exists")
    assert "_recover_stale_exits" in inspect.getsource(SLTPMonitor._check_all_positions)


def test_worker_stop_event_is_created():
    from src.queue.worker import StrategyWorker
    w = StrategyWorker()
    assert isinstance(w._stop_event, asyncio.Event)


def test_order_instrument_resolution_checks_the_scrip_master_not_the_signal(monkeypatch):
    import src.execution.order_manager as om

    class Scrip:
        _loaded = True
        def resolve_trading_symbol(self, s): return s + "-EQ"
        def get_token(self, s): return None
        def resolve(self, s): return ("nse_cm", "1234")

    monkeypatch.setattr(om, "get_scrip_master", lambda: Scrip())
    sym, seg, tok = om.OrderManager()._resolve_instrument(Signal(action=SignalAction.BUY, symbol="RELIANCE", qty=1, price=0.0))
    assert (sym, seg, tok) == ("RELIANCE-EQ", "nse_cm", "1234")


async def test_health_queue_counts_use_bull_prefix_and_zset_commands():
    from src.health.checker import _queue_counts
    calls = []

    class R:
        async def llen(self, k): calls.append(("llen", k)); return 0
        async def zcard(self, k): calls.append(("zcard", k)); return 0

    await _queue_counts(R(), "strategy-runner")
    assert ("llen", "bull:strategy-runner:wait") in calls
    assert ("zcard", "bull:strategy-runner:failed") in calls and ("zcard", "bull:strategy-runner:delayed") in calls


def test_audit_documents_match_the_node_schema():
    src_code = inspect.getsource(__import__("src.db.audit", fromlist=["x"]))
    for field in ('"actorId"', '"actorRole"', '"metaData"'):
        assert field in src_code


def test_session_manager_reads_the_real_credentials_field():
    import src.broker.session_manager as sm
    code = inspect.getsource(sm)
    assert "encryptedCredentials" in code and "encrypted_credentials" not in code


def test_oi_strategy_bearish_branch_builds_a_signal_and_helpers_are_safe():
    from src.strategies.base import StrategyContext
    from src.strategies.oi_nifty_options import OINiftyOptionsStrategy
    from src.utils.market_data import OptionChainEntry
    s = OINiftyOptionsStrategy(StrategyContext("u", "e", "oi_nifty_options", False, {}))
    chain = [OptionChainEntry(24400.0, 100, 100, 10.0, 10.0, 0, 0, "26SEP")]
    sig = s._build_signal(chain, "26SEP", 24400.0, "PE", 0.8, 0.5, 24500.0, 0.3, 24500.0, "bearish")
    assert sig.action == SignalAction.BUY and sig.symbol == "NIFTY26SEP24400PE"
    assert s._lot_size("UNKNOWN") > 0  # scrip master absent -> fallback, no TypeError on None
    assert len(s._build_symbol("NIFTY", 24500, "CE")) == len("NIFTY26SEP24500CE")  # %y%b, not %Y%b


def test_order_retry_policy_matches_safety_rules():
    import httpx
    from src.broker.errors import KotakOrderUncertainError, KotakRateLimitError
    from src.utils.retry import _is_retryable_order
    assert not _is_retryable_order(KotakOrderUncertainError("?"))  # would double-place the order
    assert _is_retryable_order(KotakRateLimitError("429")) and _is_retryable_order(httpx.ConnectError("x"))


def test_market_data_module_uses_stdlib_datetime_and_contextvars():
    import src.utils.market_data as md
    import contextvars, datetime
    assert md.contextvars is contextvars and md.datetime is datetime.datetime


async def test_scrip_master_ensure_fresh_reloads_without_crashing():
    import src.utils.market_data as md
    sm = md.ScripMaster("tok", "https://x.test")
    calls = []

    async def fake_load():
        calls.append(1)
        sm._cache = {"A": {}}
        sm._loaded = True
        sm.loaded_on = md.datetime.now(md.IST).strftime("%Y-%m-%d")

    sm.load = fake_load
    await sm.ensure_fresh()   # used to raise AttributeError: 'ScripMaster' has no attribute 'loaded'
    await sm.ensure_fresh()   # same IST day: must NOT reload again
    assert calls == [1]


# ── manual trigger / schedule gate / log throttling ───────────────────────────
async def test_admin_manual_trigger_bypasses_the_schedule_gate(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from src.strategies.base import StrategyContext
    from src.strategies.ema_61_rsi_60 import Ema61Rsi60

    async def claim(self, slot):
        return True

    async def analyze_many(self, snapshots):
        return [self._hold("analysed")]

    monkeypatch.setattr(Ema61Rsi60, "_claim_schedule_slot", claim)
    monkeypatch.setattr(Ema61Rsi60, "analyze_many", analyze_many)
    # a time that is definitely not 15:20-15:22 IST
    assert Ema61Rsi60(StrategyContext("u", "e", "61-ema-60-rsi", True, {})).current_slot(
        datetime(2026, 10, 8, 10, 52, tzinfo=ZoneInfo("Asia/Kolkata"))) is None

    scheduled = Ema61Rsi60(StrategyContext("u", "e", "61-ema-60-rsi", True, {}))
    forced = Ema61Rsi60(StrategyContext("u", "e", "61-ema-60-rsi", True, {}, force=True))
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    if scheduled.current_slot(now_ist) is None:  # almost always true
        assert (await scheduled.run({}))[0].reason == "Not Scheduled to run at this time"
        assert (await forced.run({}))[0].reason == "analysed"


def test_monitor_warning_is_throttled_per_user():
    from src.execution.sl_tp_monitor import SLTPMonitor, WARN_EVERY_SEC
    m = SLTPMonitor()
    assert m._should_warn("no_session:u1", now=1000.0)
    assert not m._should_warn("no_session:u1", now=1010.0)      # 10 s later: suppressed
    assert m._should_warn("no_session:u2", now=1010.0)          # other user: separate
    assert m._should_warn("no_session:u1", now=1000.0 + WARN_EVERY_SEC)


# ── OI strategy: direction must follow the PCR ────────────────────────────────
def _oi_strategy(monkeypatch, put_oi, call_oi):
    import src.strategies.oi_nifty_options as oi
    from src.strategies.base import StrategyContext
    from src.utils.market_data import MarketSnapshot, OptionChainEntry

    atm = 22450.0
    chain = [OptionChainEntry(atm + 50 * k, call_oi, put_oi, 200.0, 200.0, 0, 0, "26OCT") for k in range(-5, 6)]

    async def fake_chain(*a, **k):
        return chain

    monkeypatch.setattr(oi, "_fetch_live_option_chain", fake_chain)
    monkeypatch.setattr(oi, "in_entry_window", lambda *a, **k: True)
    monkeypatch.setattr(oi, "get_market_data", lambda: object())
    monkeypatch.setattr(oi, "get_scrip_master", lambda: None)
    s = oi.OINiftyOptionsStrategy(StrategyContext("u", "e", "oi_nifty_options", False, {}))
    snap = {"NIFTY": MarketSnapshot("NIFTY", atm, atm, atm, atm, 0)}
    return s, snap


async def test_high_pcr_buys_a_call_and_low_pcr_buys_a_put(monkeypatch):
    s, snap = _oi_strategy(monkeypatch, put_oi=1_600, call_oi=1_000)      # PCR 1.6 -> bullish
    sig = await s.analyze(snap)
    assert sig.action == SignalAction.BUY and sig.symbol == "NIFTY26OCT22550CE", sig   # was: a PE
    assert "bullish" in sig.reason

    s, snap = _oi_strategy(monkeypatch, put_oi=500, call_oi=1_000)        # PCR 0.5 -> bearish
    sig = await s.analyze(snap)
    assert sig.action == SignalAction.BUY and sig.symbol == "NIFTY26OCT22350PE", sig   # was: always HOLD
    assert "bearish" in sig.reason

    s, snap = _oi_strategy(monkeypatch, put_oi=1_000, call_oi=1_000)      # PCR 1.0 -> neutral
    assert (await s.analyze(snap)).action == SignalAction.HOLD


# ── EMA/RSI scan: Yahoo rate limiting ─────────────────────────────────────────
def _ema_env(monkeypatch, behaviour, n_symbols=40, max_pauses=2):
    """behaviour(call_no) -> DataFrame to return, or an Exception instance to raise."""
    import src.strategies.ema_61_rsi_60 as ema
    from src.strategies.base import StrategyContext

    calls = {"n": 0}

    class FakeTicker:
        def __init__(self, t):
            self.t = t

        def history(self, **kw):
            calls["n"] += 1
            r = behaviour(calls["n"])
            if isinstance(r, Exception):
                raise r
            return r

    monkeypatch.setattr(ema.yf, "Ticker", FakeTicker)
    monkeypatch.setattr(ema, "_get_all_symbols", lambda: [f"S{i}" for i in range(n_symbols)])
    for name, val in dict(REQUEST_DELAY_SEC=0, RATE_LIMIT_BACKOFF_START=0.05, RATE_LIMIT_BACKOFF_MAX=0.1,
                          MAX_RATE_LIMIT_PAUSES=max_pauses, PER_SYMBOL_RETRIES=2).items():
        monkeypatch.setattr(ema, name, val)
    ema._scan_cache.clear()
    return ema, ema.Ema61Rsi60(StrategyContext("u", "e", "61-ema-60-rsi", False, {})), calls


async def test_rate_limited_scan_is_not_cached_as_no_match(monkeypatch):
    from yfinance.exceptions import YFRateLimitError
    from src.strategies.base import RetryLater
    ema, s, _ = _ema_env(monkeypatch, lambda n: YFRateLimitError())
    with pytest.raises(RetryLater):
        await s._selected()
    assert ema._scan_cache == {}          # used to be {today: []}  -> "No stocks matched" all day


async def test_scan_recovers_after_a_temporary_rate_limit(monkeypatch):
    from yfinance.exceptions import YFRateLimitError
    from tests.test_core import _series
    ema, s, _ = _ema_env(monkeypatch, lambda n: _series(2.5), n_symbols=20, max_pauses=6)
    first_try = set()

    class FlakyTicker:
        """The first request for each of 5 symbols is rate-limited; the retry succeeds."""
        def __init__(self, t):
            self.t = t

        def history(self, **kw):
            if self.t in {f"S{i}.NS" for i in range(5)} and self.t not in first_try:
                first_try.add(self.t)
                raise YFRateLimitError()
            return _series(2.5)

    monkeypatch.setattr(ema.yf, "Ticker", FlakyTicker)
    found = await s._selected()
    assert len(found) == 20 and len(ema._scan_cache) == 1      # every symbol recovered, result cached


async def test_symbols_without_data_do_not_make_a_scan_fail(monkeypatch):
    import pandas as pd
    ema, s, _ = _ema_env(monkeypatch, lambda n: pd.DataFrame())
    assert await s._selected() == []                            # legitimately nothing matched
    assert list(ema._scan_cache.values()) == [[]]


async def test_retry_later_releases_the_slot_and_returns_hold(monkeypatch):
    from src.strategies.base import RetryLater, StrategyContext
    from src.strategies.ema_61_rsi_60 import Ema61Rsi60
    released = []

    async def claim(self, slot): return True
    async def release(self, slot): released.append(slot)
    async def boom(self, snapshots): raise RetryLater("rate limited; will retry")

    monkeypatch.setattr(Ema61Rsi60, "_claim_schedule_slot", claim)
    monkeypatch.setattr(Ema61Rsi60, "_release_schedule_slot", release)
    monkeypatch.setattr(Ema61Rsi60, "analyze_many", boom)
    s = Ema61Rsi60(StrategyContext("u", "e", "61-ema-60-rsi", False, {}, force=True))
    out = await s.run({})
    assert out[0].action == SignalAction.HOLD and "rate limited" in out[0].reason
    assert len(released) == 1                                   # a later tick may try again


# ── market hours ──────────────────────────────────────────────────────────────
def test_market_open_reason_follows_nse_hours():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from src.execution.validator import market_open_reason
    ist = ZoneInfo("Asia/Kolkata")
    at = lambda d, h, m: datetime(2026, 10, d, h, m, tzinfo=ist)
    assert market_open_reason(at(8, 22, 32)) and "22:32" in market_open_reason(at(8, 22, 32))   # your log: Thursday night
    assert market_open_reason(at(8, 9, 14))                  # one minute before the open
    assert market_open_reason(at(8, 9, 15)) is None          # opens
    assert market_open_reason(at(8, 15, 29)) is None
    assert market_open_reason(at(8, 15, 30))                 # closed at 15:30
    assert "weekend" in market_open_reason(at(10, 11, 0))    # Saturday 10 Oct 2026
    assert market_open_reason(at(9, 11, 0)) is None          # Friday 11:00


async def test_validator_blocks_orders_when_market_is_closed(monkeypatch):
    import src.execution.validator as v
    monkeypatch.setattr(v, "market_open_reason", lambda *a, **k: "Market is closed (22:32 IST)")
    monkeypatch.setattr(v.settings, "use_mock_broker", False)
    monkeypatch.setattr(v.settings, "allow_after_hours_orders", False)
    with pytest.raises(v.ValidationError, match="Market is closed"):
        await v.validate_signal(make_signal(), USER)          # raised before any database access
    monkeypatch.setattr(v.settings, "allow_after_hours_orders", True)   # explicit override
    async def no_db():
        raise RuntimeError("reached the database check")
    monkeypatch.setattr(v, "get_db", no_db)
    with pytest.raises(RuntimeError, match="reached the database"):
        await v.validate_signal(make_signal(), USER)
