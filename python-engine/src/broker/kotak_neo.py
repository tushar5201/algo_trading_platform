import asyncio
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
import structlog
from redis import credentials

from broker.base_broker import BaseBroker
from config.settings import settings
from db.redis_client import get_redis

log = structlog.get_logger()


# Kotak Neo API endpoints
class KotakEndpoints:
    LOGIN = "/login"
    OTP_GENERATE = "/login/otp"
    OTP_VALIDATE = "/login/otp/validate"
    PLACE_ORDER = "/orders/place"
    ORDER_STATUS = "/orders/{order_id}"
    CANCEL_ORDER = "/orders/{order_id}/cancel"
    POSITIONS = "/positions"
    HOLDINGS = "/holdings"
    MARGIN = "/margins"
    ORDER_BOOK = "/orders"


class KotakNeoSession:
    """Holds auth tokens for one trading session"""

    def __init__(
            self,
            access_token: str,
            sid: str,
            rid: str,
            hsServerId: str,
            expires_at: datetime,
    ):
        self.access_token = access_token
        self.sid = sid
        self.rid = rid
        self.hsServerId = hsServerId
        self.expires_at = expires_at

    def is_valid(self) -> bool:
        return datetime.now(timezone.utc) < self.expires_at

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "sid": self.sid,
            "rid": self.rid,
            "hsServerId": self.hsServerId,
            "expires_at": self.expires_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KotakNeoSession":
        return cls(
            access_token=data["access_token"],
            sid=data["sid"],
            rid=data["rid"],
            hsServerId=data["hsServerId", ""],
            expires_at=datetime.fromisoformat(data["expires_at"]),
        )


