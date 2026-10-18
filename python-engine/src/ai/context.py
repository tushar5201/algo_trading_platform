from src.ai.sanitizer import build_facts, holding_minutes, safe_name

HISTORY = 50
RECENT = 3

async def build_context(db, doc: dict, kind: str) -> dict:
    ctx = {"this": build_facts(doc, kind)}
    cursor = db["trades"].find({
            "userId": doc["userId"],
            "strategyName": doc.get("strategyName"),
            "status": "CLOSED",
            "_id": {"$ne": doc["_id"]},
        }).sort("closedAt", -1).limit(HISTORY)
    past = [d for d in await cursor.to_list(HISTORY) if isinstance(d.get("pnl"), (int, float))]
    if not past:
        return ctx
    pnls = [float(d["pnl"]) for d in past]
    wins, holds = [p for p in pnls if p > 0], [h for h in map(holding_minutes, past) if h is not None]
    reasons : dict = {}
    for d in past:
        r = safe_name(d.get("exitReason") or "UNKNOWN", 40)
        reasons[r] = reasons.get(r, 0) + 1
    ctx["strategy_history"] = {
        "closed_trades": len(past), "win_rate_pct": round(len(wins) / len(past) * 100, 1),
        "total_pnl": round(sum(pnls), 2), "avg_pnl": round(sum(pnls) / len(past), 2),
        "avg_holding_minutes": round(sum(holds) / len(holds), 1) if holds else None,
        "exit_reasons": reasons,
    }
    ctx["recent_trades"] = [{k: v for k, v in build_facts(d, "trade").items()
                             if k in ("symbol", "side", "entry_price", "exit_price", "pnl", "exit_reason", "indicators")}
                            for d in past[:RECENT]]
    return ctx

