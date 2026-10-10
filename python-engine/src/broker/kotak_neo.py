import httpx
import structlog
import json
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urlencode

from aiobreaker import CircuitBreakerError

from src.broker.errors import KotakOrderUncertainError, KotakBusinessError, KotakAuthError, KotakAPIError, KotakRateLimitError
from src.broker.base_broker import BaseBroker
from src.utils.rate_limiter import kotak_limiter
from src.utils.retry import retrying_orders, retrying_auth

log = structlog.get_logger()

# ── Fixed login endpoints (never use baseUrl for these) ─────────────────────
LOGIN_BASE = "https://mis.kotaksecurities.com"
TOTP_LOGIN_URL = f"{LOGIN_BASE}/login/1.0/tradeApiLogin"
MPIN_VALIDATE_URL = f"{LOGIN_BASE}/login/1.0/tradeApiValidate"
NEO_FIN_KEY = "neotradeapi"
_VALID_SEGMENTS = {"nse_cm", "nse_fo", "bse_cm", "bse_fo", "mcx_fo", "cde_fo"}
_EXCHANGE_TO_SEGMENT = {
    "NSE": "nse_cm",
    "NSE_CM": "nse_cm",
    "NFO": "nse_fo",
    "NSE_FO": "nse_fo",
    "BSE": "bse_cm",
    "BSE_CM": "bse_cm",
    "BFO": "bse_fo",
    "BSE_FO": "bse_fo",
    "MCX": "mcx_fo",
    "MCX_FO": "mcx_fo",
    "CDE": "cde_fo",
    "CDE_FO": "cde_fo",
}


def _to_kotak_segment(exchange: str) -> str:
    """Normalize any exchange spelling used elsewhere in the codebase into
    the exact lowercase segment code Kotak's API requires."""
    if not exchange:
        return exchange
    if exchange.lower() in _VALID_SEGMENTS:
        return exchange.lower()
    mapped = _EXCHANGE_TO_SEGMENT.get(exchange.upper())
    if mapped:
        return mapped
    log.warning("kotak.unknown_exchange_segment", exchange=exchange)
    return exchange.lower()


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

        async def _do_totp_call():
            return await kotak_auth_breaker.call_async(
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

        try:
            async for attempt in retrying_auth():
                with attempt:
                    resp = await _do_totp_call()
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
        """
        Step 2 of auth.
        mpin:        user's 6-digit MPIN
        view_token:  token from Step 1
        view_sid:    sid from Step 1
        access_token: from Kotak NEO dashboard

        Returns KotakNeoSession with session_token, session_sid, base_url.
        """
        from src.broker.circuit_breaker import kotak_auth_breaker
        await kotak_limiter.acquire_auth()

        async def _do_mpin_call():
            return await kotak_auth_breaker.call_async(
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

        try:
            async for attempt in retrying_auth():
                with attempt:
                    resp = await _do_mpin_call()
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
            trigger_price: float = 0.0
    ) -> dict:
        """
        Place order via Kotak Neo API.

        CRITICAL: body is x-www-form-urlencoded with a single 'jData' field
        containing a JSON string. NOT a JSON POST body.
        """
        from src.broker.circuit_breaker import kotak_orders_breaker
        self._ensure_session()
        exchange = _to_kotak_segment(exchange)
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
            "tp": str(trigger_price) if trigger_price > 0 else "0",  # trigger price
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

        except httpx.ConnectError as e:
            raise KotakAPIError(f"Couldn't connect to Kotak: {e}")

        except httpx.TransportError as e:
            raise KotakOrderUncertainError(
                f"Order request to kotak did not complete ({type(e).__name__});"
                "it may or may not have been placed - check the order book."
            )

        # Success: { "nOrdNo": "...", "stat": "Ok", "stCode": 200 }
        order_id = resp.get("nOrdNo", "")
        if not order_id:
            raise KotakBusinessError(
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

    async def place_sl_order(
            self,
            symbol: str,
            position_side: str,
            qty: int,
            trigger_price: float,
            product_type: str,
            exchange: str,
    ) -> dict:
        """
        Places main order + separate SL-M order (opposite side).
        Kotak Neo does not support native bracket orders for all segments,
        so we place two separate orders.
        """
        # Main order

        if trigger_price <= 0:
            raise KotakBusinessError("Stop-loss trigger price must be > 0")
        sl_side = "SELL" if position_side == "BUY" else "BUY"
        return await self.place_order(
            symbol=symbol,
            side=sl_side,
            qty=qty,
            price=0.0,  # SL-M = market when triggered
            order_type="SL-M",
            product_type=product_type,
            exchange=exchange,
            trigger_price=trigger_price,
        )

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
            "es": _to_kotak_segment(exchange),
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
            resp = await kotak_orders_breaker.call_async(
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

    _TERMINAL = {"complete", "filled", "traded", "rejected", "cancelled", "canceled", "cancel"}

    async def get_order_status(self, order_id: str) -> dict:
        """Fetch order history for a specific order number."""
        self._ensure_session()

        resp = await self._post_form(
            endpoint="/quick/order/history",
            j_data={"nOrdNo": order_id}
        )

        data = resp.get("data", []) or []
        latest = next(
            (e for e in data if str(e.get("ordSt", "")).lower().strip() in self._TERMINAL),
            data[0] if data else {}
        )

        try:
            avg_price = float(latest.get("avgPrc") or 0)
        except (TypeError, ValueError):
            avg_price = 0.0

        return {
            "order_id": order_id,
            "status": latest.get("ordSt", "UNKNOWN"),
            "symbol": latest.get("trdSym", ""),
            "qty": latest.get("qty", 0),
            "avg_price": avg_price,
            "side": "BUY" if latest.get("trnsTp") == "B" else "SELL",
            "rejection_reason": latest.get("rejRsn") or "",
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
            "exSeg": _to_kotak_segment(exchange),
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
        self._ensure_session()
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
            self._session = None
            log.warning("kotak.401_body", body=resp.text[:500], url=str(resp.url))
            raise KotakAuthError("Session expired - re-authenticate")

        if resp.status_code == 424:
            # Dependency failure — usually means token expired or already used
            raise KotakAuthError(
                "Kotak dependency error (424) — TOTP may have expired. "
                "Wait 30 seconds and try submitting TOTP again."
            )

        if resp.status_code == 429:
            raise KotakRateLimitError("Rate limit exceeded (HTTP 429)", 429)

        if resp.status_code >= 500:
            raise KotakAPIError(
                f"Kotak server error {resp.status_code}: {resp.text[:200]}",
                resp.status_code,
            )

        if 400 <= resp.status_code < 500:
            raise KotakBusinessError(
                f"Kotak rejected the request ({resp.status_code}): {resp.text[:200]}",
                resp.status_code,
            )

        try:
            data = resp.json()
        except Exception:
            raise KotakAPIError(f"Non-JSON response: {resp.text[:200]}")

        if isinstance(data, dict):
            stat = data.get("stat", "")
            if stat == "Not_Ok":
                raise KotakBusinessError(
                    data.get("errMsg") or data.get("emsg") or data.get("message") or "Kotak API error",
                    data.get("stCode", 0) if isinstance(data.get("stCode", 0), int) else 0,
                )

        return data

    async def _retry(self, fn) -> dict:
        async for attempt in retrying_orders():
            with attempt:
                return await fn()
        raise KotakAPIError("Max retries exceeeded")

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