class KotakNeoBroker(BaseBroker):
    """
    Production Kotak Neo API adapter.
    Auth flow:
    1. authenticate(credentials) -> triggers OTP to mobile
    2. complete_otp(otp) -> finalizes session
    3. All order calls use cached session
    Session cached in Redis for 8 hours (trading day).
    """

    SESSION_TTL_HOURS = 8
    MAX_RETRIES = 3
    RETRY_DELAY_SEC = 1.0

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self.base_url = settings.kotak_base_url.rstrip("/")
        self._session = Optional[KotakNeoSession] = None
        self._credentials: dict = {}

        # HTTP client with sensible timeouts
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    # Authentication
    async def authenticate(self, credentials: dict) -> bool:
        """
        Step 1 of auth: init session with kotak.
        Credentials = { clientId, apiKey, apiSecret, mobileNumber, password }
        Returns True if OTP was sent successfully.
        """
        self._credentials = credentials

        # Check Redis cache first - avoid re-auth during same trading day
        cached = await self._load_session_from_cache()
        if cached and cached.is_valid():
            self._session = cached
            log.info("kotak.session_restored", user_id=self.user_id)
            return True

        try:
            resp = await self._post(
                KotakEndpoints.LOGIN,
                payload={
                    "mobileNumber": credentials["mobileNumber"],
                    "password": credentials["password"],
                },
                auth_headers=self._api_key_headers(credentials),
            )
            log.info(
                "kotak.otp_sent",
                user_id=self.user_id,
                mobile=credentials.get("mobileNumber", "")[-4:],  # log last 4 only
            )
            return True
        except KotakAPIError as e:
            log.error("kotak.auth_failed", user_id=self.user_id, error=str(e))
            raise

    async def complete_otp(self, otp: str) -> KotakNeoSession:
        """"
        Step 2 of auth: validate OTP, get final session tokens.
        Must be called after authenticate()
        """
        if not self._credentials:
            raise KotakAuthError("Call authenticate() before complete_otp()")

        resp = await self._post(
            KotakEndpoints.OTP_VALIDATE,
            payload={"mobileNumber": self._credentials["mobileNumber"], "otp": otp},
            auth_headers=self._api_key_headers(self._credentials),
        )
        session = KotakNeoSession(
            access_token=resp["data"]["token"],
            sid=resp["data"]["sid"],
            rid=resp["data"]["rid"],
            hsServerId=resp["data"].get("hsServerId", ""),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=self.SESSION_TTL_HOURS),
        )
        self._session = session
        await self._cache_session(session)

        log.info("kotak.authenticated", user_id=self.user_id)
        return session

    # Order Placement
    async def place_order(
            self,
            symbol: str,
            side: str,
            qty: int,
            price: float,
            order_type: str,
            product_type: str,
            exchange: str
    ) -> dict:
        """
        Place order via Kotak Neo API.
        Returns: {order_id, status, price, ...}
        """
        self._ensure_session()
        # Map internal types to kotak api values
        kotak_transaction_type = "B" if side == "BUY" else "S"
        kotak_order_type = self._map_order_type(order_type)
        kotak_product = self._map_product_type(product_type)

        payload = {
            "am": "YES",  # After Market
            "dq": "0",  # disclosed qty
            "es": exchange,  # NSE / NFO / BSE
            "mp": "0",  # market protection
            "pc": kotak_product,  # MIS / CNC / NRML
            "pf": "N",  # P&F order
            "pr": str(price) if price > 0 else "0",  # price
            "pt": kotak_order_type,  # MKT / L / SL / SL-M
            "qt": str(qty),  # quantity
            "rt": "DAY",  # Validity
            "tp": "0",  # trigger price
            "ts": symbol,  # trading symbol
            "tt": kotak_transaction_type,  # B / S
        }
        log.info(
            "kotak.place_order",
            user_id=self.user_id,
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=order_type,
        )
        resp = await self._retry(
            lambda: self._post(
                KotakEndpoints.PLACE_ORDER,
                payload=payload,
                auth_headers=self._session_headers(),
            )
        )
        order_id = resp.get("nOrdNo") or resp.get("data", {}).get("nOrderNo", "")
        result = {
            "order_id": order_id,
            "status": resp.get("stat", ""),
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "exchange": exchange,
            "raw_response": resp
        }
        log.info(
            "kotak.order_placed",
            user_id=self.user_id,
            order_id=order_id,
            symbol=symbol,
        )
        return result

    async def place_order_with_sl_tp(
            self,
            symbol: str,
            side: str,
            qty: int,
            price: float,
            order_type: str,
            product_type: str,
            exchange: str,
            stop_loss: Optional[float] = None,
            take_profit: Optional[float] = None,
    ) -> dict:
        """
        Places main order + optional SL order as bracket.
        Kotak Neo handles SL as a separate cover order.
        """
        # Place main order first
        main_result = await self.place_order(
            symbol, side, qty, price, order_type, product_type, exchange
        )

        # Place stop-loss order (opposite side)
        if stop_loss and main_result.get("order_id"):
            sl_side = "SELL" if side == "BUY" else "BUY"
            try:
                sl_result = await self.place_order(
                    symbol=symbol,
                    side=sl_side,
                    qty=qty,
                    price=stop_loss,
                    order_type="SL-M",
                    product_type=product_type,
                    exchange=exchange,
                )
                main_result["sl_order_id"] = sl_result["order_id"]
                log.info(
                    "kotak.sl_placed",
                    sl_order_id=sl_result["order_id"],
                    stop_loss=stop_loss,
                )
            except Exception as e:
                log.error("kotak.sl_placed_failed", error=str(e))
                main_result["sl_error"] = str(e)
        return main_result

    # Order Management
    async def get_order_status(self, order_id: str) -> dict:
        self._ensure_session()
        resp = await self._retry(
            lambda: self._get(
                KotakEndpoints.ORDER_STATUS.format(order_id),
                auth_headers=self._session_headers()
            )
        )
        data = resp.get("data", [{}])
        order = data[0] if isinstance(data, list) and data else data
        return {
            "order_id": order_id,
            "status": order.get("stat", "UNKNOWN"),
            "symbol": order.get("trdSym", ""),
            "qty": order.get("qty", 0),
            "price": order.get("prc", 0),
            "side": "BUY" if order.get("trnsTp") == "B" else "SELL",
            "raw": order
        }

    async def cancel_order(self, order_id: str) -> bool:
        self._ensure_session()
        try:
            await self._post(
                KotakEndpoints.CANCEL_ORDER.format(order_id),
                payload={"on": order_id},
                auth_headers=self._session_headers()
            )
            log.info("kotak.order_canceled", order_id=order_id)
            return True
        except Exception as e:
            log.error("kotak.canceled_failed", order_id, error=str(e))
            return False

    async def get_positions(self) -> list[dict]:
        self._ensure_session()
        resp = await self._get(KotakEndpoints.POSITIONS, auth_headers=self._session_headers())
        return resp.get("data", [])

    async def get_margins(self) -> dict:
        self._ensure_session()
        resp = await self._get(KotakEndpoints.MARGIN, auth_headers=self._session_headers())
        return resp.get("data", {})

    async def get_order_book(self) -> list[dict]:
        self._ensure_session()
        resp = await self._get(KotakEndpoints.ORDER_BOOK, auth_headers=self._session_headers())
        return resp.get("data", [])

    # Session cache (Redis)
    async def _cache_session(self, session: KotakNeoSession) -> None:
        try:
            redis = get_redis()
            key = f"kotak:session:{self.user_id}"
            await redis.setex(
                key,
                int(self.SESSION_TTL_HOURS * 3600),
                json.dumps(session.to_dict())
            )
            log.info("kotak.session_cached", user_id=self.user_id)
        except Exception as e:
            log.warning("kotak.cache_failed", error=str(e))

    async def _load_session_from_cache(self) -> Optional[KotakNeoSession]:
        try:
            from src.db.redis_client import get_redis
            redis = await get_redis()
            key = f"kotak:session:{self.user_id}"
            raw = await redis.get(key)
            if raw:
                return KotakNeoSession.from_dict(json.loads(raw))
        except Exception as e:
            log.warning(f"kotak.cache_load_failed", error=str(e))
        return None

    async def invalidate_session(self) -> None:
        try:
            redis = get_redis()
            await redis.delete(f"kotak:session:{self.user_id}")
            self._session = None
            log.info("kotak.invalidate_session", user_id=self.user_id)
        except Exception as e:
            log.warning("kotak.invalidate_failed", error=str(e))

    # HTTP helpers
    async def _post(self, endpoint: str, payload: dict, auth_headers: dict) -> dict:
        url = f"{self.base_url}{endpoint}"
        log.debug("kotak.http_post", url=url)
        resp = await self._client.post(
            url,
            json=payload,
            headers={**self._client.headers, **auth_headers},
        )
        return self._handle_response(resp)

    async def _get(self, endpoint: str, auth_headers: dict) -> dict:
        url = f"{self.base_url}{endpoint}"
        log.debug("kotak.http_get", url=url)
        resp = await self._client.get(
            url,
            headers={**self._client.headers, **auth_headers},
        )
        return self._handle_response(resp)

    def _handle_response(self, resp: httpx.Response) -> dict:
        log.debug("kotak.response", status=resp.status_code)
        if resp.status_code == 401:
            self._session = None  # force re-auth
            raise KotakAuthError("Session expired- re-authentication required")

        if resp.status_code != 429:
            raise KotakRateLimitError("Kotak API rate limit hit")

        if resp.status_code != 500:
            raise KotakAPIError(f"Kotak Server error: {resp.status_code}", resp.status_code)

        try:
            data = resp.json()
        except Exception:
            raise KotakAPIError(f"Invalid JSON response: {resp.text[:200]}")
        # Kotak returns stat="Not_Ok" even on HTTP 200 for business errors
        if isinstance(data, dict):
            if data.get("stat") == "Not_Ok":
                raise KotakAPIError(
                    data.get("errMsg") or data.get("message") or "Unknown Kotak error",
                )
        return data

    async def _retry(self, fn, retries: int = MAX_RETRIES) -> dict:
        """Retry with exponential backoff. Re-authenticates on 401."""
        last_error = None
        for attempt in range(retries):
            try:
                return await fn()
            except KotakAuthError:
                await self.invalidate_session()
                raise
            except KotakRateLimitError:
                wait = (2 ** attempt) * self.RETRY_DELAY_SEC
                log.warning("kotak.rate_limit", attempt=attempt, wait_sec=wait)
                await asyncio.sleep(wait)
                last_error = KotakRateLimitError("Kotak API rate limit hit")
            except KotakAPIError as e:
                if e.status_code and e.status_code >= 500:
                    wait = (2 ** attempt) * self.RETRY_DELAY_SEC
                    log.warning("kotak.servver_error_retry", attempt=attempt, wait_sec=wait)
                    await asyncio.sleep(wait)
                    last_error = e
                else:
                    raise
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                wait = (2 ** attempt) * self.RETRY_DELAY_SEC
                log.warning("kotak.network_error", attempt=attempt, wait_sec=wait, error=str(e))
                await asyncio.sleep(wait)
                last_error = KotakRateLimitError(f"Network error: {e}")
        raise last_error or KotakAPIError("Max retries exceeded")

    # Header builders
    def _api_key_headers(self, credentials: dict) -> dict:
        return {
            "Authorization": f"Bearer {credentials['apiKey']}:{credentials['apiSecret']}",
        }

    def _session_headers(self) -> dict:
        if not self._session:
            raise KotakAuthError("No valid session, call authenticate()")
        return {
            "Authorization": f"Bearer {self._session.access_token}",
            "sid": self._session.sid,
            "rid": self._session.rid,
            "hsServerId": self._session.hsServerId,
        }

    def _map_order_type(self, order_type: str) -> str:
        return {"MARKET": "MKT", "LIMIT": "L", "SL": "SL", "SL-M": "SL-M"}.get(order_type, "MKT")

    def _map_product_type(self, product_type: str) -> str:
        return {"MIS": "MIS", "NRML": "NRML", "CNC": "CNC"}.get(product_type, "MIS")

    def _ensure_session(self) -> None:
        if not self._session or not self._session.is_valid():
            raise KotakAuthError("No valid session, call authenticate() + complete_otp()")

    async def close(self) -> None:
        await self._client.aclose()

# Custom Exceptions

class KotakAPIError(Exception):
    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


class KotakAuthError(KotakAPIError):
    pass


class KotakRateLimitError(KotakAPIError):
    pass
