from datetime import datetime, timezone
from typing import Optional

import structlog
from bson import ObjectId

from src.db.mongo import get_db

log = structlog.get_logger()
AUDIT_COLLECTION = "auditlogs"

async def write_audit(user_id: str, action: str, metadata: dict, target_id: Optional[str] = None) -> None:
    try:
        db = await get_db()
        now = datetime.now(timezone.utc)
        await db[AUDIT_COLLECTION].insert_one({
            "actorId": ObjectId(user_id),
            "actorRole": "system",
            "action": action,
            "targetId": ObjectId(target_id) if target_id else None,
            "metaData": metadata,
            "createdAt": now,
            "updatedAt": now,
        })
    except Exception as e:
        log.error("audit.write_failed", action=action, error=str(e))
