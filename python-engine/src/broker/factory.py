from bson import ObjectId
import json
from src.broker.base_broker import BaseBroker
from src.broker.errors import KotakAuthError
from src.broker.kotak_neo import KotakNeoBroker
from src.broker.mock_broker import MockBroker
from src.config.settings import settings
from src.db.mongo import get_db
from src.utils.crypto import decrypt


async def create_broker(user_id: str, credentials: dict) -> BaseBroker:
    if settings.use_mock_broker:
        broker = MockBroker(fail_rate=0.0)
        await broker.authenticate(credentials)
        return broker

    broker = KotakNeoBroker(user_id)
    try:
        if not await broker.authenticate(credentials):
            raise KotakAuthError("No active Kotak session. User must complete TOTP → MPIN login.")
    except BaseException:
        await broker.close()
        raise
    return broker


async def load_user_credentials(user_id: str) -> dict:
    db = await get_db()
    user = await db.users.find_one({"_id": ObjectId(user_id)}, {"encryptedCredentials": 1})
    if not user or not user.get("encryptedCredentials"):
        raise KotakAuthError(f"No credentials found for user {user_id}")
    return json.loads(decrypt(user["encryptedCredentials"]))
