import asyncio
import json
import structlog
from src.db.redis_client import get_redis
from src.db.mongo import get_db
from src.broker.kotak_neo import KotakNeoBroker
from src.utils.market_data import init_market_data, get_scrip_master
from bson import ObjectId

log = structlog.get_logger()


class SessionManager:
    """
    Watches Redis for two-step Kotak auth jobs submitted by Node.js.

    Full flow:
    ─────────────────────────────────────────────────────────────────
    Node writes  kotak:auth:step1:{userId}
      → Python calls Kotak TOTP login
      → Python writes kotak:auth:view:{userId}  (view token + view sid)

    Node reads   kotak:auth:view:{userId}  (confirms step1 done)
    Node writes  kotak:auth:step2:{userId}
      → Python calls Kotak MPIN validate
      → Python writes kotak:session:{userId}  (full trading session)
      → Python initializes live market data client
      → Python loads scrip master CSV in background

    Worker reads kotak:session:{userId} before every strategy run.
    ─────────────────────────────────────────────────────────────────
    """

    POLL_INTERVAL_SEC = 2

    def __init__(self):
        self.running = True

    # ── Main loop ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        log.info("session_manager.started")
        while self.running:
            try:
                await self._process_step1()
                await self._process_step2()
            except Exception as e:
                log.error("session_manager.loop_error", error=str(e), exc_info=True)
            await asyncio.sleep(self.POLL_INTERVAL_SEC)

    def stop(self) -> None:
        self.running = False
        log.info("session_manager.stopped")

    # ── Step 1: TOTP login ───────────────────────────────────────────────────

    async def _process_step1(self) -> None:
        """
        Scans for kotak:auth:step1:{userId} keys.
        Calls Kotak TOTP login.
        On success writes kotak:auth:view:{userId}.
        On failure writes kotak:auth:error:{userId}.
        """
        redis = get_redis()
        keys  = [k async for k in redis.scan_iter("kotak:auth:step1:*")]

        for key in keys:
            raw = await redis.get(key)
            if not raw:
                continue

            # Extract userId from key: kotak:auth:step1:{userId}
            user_id = key.split(":")[-1]

            try:
                data = json.loads(raw)

                # Validate required fields
                required = ["accessToken", "mobileNumber", "ucc", "totp"]
                missing  = [f for f in required if not data.get(f)]
                if missing:
                    raise ValueError(f"Missing required fields: {missing}")

                log.info("session_manager.step1_start", user_id=user_id)

                broker = KotakNeoBroker(user_id=user_id)

                result = await broker.totp_login(
                    mobile_number = data["mobileNumber"],
                    ucc           = data["ucc"],
                    totp          = data["totp"],
                    access_token  = data["accessToken"],
                )

                # Write view tokens so Node.js knows step1 is done
                # and can accept the MPIN submission
                await redis.setex(
                    f"kotak:auth:view:{user_id}",
                    600,  # 10 minutes to submit MPIN
                    json.dumps({
                        "view_token":   result["view_token"],
                        "view_sid":     result["view_sid"],
                        "access_token": data["accessToken"],
                    }),
                )

                # Clean up step1 key
                await redis.delete(key)

                log.info(
                    "session_manager.step1_complete",
                    user_id=user_id,
                )

            except Exception as e:
                log.error(
                    "session_manager.step1_error",
                    user_id=user_id,
                    error=str(e),
                    exc_info=True,
                )
                # Store error so Node.js can surface it to the user
                await redis.setex(
                    f"kotak:auth:error:{user_id}",
                    300,  # show error for 5 minutes
                    str(e),
                )
                await redis.delete(key)

    # ── Step 2: MPIN validate ────────────────────────────────────────────────

    async def _process_step2(self) -> None:
        """
        Scans for kotak:auth:step2:{userId} keys.
        Reads view tokens from kotak:auth:view:{userId}.
        Calls Kotak MPIN validate.
        On success:
          - Caches full session in kotak:session:{userId}
          - Initializes live market data client
          - Loads scrip master CSV in background
        On failure writes kotak:auth:error:{userId}.
        """
        redis = get_redis()
        keys  = [k async for k in redis.scan_iter("kotak:auth:step2:*")]

        for key in keys:
            raw = await redis.get(key)
            if not raw:
                continue

            user_id = key.split(":")[-1]

            try:
                data = json.loads(raw)

                if not data.get("mpin"):
                    raise ValueError("MPIN is required")

                # Check view tokens from step1
                view_raw = await redis.get(f"kotak:auth:view:{user_id}")
                if not view_raw:
                    raise Exception(
                        "TOTP step expired or not completed. "
                        "Please submit TOTP again."
                    )

                view_data = json.loads(view_raw)

                # Validate view token fields
                required  = ["view_token", "view_sid", "access_token"]
                missing   = [f for f in required if not view_data.get(f)]
                if missing:
                    raise ValueError(f"Corrupt view data, missing: {missing}")

                log.info("session_manager.step2_start", user_id=user_id)

                broker = KotakNeoBroker(user_id=user_id)

                session = await broker.mpin_validate(
                    mpin         = data["mpin"],
                    view_token   = view_data["view_token"],
                    view_sid     = view_data["view_sid"],
                    access_token = view_data["access_token"],
                )

                # Clean up temp keys
                await redis.delete(key)
                await redis.delete(f"kotak:auth:view:{user_id}")

                log.info(
                    "session_manager.step2_complete",
                    user_id=user_id,
                    base_url=session.base_url,
                    expires_at=session.expires_at.isoformat(),
                )

                # Initialize live market data with this session's credentials
                await self._init_market_data(
                    access_token = view_data["access_token"],
                    base_url     = session.base_url,
                    user_id      = user_id,
                )

            except Exception as e:
                log.error(
                    "session_manager.step2_error",
                    user_id=user_id,
                    error=str(e),
                    exc_info=True,
                )
                await redis.setex(
                    f"kotak:auth:error:{user_id}",
                    300,
                    str(e),
                )
                await redis.delete(key)

    # ── Market data initialization ───────────────────────────────────────────

    async def _init_market_data(
        self,
        access_token: str,
        base_url:     str,
        user_id:      str,
    ) -> None:
        """
        Initializes the live KotakMarketData client and loads scrip master.
        Called after every successful MPIN validation.
        Scrip master loads in background — does not block auth completion.
        """
        from src.config.settings import settings

        if settings.use_mock_broker:
            log.info(
                "session_manager.skip_market_data_init",
                reason="use_mock_broker=true",
            )
            return

        try:
            # Initialize global market data client
            init_market_data(
                access_token = access_token,
                base_url     = base_url,
            )

            log.info(
                "session_manager.market_data_initialized",
                user_id=user_id,
                base_url=base_url,
            )

            # Load scrip master in background
            # Don't await — this takes a few seconds and must not block auth
            scrip = get_scrip_master()
            if scrip:
                asyncio.create_task(
                    self._load_scrip_master_safe(scrip, user_id)
                )

        except Exception as e:
            # Market data init failure must never fail the auth flow
            log.error(
                "session_manager.market_data_init_failed",
                user_id=user_id,
                error=str(e),
            )

    async def _load_scrip_master_safe(self, scrip, user_id: str) -> None:
        """
        Loads scrip master CSV safely in background.
        Errors are logged but never propagated.
        """
        try:
            log.info("session_manager.scrip_master_loading", user_id=user_id)
            await scrip.load()
            log.info(
                "session_manager.scrip_master_loaded",
                user_id=user_id,
            )
        except Exception as e:
            log.error(
                "session_manager.scrip_master_load_failed",
                user_id=user_id,
                error=str(e),
            )

    # ── Session restore (called by worker on startup) ────────────────────────

    async def restore_sessions(self) -> None:
        """
        On Python engine startup, check if any valid sessions exist in Redis
        and restore market data client for the first valid one found.
        Called once from main.py before worker starts.
        """
        from src.config.settings import settings

        if settings.use_mock_broker:
            log.info("session_manager.restore_skipped", reason="use_mock_broker=true")
            return

        redis = get_redis()

        session_keys = [k async for k in redis.scan_iter("kotak:session:*")]

        if not session_keys:
            log.info("session_manager.no_sessions_to_restore")
            return

        for key in session_keys:
            raw = await redis.get(key)
            if not raw:
                continue

            try:
                session_data = json.loads(raw)
                base_url     = session_data.get("base_url", "")
                user_id      = key.split(":")[-1]

                if not base_url:
                    continue

                # Get access token from user's stored credentials in MongoDB
                access_token = await self._get_access_token(user_id)
                if not access_token:
                    continue

                init_market_data(
                    access_token = access_token,
                    base_url     = base_url,
                )

                log.info(
                    "session_manager.session_restored",
                    user_id=user_id,
                    base_url=base_url,
                )

                # Load scrip master for first valid session
                scrip = get_scrip_master()
                if scrip:
                    asyncio.create_task(
                        self._load_scrip_master_safe(scrip, user_id)
                    )

                # Only need one valid session to init market data
                return

            except Exception as e:
                log.warning(
                    "session_manager.restore_error",
                    key=key,
                    error=str(e),
                )
                continue

        log.info("session_manager.no_valid_sessions_found")

    async def _get_access_token(self, user_id: str) -> str | None:
        """
        Gets the Kotak API access token from the user's
        encrypted credentials stored in MongoDB.
        """
        try:
            from src.utils.crypto import decrypt

            db   = await get_db()
            user = await db.users.find_one({"_id": ObjectId(user_id)})

            if not user or not user.get("encryptedCredentials"):
                return None

            creds = json.loads(decrypt(user["encryptedCredentials"]))
            # apiKey is the NEO dashboard access token
            return creds.get("apiKey")

        except Exception as e:
            log.warning(
                "session_manager.get_token_failed",
                user_id=user_id,
                error=str(e),
            )
            return None