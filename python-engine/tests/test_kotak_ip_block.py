"""Kotak order APIs reject requests from a non-whitelisted IP (stCode 100008) or from a session created at
another IP (stCode 1037). That must not be mistaken for an expired session."""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from src.broker.circuit_breaker import kotak_orders_breaker
from src.broker.errors import KotakAuthError, KotakBusinessError, KotakIPNotAllowedError
from src.broker.kotak_neo import KotakNeoBroker, KotakNeoSession
from src.utils.retry import _is_retryable
from tests.test_flows import USER, ScriptedBroker, env, make_signal  # noqa: F401  (fixture reuse)

BLOCKED = {"stCode": 100008, "errMsg": "unauthorized", "stat": "Not_Ok"}   # body seen in the real log


def broker_with(handler):
    b = KotakNeoBroker("u1")
    b._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    b._session = KotakNeoSession("tok", "sid", "rid", "https://e21.kotaksecurities.com",
                                 datetime.now(timezone.utc) + timedelta(hours=1))
    return b


async def test_401_100008_is_an_ip_error_and_keeps_the_session():
    b = broker_with(lambda r: httpx.Response(401, json=BLOCKED))
    with pytest.raises(KotakIPNotAllowedError) as ei:
        await b._post_form("/quick/order/rule/ms/place", {})
    assert not isinstance(ei.value, KotakAuthError)
    assert b._session is not None                       # the session is fine: do not drop it
    msg = str(ei.value)
    assert "100008" in msg and "whitelisted" in msg and "Trade API" in msg


async def test_session_ip_mismatch_1037_is_also_an_ip_error():
    body = {"stCode": 1037, "errMsg": "session ip doesnt match with reqest ip", "stat": "Not_Ok"}
    for status in (200, 401, 403):
        b = broker_with(lambda r, s=status: httpx.Response(s, json=body))
        with pytest.raises(KotakIPNotAllowedError) as ei:
            await b._post_form("/quick/order/rule/ms/place", {})
        assert "different IP" in str(ei.value)


async def test_a_real_expired_session_still_means_reauthenticate():
    b = broker_with(lambda r: httpx.Response(401, json={"stCode": 1003, "errMsg": "invalid session", "stat": "Not_Ok"}))
    with pytest.raises(KotakAuthError, match="Session expired"):
        await b._post_form("/quick/order/rule/ms/place", {})
    assert b._session is None


async def test_other_business_rejections_are_not_mistaken_for_an_ip_block():
    b = broker_with(lambda r: httpx.Response(200, json={"stCode": 1007, "errMsg": "body invalid", "stat": "Not_Ok"}))
    with pytest.raises(KotakBusinessError) as ei:
        await b._post_form("/quick/order/rule/ms/place", {})
    assert not isinstance(ei.value, KotakIPNotAllowedError)


def test_ip_error_is_never_retried_and_never_trips_the_breaker():
    err = KotakIPNotAllowedError("blocked", 401)
    assert not _is_retryable(err)
    assert not kotak_orders_breaker.is_system_error(err)


async def test_order_manager_keeps_the_session_and_reports_the_real_cause(env):  # noqa: F811
    db, manager, audits, redis = env

    class Blocked(ScriptedBroker):
        invalidated = False

        async def place_order(self, **kw):
            raise KotakIPNotAllowedError("Kotak rejected the order (code 100008): not whitelisted", 401)

        async def invalidate_session(self):
            self.invalidated = True

    b = Blocked()
    res = await manager.execute(make_signal(), USER, b, {})
    assert res["stage"] == "ip_blocked" and not b.invalidated          # session NOT deleted
    trade = db.trades.docs[0]
    assert trade["status"] == "FAILED" and "100008" in trade["errorMessage"]
    assert "TRADE_ORDER_FAILED" in audits


async def test_worker_stops_the_batch_without_asking_for_a_new_login(monkeypatch):
    from src.queue.worker import StrategyWorker
    w = StrategyWorker()
    seen, notified = [], []

    class FakeOM:
        async def execute(self, signal, user_id, broker, credentials):
            seen.append(signal.symbol)
            return {"success": False, "stage": "ip_blocked", "reason": "IP not whitelisted"}

    async def notify(user_id):
        notified.append(user_id)

    w.order_manager = FakeOM()
    monkeypatch.setattr(w, "_notify_auth_required", notify)
    sigs = [make_signal(symbol=s) for s in ("RUCHINFRA", "HPIL", "QUICKHEAL")]   # the three from the real run
    results = await w._execute_signals(sigs, USER, object(), {})
    assert seen == ["RUCHINFRA"] and len(results) == 1      # one failed trade row, not three
    assert notified == []                                    # re-login cannot fix an IP problem
