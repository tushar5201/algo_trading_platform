import structlog
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from src.config.settings import settings

log = structlog.getLogger()
_client: AsyncIOMotorClient | None=None

async def connect_db() -> None:
    global _client
    _client = AsyncIOMotorClient(settings.mongo_uri)
    # Ping to verify connection
    await _client.admin.command("ping")
    log.info("mongodb Connected", uri=settings.mongo_uri)

async def disconnect_db() -> None:
    global _client
    if _client:
        _client.close()
        log.info("mongodb.disconnected")


async def get_db() -> AsyncIOMotorDatabase:
    if _client is None:
        raise RuntimeError("MongoDB not connected. Call connect_db() first.")
    return _client.get_database("test")

