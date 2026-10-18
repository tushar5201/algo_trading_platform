import re
from datetime import datetime, timezone
from typing import Any, Optional

_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_KEYVAL = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password|passwd|mpin|totp|otp|authorization|ucc)\b"
    r"\s*[:=]\s*\S+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_OBJECTID = re.compile(r"\b[0-9a-fA-F]{24}\b")
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.\- ]")

METADATA_WHITELIST = ("pcr", "atm_strike", "concentration", "ltp", "entry_price", "rank", "pct_change",
                      "rsi", "ema", "ema61", "rsi60")
MAX_TEXT = 200


def sanitize_text(value: Any, max_len: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    s = _CTRL.sub(" ", str(value))
    for pat, repl in ((_JWT, "[redacted]"), (_BEARER, "[redacted]"), (_KEYVAL, "[redacted]"),
                      (_EMAIL, "[redacted]"), (_OBJECTID, "[id]"), (_LONG_TOKEN, "[redacted]"),
                      (_PHONE, "[redacted]")):
        s = pat.sub(repl, s)
    s = s.replace("```", "'''")
    s = " ".join(s.split())
    return s if len(s) <= max_len else s[: max_len - 1].rstrip() + "…"

def safe_name(value: Any, max_len: int = 64) -> str:
    return _SAFE_NAME.sub("", str(value or ""))[:max_len]

def _num(v: Any, nd: int = 2) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return round(float(v), nd)

def _dt(v: Any) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return None

def sanitize_metadata(meta: Any) -> dict:
    out: dict = {}
    if not isinstance(meta, dict):
        return out
    for key in METADATA_WHITELIST:
        if key in meta:
            v = meta[key]
            out[key] = _num(v, 4) if isinstance(v, (int, float)) and not isinstance(v, bool) else sanitize_text(v, 40)
    top = meta.get("top_n")
    if isinstance(top, list):
        out["top_gainers"] = [
            {"symbol": safe_name(t.get("symbol"), 24), "pct_change": _num(t.get("pct_change"))}
            for t in top[:3] if isinstance(t, dict)
        ]
    return {k: v for k, v in out.items() if v not in (None, "")}

def holding_minutes(doc: dict) -> Optional[float]:
    start, end = _dt(doc.get("executedAt")) or _dt(doc.get("createdAt")), _dt(doc.get("closedAt"))
    if start and end and end >= start:
        return round((end - start).total_seconds() / 60, 1)
    return None


def build_facts(doc: dict, kind: str) -> dict:
    facts: dict = {
        "kind": kind,
        "strategy": safe_name(doc.get("strategyName")),
        "symbol": safe_name(doc.get("symbol"), 40),
    }
    if kind == "signal":
        facts.update({
            "action": safe_name(doc.get("action"), 8),
            "confidence": _num(doc.get("confidence"), 3),
            "reason": sanitize_text(doc.get("reason")),
            "stop_loss": _num(doc.get("stopLoss")),
            "take_profit": _num(doc.get("takeProfit")),
            "executed_as_trade": bool(doc.get("autoTraded")),
        })
    else:
        entry, exit_, qty, pnl = _num(doc.get("price")), _num(doc.get("exitPrice")), doc.get("qty"), _num(
            doc.get("pnl"))
        pnl_pct = None
        if pnl is not None and entry and isinstance(qty, (int, float)) and qty:
            pnl_pct = round(pnl / (entry * qty) * 100, 2)
        facts.update({
            "side": safe_name(doc.get("side"), 8),
            "qty": qty if isinstance(qty, (int, float)) and not isinstance(qty, bool) else None,
            "entry_price": entry,
            "exit_price": exit_,
            "stop_loss": _num(doc.get("stopLoss")),
            "take_profit": _num(doc.get("takeProfit")),
            "pnl": pnl,
            "pnl_pct_of_entry_value": pnl_pct,
            "status": safe_name(doc.get("status"), 20),
            "exit_reason": safe_name(doc.get("exitReason"), 40),
            "product_type": safe_name(doc.get("productType"), 8),
            "exchange": safe_name(doc.get("exchange"), 8),
            "holding_minutes": holding_minutes(doc),
            "signal_confidence": _num(doc.get("confidence"), 3),
            "signal_reason": sanitize_text(doc.get("reason")),
        })
        if doc.get("status") in ("FAILED", "NEEDS_REVIEW", "CANCELED"):
            facts["problem"] = sanitize_text(doc.get("errorMessage"))
    meta = sanitize_metadata(doc.get("metadata"))
    if meta:
        facts["indicators"] = meta
    return {k: v for k, v in facts.items() if v not in (None, "")}
