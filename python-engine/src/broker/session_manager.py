import asyncio
import json
import structlog
from src.db.redis_client import get_redis
from src.utils.crypto import decrypt
from src.db.mongo import get_db
from src.broker.kotak_neo import KotakNeoBroker
from bson import ObjectId

log = structlog.get_logger()


class SessionManager:
    """
    Watches Redis for two-step auth jobs submitted by Node.js:

    Key: kotak:auth:step1:{userId}
         Value: { access_token, mobile_number, ucc, totp }
         → Calls Kotak TOTP login → stores view_token + view_sid

    Key: kotak:auth:step2:{userId}
         Value: { mpin }
         → Calls Kotak MPIN validate → stores full session
    """

    def __init__(self):
        self.running = True

    async def start(self) -> None:
        log.info("session_manager.started")
        while self.running:
            try:
                await self._process_step1()
                await self._process_step2()
            except Exception as e:
                log.error("session_manager.error", error=str(e))
            await asyncio.sleep(2)

    async def _process_step1(self) -> None:
        """TOTP login → store view tokens for step 2."""
        redis = get_redis()
        async for key in redis.scan_iter("kotak:auth:step1:*"):
            raw = await redis.get(key)
            if not raw:
                continue
            try:
                data       = json.loads(raw)
                user_id    = key.split(":")[-1]
                broker     = KotakNeoBroker(user_id=user_id)

                result = await broker.totp_login(
                    mobile_number = data["mobileNumber"],
                    ucc           = data["ucc"],
                    totp          = data["totp"],
                    access_token  = data["accessToken"],
                )

                # Store view tokens for step 2 (10 min TTL)
                await redis.setex(
                    f"kotak:auth:view:{user_id}",
                    600,
                    json.dumps({
                        "view_token":   result["view_token"],
                        "view_sid":     result["view_sid"],
                        "access_token": data["accessToken"],
                    }),
                )
                await redis.delete(key)
                log.info("session_manager.step1_complete", user_id=user_id)

            except Exception as e:
                log.error("session_manager.step1_failed", key=key, error=str(e))
                # Store error so Node can surface it to user
                user_id = key.split(":")[-1]
                await redis.setex(
                    f"kotak:auth:error:{user_id}", 300, str(e)
                )
                await redis.delete(key)

    async def _process_step2(self) -> None:
        """MPIN validate → create full trading session."""
        redis = get_redis()
        async for key in redis.scan_iter("kotak:auth:step2:*"):
            raw = await redis.get(key)
            if not raw:
                continue
            try:
                data    = json.loads(raw)
                user_id = key.split(":")[-1]

                # Get view tokens from step 1
                view_raw = await redis.get(f"kotak:auth:view:{user_id}")
                if not view_raw:
                    raise Exception("Step 1 not completed or expired. Re-submit TOTP.")

                view_data = json.loads(view_raw)
                broker    = KotakNeoBroker(user_id=user_id)

                await broker.mpin_validate(
                    mpin         = data["mpin"],
                    view_token   = view_data["view_token"],
                    view_sid     = view_data["view_sid"],
                    access_token = view_data["access_token"],
                )

                # Clean up temp keys
                await redis.delete(key)
                await redis.delete(f"kotak:auth:view:{user_id}")

                log.info("session_manager.step2_complete", user_id=user_id)

            except Exception as e:
                log.error("session_manager.step2_failed", key=key, error=str(e))
                user_id = key.split(":")[-1]
                await redis.setex(f"kotak:auth:error:{user_id}", 300, str(e))
                await redis.delete(key)

    def stop(self) -> None:
        self.running = False