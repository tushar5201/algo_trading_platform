"""Phase 2: Groq client and context builder. httpx.MockTransport only: no network, no real LLM."""
import json
import logging
from datetime import datetime, timezone

import httpx
import pytest
from bson import ObjectId

from src.ai.context import build_context
from src.ai.groq_client import (GroqClient, LLMBadRequest, LLMError, LLMRateLimited, LLMUnavailable)
from tests.ai_fakes import DB, Coll

SECRET_KEY = "gsk_SUPERSECRETKEY1234567890abcdefghijkl"
USER = ObjectId()


def client(handler):
    return GroqClient(SECRET_KEY, "some-model", timeout=5, max_tokens=100, transport=httpx.MockTransport(handler))


OK = {"model": "m-1", "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
      "usage": {"prompt_tokens": 11, "completion_tokens": 5}}


# ── client ───────────────────────────────────────────────────────────────────────────────────
async def test_request_shape_and_response_parsing():
    seen = {}

    def handler(request):
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["authorization"], json.loads(request.content)
        return httpx.Response(200, json=OK)

    r = await client(handler).complete([{"role": "user", "content": "x"}])
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["auth"] == f"Bearer {SECRET_KEY}"
    assert seen["body"]["model"] == "some-model" and seen["body"]["max_tokens"] == 100
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert (r.content, r.tokens_in, r.tokens_out, r.model) == ("{}", 11, 5, "m-1")
    await client(handler).complete([], json_mode=False)
    assert "response_format" not in seen["body"]


async def test_429_raises_with_retry_after_and_makes_exactly_one_request():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(429, headers={"retry-after": "7"}, json={"error": {"message": "slow down"}})

    with pytest.raises(LLMRateLimited) as ei:
        await client(handler).complete([{"role": "user", "content": "hi"}])
    assert ei.value.retry_after == 7 and len(seen) == 1       # the client itself never retries or loops


async def test_429_without_header_defaults_to_30s_and_is_clamped():
    with pytest.raises(LLMRateLimited) as ei:
        await client(lambda r: httpx.Response(429, json={})).complete([])
    assert ei.value.retry_after == 30
    with pytest.raises(LLMRateLimited) as ei:
        await client(lambda r: httpx.Response(429, headers={"retry-after": "0"}, json={})).complete([])
    assert ei.value.retry_after == 1


async def test_http_errors_map_to_typed_errors_without_leaking_the_key():
    cases = ((401, LLMError), (403, LLMError), (400, LLMBadRequest), (422, LLMBadRequest),
             (500, LLMUnavailable), (503, LLMUnavailable))
    for code, exc in cases:
        with pytest.raises(exc) as ei:
            await client(lambda r, c=code: httpx.Response(c, json={"error": {"message": "x"}})).complete([])
        assert type(ei.value) is exc and SECRET_KEY not in str(ei.value)

    def boom(request):
        raise httpx.ReadTimeout("t")
    with pytest.raises(LLMUnavailable):
        await client(boom).complete([])
    with pytest.raises(LLMUnavailable):
        await client(lambda r: httpx.Response(200, json={"nope": 1})).complete([])


async def test_api_key_never_reaches_logs(caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(LLMError):
        await client(lambda r: httpx.Response(401, json={"error": {"message": "bad key"}})).complete([])
    with pytest.raises(LLMRateLimited):
        await client(lambda r: httpx.Response(429, headers={"retry-after": "1"}, json={})).complete([])
    assert SECRET_KEY not in caplog.text


# ── context ──────────────────────────────────────────────────────────────────────────────────
def trade(**kw):
    t = {"_id": ObjectId(), "userId": USER, "strategyName": "oi_nifty_options", "symbol": "NIFTY26SEP24500CE",
         "side": "BUY", "qty": 65, "price": 100.0, "exitPrice": 70.0, "pnl": -1950.0, "status": "CLOSED",
         "exitReason": "STOP_LOSS", "orderId": "ORD-SECRET-1", "encryptedCredentials": "xxx",
         "metadata": {"pcr": 1.6, "userEmail": "a@b.com"},
         "executedAt": datetime(2026, 9, 1, 4, 0, tzinfo=timezone.utc),
         "closedAt": datetime(2026, 9, 1, 5, 30, tzinfo=timezone.utc)}
    t.update(kw)
    return t


async def test_context_is_scoped_to_the_owner_and_has_no_identifiers():
    current = trade()
    other_user = trade(userId=ObjectId(), pnl=999.0)
    other_strategy = trade(strategyName="61-ema-60-rsi", pnl=888.0)
    win = trade(pnl=500.0, closedAt=datetime(2026, 9, 2, tzinfo=timezone.utc))
    db = DB(trades=Coll([current, other_user, other_strategy, win]))
    ctx = await build_context(db, current, "trade")
    h = ctx["strategy_history"]
    assert h["closed_trades"] == 1 and h["total_pnl"] == 500.0 and h["win_rate_pct"] == 100.0   # current + others excluded
    assert [t["pnl"] for t in ctx["recent_trades"]] == [500.0]
    assert ctx["this"]["pnl"] == -1950.0 and ctx["this"]["indicators"]["pcr"] == 1.6
    blob = json.dumps(ctx, default=str)
    for leaked in (str(USER), str(current["_id"]), "a@b.com", "ORD-SECRET-1", "xxx", "encryptedCredentials", "userEmail"):
        assert leaked not in blob


async def test_context_statistics_and_recent_limit():
    current = trade()
    past = [trade(pnl=p, executedAt=datetime(2026, 8, i + 1, 4, 0, tzinfo=timezone.utc),
                  closedAt=datetime(2026, 8, i + 1, 5, 30, tzinfo=timezone.utc))
            for i, p in enumerate((100.0, -50.0, 30.0, -20.0))]
    ctx = await build_context(DB(trades=Coll([current, *past])), current, "trade")
    h = ctx["strategy_history"]
    assert h["closed_trades"] == 4 and h["total_pnl"] == 60.0 and h["win_rate_pct"] == 50.0
    assert h["exit_reasons"] == {"STOP_LOSS": 4} and h["avg_holding_minutes"] == 90.0
    assert len(ctx["recent_trades"]) == 3 and ctx["recent_trades"][0]["pnl"] == -20.0   # newest first


async def test_context_without_history_has_only_this_and_works_for_signals():
    sig = {"_id": ObjectId(), "userId": USER, "strategyName": "oi_nifty_options", "symbol": "X", "action": "BUY",
           "confidence": 0.8, "reason": "PCR=1.6"}
    ctx = await build_context(DB(trades=Coll(), signals=Coll([sig])), sig, "signal")
    assert list(ctx) == ["this"] and ctx["this"]["kind"] == "signal"
