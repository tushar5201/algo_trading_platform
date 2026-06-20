import httpx
import structlog
import json
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urlencode

from aiobreaker import CircuitBreakerError

from src.broker.base_broker import BaseBroker
from src.utils.rate_limiter import kotak_limiter

log = structlog.get_logger()

# ── Fixed login endpoints (never use baseUrl for these) ─────────────────────
LOGIN_BASE = "https://mis.kotaksecurities.com"
TOTP_LOGIN_URL = f"{LOGIN_BASE}/login/1.0/tradeApiLogin"
MPIN_VALIDATE_URL = f"{LOGIN_BASE}/login/1.0/tradeApiValidate"
NEO_FIN_KEY = "neotradeapi"


class KotakNeoSession:
    """
    Holds all tokens from the two-step auth flow.

    Step 1 (TOTP):  view_token + view_sid
    Step 2 (MPIN):  session_token + session_sid + base_url
    Only session_token + session_sid are used for trading APIs.
    """

    def __init__(
            self,
            session_token: str,  # Auth header for all post-login APIs
            session_sid: str,  # Sid header for all post-login APIs
            rid: str,
            base_url: str,  # dynamic — from MPIN validate response
            expires_at: datetime,
    ):
        self.session_token = session_token
        self.session_sid = session_sid
        self.rid = rid
        self.base_url = base_url.rstrip("/")
        self.expires_at = expires_at

    def is_valid(self) -> bool:
        return datetime.now(timezone.utc) < self.expires_at

    def to_dict(self) -> dict:
        return {
            "session_token": self.session_token,
            "session_sid": self.session_sid,
            "rid": self.rid,
            "base_url": self.base_url,
            "expires_at": self.expires_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KotakNeoSession":
        return cls(
            session_token=data["session_token"],
            session_sid=data["session_sid"],
            rid=data["rid"],
            base_url=data["base_url"],
            expires_at=datetime.fromisoformat(data["expires_at"]),
        )


class KotakNeoBroker(BaseBroker):
    """
    Production Kotak Neo broker adapter.

    Auth flow (TOTP — uses Google Authenticator, NOT SMS):
      Step 1: totp_login(mobile, ucc, totp)
              → returns view_token + view_sid

      Step 2: mpin_validate(mpin, view_token, view_sid)
              → returns session_token + session_sid + base_url
              → session cached in Redis for 8 hours

    All order/report APIs:
      Headers: Auth=session_token, Sid=session_sid, neo-fin-key=neotradeapi
      Body:    application/x-www-form-urlencoded  with  jData=<json_string>
    """

    SESSION_TTL_HOURS = 8
    MAX_RETRIES = 3
    RETRY_DELAY_SEC = 1.0

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._session: Optional[KotakNeoSession] = None
        self._access_token: str = ""  # from NEO dashboard — used for login + quotes

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
        )

    # ────────────────────────────────────────────────────────────────────────
    # Public auth methods
    # ────────────────────────────────────────────────────────────────────────

    async def authenticate(self, credentials: dict) -> bool:
        """
        Called by the worker before every strategy run.
        Restores session from Redis cache if still valid.
        Returns True if a valid cached session exists.
        Returns False if user must go through TOTP → MPIN flow.
        """
        self._access_token = credentials.get("apiKey", "")

        cached = await self._load_session_from_cache()
        if cached and cached.is_valid():
            self._session = cached
            log.info("kotak.session_restored", user_id=self.user_id)
            return True

        log.info("kotak.no_valid_session", user_id=self.user_id)
        return False  # worker will raise — user must complete TOTP + MPIN

    async def totp_login(
            self,
            mobile_number: str,
            ucc: str,
            totp: str,
            access_token: str,
    ) -> dict:
        """
        Step 1 of auth.
        mobile_number: "+91XXXXXXXXXX"
        ucc:           client code (e.g. "AB1234")
        totp:          6-digit code from Google Authenticator
        access_token:  from Kotak NEO API dashboard (plain, no Bearer)

        Returns { view_token, view_sid, rid }
        """
        from src.broker.circuit_breaker import kotak_auth_breaker
        self._access_token = access_token
        await kotak_limiter.acquire_auth()

        try:
            resp = await kotak_auth_breaker.call_async(
                self._post_json,
                url=TOTP_LOGIN_URL,
                payload={
                    "mobileNumber": mobile_number,
                    "ucc": ucc,
                    "totp": totp,
                },
                headers={
                    "Authorization": access_token,  # plain token — no Bearer
                    "neo-fin-key": NEO_FIN_KEY,
                    "Content-Type": "application/json",
                },
            )
        except CircuitBreakerError:
            raise KotakAPIError(
                "Kotak auth service is unavailable (circuit open). "
                "Too many recent failures — wait 2 minutes and try again."
            )

        data = resp.get("data", {})
        if data.get("status") == "error" or resp.get("status") == "error":
            raise KotakAuthError(
                resp.get("message") or data.get("message") or "TOTP login failed"
            )

        log.info(
            "kotak.totp_login_success",
            user_id=self.user_id,
            k_type=data.get("kType"),
        )

        return {
            "view_token": data["token"],
            "view_sid": data["sid"],
            "rid": data.get("rid", ""),
        }

    async def mpin_validate(
            self,
            mpin: str,
            view_token: str,
            view_sid: str,
            access_token: str,
    ) -> KotakNeoSession:
        from src.broker.circuit_breaker import kotak_auth_breaker
        """
        Step 2 of auth.
        mpin:        user's 6-digit MPIN
        view_token:  token from Step 1
        view_sid:    sid from Step 1
        access_token: from Kotak NEO dashboard

        Returns KotakNeoSession with session_token, session_sid, base_url.
        """
        await kotak_limiter.acquire_auth()

        try:
            resp = await kotak_auth_breaker.call_async(
                self._post_json,
                url=MPIN_VALIDATE_URL,
                payload={"mpin": mpin},
                headers={
                    "Authorization": access_token,  # plain token — no Bearer
                    "neo-fin-key": NEO_FIN_KEY,
                    "Content-Type": "application/json",
                    "sid": view_sid,
                    "Auth": view_token,
                },
            )
        except CircuitBreakerError:
            raise KotakAPIError(
                "Kotak auth service is unavailable (circuit open). "
                "Too many recent failures — wait 2 minutes and try again."
            )

        data = resp.get("data", {})
        if data.get("status") == "error" or resp.get("status") == "error":
            raise KotakAuthError(
                resp.get("message") or data.get("message") or "MPIN validation failed"
            )

        base_url = data.get("baseUrl", "")
        if not base_url:
            raise KotakAuthError("No baseUrl in MPIN validate response")

        session = KotakNeoSession(
            session_token=data["token"],
            session_sid=data["sid"],
            rid=data.get("rid", ""),
            base_url=base_url,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=self.SESSION_TTL_HOURS),
        )

        self._session = session
        await self._cache_session(session)

        log.info(
            "kotak.mpin_validate_success",
            user_id=self.user_id,
            base_url=base_url,
            expires_at=session.expires_at.isoformat(),
        )
        return session

    # ────────────────────────────────────────────────────────────────────────
    # Order placement — BaseBroker interface
    # ────────────────────────────────────────────────────────────────────────

    async def place_order(
            self,
            symbol: str,  # pTrdSymbol from scrip master e.g. "ITBEES-EQ"
            side: str,  # "BUY" | "SELL"
            qty: int,
            price: float,  # 0.0 = market order
            order_type: str,  # "MARKET" | "LIMIT" | "SL" | "SL-M"
            product_type: str,  # "MIS" | "NRML" | "CNC"
            exchange: str,  # "nse_cm" | "nse_fo" | "bse_cm" etc.
    ) -> dict:
        """
        Place order via Kotak Neo API.

        CRITICAL: body is x-www-form-urlencoded with a single 'jData' field
        containing a JSON string. NOT a JSON POST body.
        """
        from src.broker.circuit_breaker import kotak_orders_breaker
        self._ensure_session()

        j_data = {
            "am": "NO",  # not AMO
            "dq": "0",  # no disclosed qty
            "es": exchange,  # nse_cm / nse_fo etc.
            "mp": "0",  # no market protection
            "pc": product_type,  # MIS / NRML / CNC
            "pf": "N",  # not portfolio order
            "pr": str(price) if price > 0 else "0",  # price
            "pt": self._map_order_type(order_type),  # MKT / L / SL / SL-M
            "qt": str(qty),
            "rt": "DAY",
            "tp": "0",  # trigger price
            "ts": symbol,  # trading symbol
            "tt": "B" if side == "BUY" else "S",
        }

        log.info(
            "kotak.place_order",
            user_id=self.user_id,
            symbol=symbol,
            side=side,
            qty=qty,
            exchange=exchange,
        )
        await kotak_limiter.acquire_orders()
        try:
            resp = await kotak_orders_breaker.call_async(
                self._retry,
                lambda: self._post_form(
                    endpoint="/quick/order/rule/ms/place",
                    j_data=j_data,
                )
            )
        except CircuitBreakerError:
            raise KotakAPIError(
                "Kotak order placement is unavailable (circuit open). "
                "Too many recent failures — wait 1 minute and try again."
            )

        # Success: { "nOrdNo": "...", "stat": "Ok", "stCode": 200 }
        order_id = resp.get("nOrdNo", "")
        if not order_id:
            raise KotakAPIError(
                resp.get("emsg") or "Order placed but no order number returned"
            )

        log.info(
            "kotak.order_placed",
            user_id=self.user_id,
            order_id=order_id,
            symbol=symbol,
            side=side,
        )

        return {
            "order_id": order_id,
            "status": resp.get("stat", ""),
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "exchange": exchange,
            "raw": resp,
        }

    async def place_order_with_sl(
            self,
            symbol: str,
            side: str,
            qty: int,
            price: float,
            order_type: str,
            product_type: str,
            exchange: str,
            stop_loss: Optional[float] = None,
    ) -> dict:
        """
        Places main order + separate SL-M order (opposite side).
        Kotak Neo does not support native bracket orders for all segments,
        so we place two separate orders.
        """
        # Main order
        result = await self.place_order(
            symbol, side, qty, price, order_type, product_type, exchange
        )

        # SL order — opposite side, SL-M type
        if stop_loss and result.get("order_id"):
            sl_side = "SELL" if side == "BUY" else "BUY"
            try:
                sl_result = await self.place_order(
                    symbol=symbol,
                    side=sl_side,
                    qty=qty,
                    price=0.0,  # SL-M = market when triggered
                    order_type="SL-M",
                    product_type=product_type,
                    exchange=exchange,
                )
                result["sl_order_id"] = sl_result.get("order_id")
                log.info("kotak.sl_order_placed", sl_order_id=sl_result.get("order_id"))
            except Exception as e:
                log.error("kotak.sl_order_failed", error=str(e))
                result["sl_error"] = str(e)

        return result

    async def modify_order(
            self,
            order_no: str,
            symbol: str,
            exchange: str,
            side: str,
            qty: int,
            price: float,
            order_type: str,
            product_type: str,
            trigger_price: float = 0.0,
    ) -> dict:
        """Modify an existing open order."""
        from src.broker.circuit_breaker import kotak_orders_breaker
        self._ensure_session()

        j_data = {
            "no": order_no,
            "ts": symbol,
            "es": exchange,
            "tt": "B" if side == "BUY" else "S",
            "qt": str(qty),
            "pr": str(price),
            "tp": str(trigger_price),
            "pt": self._map_order_type(order_type),
            "pc": product_type,
            "mp": "0",
            "dq": "0",
            "vd": "DAY",
            "am": "NO",
        }

        await kotak_limiter.acquire_orders()

        try:
            resp = await kotak_orders_breaker.modify_order(
                self._retry,
                lambda: self._post_form(endpoint="/quick/order/vr/modify", j_data=j_data)
            )
        except CircuitBreakerError:
            raise KotakAPIError(
                "Kotak order modification is unavailable (circuit open)."
            )

        log.info("kotak.order_modified", order_no=order_no, new_order_no=resp.get("nOrdNo"))
        return {"order_id": resp.get("nOrdNo", order_no), "raw": resp}

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a regular pending order."""
        self._ensure_session()

        j_data = {"on": order_id, "am": "NO"}

        try:
            await self._post_form(endpoint="/quick/order/cancel", j_data=j_data)
            log.info("kotak.order_cancelled", order_id=order_id)
            return True
        except KotakAPIError as e:
            log.error("kotak.cancel_failed", order_id=order_id, error=str(e))
            return False

    async def get_order_status(self, order_id: str) -> dict:
        """Fetch order history for a specific order number."""
        self._ensure_session()

        j_data = {"nOrdNo": order_id}

        resp = await self._post_form(
            endpoint="/quick/order/history",
            j_data=j_data,
        )

        data = resp.get("data", [])
        latest = data[0] if data else {}

        return {
            "order_id": order_id,
            "status": latest.get("ordSt", "UNKNOWN"),
            "symbol": latest.get("trdSym", ""),
            "qty": latest.get("qty", 0),
            "avg_price": latest.get("avgPrc", "0"),
            "side": "BUY" if latest.get("trnsTp") == "B" else "SELL",
            "raw": data,
        }

    # ────────────────────────────────────────────────────────────────────────
    # Portfolio / account APIs
    # ────────────────────────────────────────────────────────────────────────

    async def get_order_book(self) -> list[dict]:
        """All orders for today."""
        self._ensure_session()
        resp = await self._get(endpoint="/quick/user/orders")
        return resp.get("data", [])

    async def get_trade_book(self) -> list[dict]:
        """All executed trades for today."""
        self._ensure_session()
        resp = await self._get(endpoint="/quick/user/trades")
        return resp.get("data", [])

    async def get_positions(self) -> list[dict]:
        self._ensure_session()
        resp = await self._get(endpoint="/quick/user/positions")
        return resp.get("data", [])

    async def get_holdings(self) -> list[dict]:
        self._ensure_session()
        resp = await self._get(endpoint="/portfolio/v1/holdings")
        return resp.get("data", [])

    async def get_limits(
            self,
            seg: str = "ALL",
            exch: str = "ALL",
            prod: str = "ALL",
    ) -> dict:
        """Available margin / cash limits."""
        self._ensure_session()
        j_data = {"seg": seg, "exch": exch, "prod": prod}
        return await self._post_form(endpoint="/quick/user/limits", j_data=j_data)

    async def check_margin(
            self,
            exchange: str,
            symbol_token: str,
            price: float,
            qty: int,
            side: str,
            order_type: str,
            product_type: str,
    ) -> dict:
        """Check required margin before placing order."""
        self._ensure_session()
        j_data = {
            "brkName": "KOTAK",
            "brnchId": "ONLINE",
            "exSeg": exchange,
            "prc": str(price),
            "prcTp": self._map_order_type(order_type),
            "prod": product_type,
            "qty": str(qty),
            "tok": symbol_token,
            "trnsTp": "B" if side == "BUY" else "S",
        }
        return await self._post_form(endpoint="/quick/user/check-margin", j_data=j_data)

    async def get_quotes(self, queries: list[str]) -> list[dict]:
        """
        Live market quotes.
        queries: list of "exchange|symbol" e.g. ["nse_cm|11536", "nse_fo|Nifty 50"]

        Uses access_token (not session_token) — no neo-fin-key, no Auth/Sid.
        """
        from src.broker.circuit_breaker import kotak_quotes_breaker

        query_str = ",".join(queries)
        url = f"{self._session.base_url}/script-details/1.0/quotes/neosymbol/{query_str}/all"

        try:
            resp = await kotak_quotes_breaker.call_async(
                self._client.get,
                url,
                headers={"Authorization": self._access_token},
            )
        except CircuitBreakerError:
            raise KotakAPIError(
                "Kotak quotes API is unavailable (circuit open). "
                "Too many recent failures."
            )
        self._handle_response(resp)
        return resp.json()

    # ────────────────────────────────────────────────────────────────────────
    # Session cache
    # ────────────────────────────────────────────────────────────────────────

    async def _cache_session(self, session: KotakNeoSession) -> None:
        try:
            from src.db.redis_client import get_redis
            redis = get_redis()
            key = f"kotak:session:{self.user_id}"
            await redis.setex(
                key,
                int(self.SESSION_TTL_HOURS * 3600),
                json.dumps(session.to_dict()),
            )
            log.info("kotak.session_cached", user_id=self.user_id)
        except Exception as e:
            log.warning("kotak.cache_failed", error=str(e))

    async def _load_session_from_cache(self) -> Optional[KotakNeoSession]:
        try:
            from src.db.redis_client import get_redis
            redis = get_redis()
            raw = await redis.get(f"kotak:session:{self.user_id}")
            if raw:
                return KotakNeoSession.from_dict(json.loads(raw))
        except Exception as e:
            log.warning("kotak.cache_load_failed", error=str(e))
        return None

    async def invalidate_session(self) -> None:
        try:
            from src.db.redis_client import get_redis
            redis = get_redis()
            await redis.delete(f"kotak:session:{self.user_id}")
            self._session = None
            log.info("kotak.session_invalidated", user_id=self.user_id)
        except Exception as e:
            log.warning("kotak.invalidate_failed", error=str(e))

    # ────────────────────────────────────────────────────────────────────────
    # HTTP helpers
    # ────────────────────────────────────────────────────────────────────────

    async def _post_json(self, url: str, payload: dict, headers: dict) -> dict:
        """POST with JSON body — used only for login endpoints."""
        resp = await self._client.post(url, json=payload, headers=headers)
        return self._handle_response(resp)

    async def _post_form(self, endpoint: str, j_data: dict) -> dict:
        """
        POST with x-www-form-urlencoded body containing jData field.
        Used for ALL post-login order and report APIs.
        """
        self._ensure_session()
        url = f"{self._session.base_url}{endpoint}"

        # jData must be a JSON string, then URL-encoded in the form body
        body = urlencode({"jData": json.dumps(j_data)})

        resp = await self._client.post(
            url,
            content=body,
            headers={
                **self._trading_headers(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        return self._handle_response(resp)

    async def _get(self, endpoint: str) -> dict:
        """GET request — used for order book, positions, holdings."""
        self._ensure_session()
        url = f"{self._session.base_url}{endpoint}"
        resp = await self._client.get(url, headers=self._trading_headers())
        return self._handle_response(resp)

    def _handle_response(self, resp: httpx.Response) -> dict:
        log.debug("kotak.response", status=resp.status_code, url=str(resp.url))

        if resp.status_code == 401:
            try:
                body = resp.json()
                kotak_msg = (
                    body.get("emsg") or body.get("message") or body.get("errMsg") or str(body)
                )
            except Exception as e:
                kotak_msg = resp.text[:300]
            log.warning("kotak.401_received", url=str(resp.url), body=kotak_msg)
            expiry_markers = ("session", "token", "auth", "expir", "login")
            looks_like_expiry = any(m in kotak_msg.lower() for m in expiry_markers)

            if looks_like_expiry:
                self.session = None
                raise KotakAuthError(f"Session expired — re-authenticate ({kotak_msg})")
            else:
                raise KotakAuthError(f"kotak rejected req (401) ({kotak_msg})", 401)

        if resp.status_code == 424:
            # Dependency failure — usually means token expired or already used
            raise KotakAuthError(
                "Kotak dependency error (424) — TOTP may have expired. "
                "Wait 30 seconds and try submitting TOTP again."
            )

        if resp.status_code == 429:
            raise KotakRateLimitError("Rate limit — 10 req/sec max")

        if resp.status_code >= 500:
            raise KotakAPIError(
                f"Kotak server error {resp.status_code}: {resp.text[:200]}",
                resp.status_code,
            )

        try:
            data = resp.json()
        except Exception:
            raise KotakAPIError(f"Non-JSON response: {resp.text[:200]}")

        if isinstance(data, dict):
            stat = data.get("stat", "")
            if stat == "Not_Ok":
                raise KotakAPIError(
                    data.get("emsg") or data.get("message") or "Kotak API error",
                    data.get("stCode", 0),
                )

        return data

    async def _retry(self, fn, retries: int = MAX_RETRIES) -> dict:
        last_err = None
        for attempt in range(retries):
            try:
                return await fn()
            except KotakAuthError:
                raise  # never retry auth errors
            except KotakRateLimitError as e:
                wait = (2 ** attempt) * self.RETRY_DELAY_SEC
                log.warning("kotak.rate_limit_retry", attempt=attempt, wait=wait)
                await asyncio.sleep(wait)
                last_err = e
            except KotakAPIError as e:
                if e.status_code and e.status_code >= 500:
                    wait = (2 ** attempt) * self.RETRY_DELAY_SEC
                    log.warning("kotak.server_error_retry", attempt=attempt, wait=wait)
                    await asyncio.sleep(wait)
                    last_err = e
                else:
                    raise  # 400/422 = client error, no retry
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                wait = (2 ** attempt) * self.RETRY_DELAY_SEC
                log.warning("kotak.network_retry", attempt=attempt, wait=wait, error=str(e))
                await asyncio.sleep(wait)
                last_err = KotakAPIError(f"Network error: {e}")

        raise last_err or KotakAPIError("Max retries exceeded")

    def _trading_headers(self) -> dict:
        """Headers for all post-login APIs."""
        return {
            "accept": "application/json",
            "Auth": self._session.session_token,
            "Sid": self._session.session_sid,
            "neo-fin-key": NEO_FIN_KEY,
        }

    def _map_order_type(self, order_type: str) -> str:
        return {
            "MARKET": "MKT",
            "LIMIT": "L",
            "SL": "SL",
            "SL-M": "SL-M",
        }.get(order_type.upper(), "MKT")

    def _ensure_session(self) -> None:
        if not self._session or not self._session.is_valid():
            raise KotakAuthError(
                "No valid session. User must complete TOTP → MPIN auth."
            )

    async def close(self) -> None:
        await self._client.aclose()


# ── Exceptions ──────────────────────────────────────────────────────────────

class KotakAPIError(Exception):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class KotakAuthError(KotakAPIError):
    pass


class KotakRateLimitError(KotakAPIError):
    pass
