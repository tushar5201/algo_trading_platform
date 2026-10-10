from datetime import datetime
from zoneinfo import ZoneInfo
import asyncio
import httpx
import structlog
from dataclasses import dataclass, field
from typing import Optional
import re as _re

from aiobreaker import CircuitBreakerError
import contextvars

from src.broker.errors import KotakAuthError, KotakBusinessError
from src.broker.circuit_breaker import kotak_quotes_breaker
from src.utils.rate_limiter import kotak_limiter
from src.utils.retry import retrying_quotes

log = structlog.get_logger()
IST = ZoneInfo("Asia/Kolkata")

@dataclass
class OptionChainEntry:
    strike: float
    call_oi: int
    put_oi: int
    call_ltp: float
    put_ltp: float
    call_iv: float
    put_iv: float
    expiry_tag: str = ""

@dataclass
class MarketSnapshot:
    symbol: str
    ltp: float
    open: float
    high: float
    low: float
    volume: int
    option_chain: list[OptionChainEntry] = field(default_factory=list)


# ── Kotak market data client ──────────────────────────────────────────────────

class KotakMarketData:
    """
    Fetches live market data from Kotak Neo Quotes API.
    Requires numeric pSymbol token (from scrip master) as the identifier —
    NOT the trading symbol string.
    """

    QUOTES_URL = "{base_url}/script-details/1.0/quotes/neosymbol/{query}/all"

    def __init__(self, access_token: str, base_url: str):
        self.access_token = access_token
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            headers={"Authorization": access_token},
        )

    async def _get(self, url: str) -> httpx.Response:
        await kotak_limiter.acquire_quotes()
        async def _once() -> httpx.Response:
            resp = await self._client.get(url)
            if resp.status_code in (401, 403):
                raise KotakAuthError("Quotes API rejected the session (401/403)", resp.status_code)
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                raise KotakBusinessError(f"Quotes API error ({resp.status_code})", resp.status_code)
            resp.raise_for_status()
            return resp

        try:
            async for attempt in retrying_quotes():
                with attempt:
                    return await kotak_quotes_breaker.call_async(_once)
        except CircuitBreakerError:
            raise RuntimeError(
                "Kotak quotes circuit is OPEN - too many recent failures"
                "Strategy will HOLD until circuit recovers (~60s)."
            )
        raise RuntimeError("unreachable")

    async def get_quote(self, exchange: str, symbol: str) -> dict:
        """
        Fetch live quote for one symbol.
        exchange: nse_cm | nse_fo | bse_cm | bse_fo
        symbol:   pSymbol token from scrip master (e.g. "26000" for Nifty 50)
        """
        query = f"{exchange}|{symbol}"
        url = self.QUOTES_URL.format(base_url=self.base_url, query=query)
        await kotak_limiter.acquire_quotes()

        resp = await self._get(url)
        raw = resp.json()

        data = raw.get("data", raw) if isinstance(raw, dict) else raw

        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict):
            return data
        raise Exception(f"No quote data for {exchange}|{symbol}:{raw}")

    async def get_multiple_quotes(self, queries: list[str]) -> list[dict]:
        """
        Batch quote fetch.
        queries: ["nse_cm|26000", "nse_fo|60517", ...]  (pSymbol tokens)
        """
        query_str = ",".join(queries)
        url = self.QUOTES_URL.format(base_url=self.base_url, query=query_str)

        resp = await self._get(url)
        raw = resp.json()

        # Unwrap Kotak envelope
        if isinstance(raw, dict):
            data = raw.get("data", [])
            return data if isinstance(data, list) else []
        return raw if isinstance(raw, list) else []

    async def close(self) -> None:
        await self._client.aclose()


# ── Scrip master ──────────────────────────────────────────────────────────────

class ScripMaster:
    """
    Downloads and caches Kotak scrip master CSV.
    Maps trading symbols → numeric pSymbol tokens required by the quotes API.
    """

    SCRIP_URL = "{base_url}/script-details/1.0/masterscrip/file-paths"

    def __init__(self, access_token: str, base_url: str):
        self.access_token = access_token
        self.base_url = base_url.rstrip("/")
        self._cache: dict[str, dict] = {}  # trading_symbol → {token, lot_size, ...}
        self._cm_cache: dict[str, dict] = {}  # trading_symbol → {token, lot_size, ...}
        self._loaded = False
        self.loaded_on: Optional[str] = None
        self._lock = asyncio.Lock()

    async def ensure_fresh(self) -> None:
        today = datetime.now(IST).strftime("%Y-%m-%d")
        if self._loaded and self.loaded_on == today:
            return
        async with self._lock:
            if self._loaded and self.loaded_on == today:
                return
            await self.load()

    async def load(self) -> None:

        auth_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers={"Authorization": self.access_token},
        )
        try:
            # Step 1: get file paths (needs auth)
            resp = await auth_client.get(
                self.SCRIP_URL.format(base_url=self.base_url)
            )
            resp.raise_for_status()
            paths = resp.json()["data"]["filesPaths"]
            nse_fo_url = next((p for p in paths if "nse_fo" in p), None)
            nse_cm_url = next((p for p in paths if "nse_cm" in p), None)

            if not nse_fo_url:
                log.warning("scrip_master.nse_fo_not_found")
            if not nse_cm_url:
                log.warning("scrip_master.nse_cm_not_found")
        finally:
            await auth_client.aclose()

        # Step 2: download CSV — public URL, NO auth header (auth causes 400)
        csv_client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        try:
            if nse_fo_url:
                await self._load_csv(csv_client, nse_fo_url, self._cache, default_exchange="nse_fo")
            if nse_cm_url:
                await self._load_csv(csv_client, nse_cm_url, self._cm_cache, default_exchange="nse_cm")
        finally:
            await csv_client.aclose()

        self._loaded = bool(self._cache or self._cm_cache)
        if self._loaded:
            self.loaded_on = datetime.now(IST).strftime("%Y-%m-%d")
        log.info("scrip_master.loaded", fo_count=len(self._cache), cm_count=len(self._cm_cache))

    @staticmethod
    async def _load_csv(
            csv_client: httpx.AsyncClient,
            url: str,
            target: dict[str, dict],
            default_exchange: str
    ) -> None:
        import csv, io
        csv_resp = await csv_client.get(url)
        csv_resp.raise_for_status()

        reader = csv.DictReader(io.StringIO(csv_resp.text))
        for row in reader:
            symbol = row.get("pTrdSymbol", "").strip()
            if symbol:
                target[symbol] = {
                    "token": row.get("pSymbol", "").strip(),
                    "exchange": row.get("pExchSeg", default_exchange),
                    "lot_size": int(row.get("lLotSize", 1) or 1),
                    "expiry": row.get("lExpiryDate", ""),
                }

    def get_token(self, trading_symbol: str) -> Optional[str]:
        """Return numeric pSymbol token for a trading symbol, or None."""
        return self._cache.get(trading_symbol, {}).get("token") or None

    def get_lot_size(self, trading_symbol: str) -> Optional[int]:
        entry = self._cache.get(trading_symbol)
        return entry.get("lot_size") if entry else None

    def get_equity_token(self, trading_symbol: str) -> Optional[str]:
        return self._cm_cache.get(trading_symbol, {}).get("token") or None

    def resolve(self, symbol: str) -> Optional[tuple[str, str]]:
        entry = self._cm_cache.get(symbol)
        if entry and entry.get("token"):
            return entry.get("exchange", "nse_cm"), entry["token"]
        entry = self._cache.get(symbol)
        if entry and entry.get("token"):
            return entry.get("exchange", "nse_fo"), entry["token"]
        return None

    def resolve_trading_symbol(self, bare_symbol: str) -> Optional[str]:
        if bare_symbol in self._cm_cache:
            return bare_symbol
        eq_candidate = f"{bare_symbol}-EQ"
        if eq_candidate in self._cm_cache:
            return eq_candidate

        prefix = f"{bare_symbol}-"
        for key in self._cm_cache:
            if key.startswith(prefix):
                return key
        return None


# ── Global singletons ─────────────────────────────────────────────────────────

_market_data: Optional[KotakMarketData] = None
_md_by_user: dict[str, KotakMarketData] = {}
_scrip_master: Optional[ScripMaster] = None
_current_user: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("md_user", default=None)

def _close_later(client: Optional[KotakMarketData]) -> None:
    if client is None:
        return
    try:
        asyncio.get_event_loop().create_task(client.close())
    except RuntimeError:
        pass

def init_market_data(access_token: str, base_url: str, user_id: Optional[str]=None) -> None:
    global _market_data, _scrip_master
    if not base_url or not access_token:
        log.error(
            "market_data.init_skipped",
            reason="Missing base_url or access_token — complete Kotak TOTP→MPIN login first",
            has_base_url=bool(base_url),
            has_token=bool(access_token),
        )
        return

    client = KotakMarketData(access_token, base_url)
    if user_id:
        old = _md_by_user.get(user_id)
        _md_by_user[user_id] = client
        _close_later(old)
    else:
        _close_later(_market_data)
    _market_data = client
    if _scrip_master is None:
        _scrip_master = ScripMaster(access_token, base_url)
    else:
        _scrip_master.access_token = access_token
        _scrip_master.base_url = base_url.rstrip("/")
    log.info("market_data.initialized", user_id=user_id, base_url=base_url[:50])

def has_market_data(user_id: str) -> bool:
    return user_id in _md_by_user

def bind_market_data_user(user_id: Optional[str]) -> None:
    _current_user.set(user_id)

def get_market_data() -> Optional[KotakMarketData]:
    uid = _current_user.get()
    if uid and uid in _md_by_user:
        return _md_by_user[uid]
    return _market_data

def get_scrip_master() -> Optional[ScripMaster]:
    return _scrip_master

async def close_market_data() -> None:
    clients = list(_md_by_user.values())
    if _market_data is not None and _market_data not in clients:
        clients.append(_market_data)
    for c in clients:
        try:
            await c.close()
        except Exception:
            pass
    _md_by_user.clear()

# ── Snapshot fetcher ──────────────────────────────────────────────────────────

async def _fetch_live_snapshot(symbol: str) -> MarketSnapshot:
    """Fetch real OHLC snapshot from Kotak Quotes API."""
    md = get_market_data()
    if md is None:
        raise RuntimeError(
            "Market data client not initialised — complete Kotak login first"
        )

    exchange, kotak_symbol = _map_symbol(symbol)
    log.info("market_data.fetching_live",
             symbol=symbol, kotak_symbol=kotak_symbol)

    quote = await md.get_quote(exchange, kotak_symbol)
    ohlc = quote.get("ohlc", {})

    return MarketSnapshot(
        symbol=symbol,
        ltp=float(quote.get("ltp", 0) or 0),
        open=float(ohlc.get("open", 0) or 0),
        high=float(ohlc.get("high", 0) or 0),
        low=float(ohlc.get("low", 0) or 0),
        volume=int(quote.get("last_volume", 0) or 0),
        option_chain=[],  # populated separately by _fetch_live_option_chain
    )


# ── Option chain fetcher ──────────────────────────────────────────────────────

async def _fetch_live_option_chain(
        index: str,
        atm_range: int = 5,
        strike_gap: int = 50,
) -> list[OptionChainEntry]:
    """
    Fetch live option chain for Nifty / BankNifty using Kotak Quotes API.

    KEY POINT: Kotak's neosymbol endpoint requires the numeric pSymbol token,
    not the trading symbol string.  We look up each strike in the scrip master
    cache to build "nse_fo|{token}" queries.  If the scrip master hasn't loaded
    yet we return [] immediately — the strategy HOLDs and retries next minute.
    """
    md = get_market_data()
    scrip = _scrip_master

    if md is None:
        raise RuntimeError("Market data not initialised")

    # ── 1. Get Nifty LTP to find ATM ─────────────────────────────────────────
    index_quote = await md.get_quote("nse_cm", "Nifty 50")
    ltp = float(index_quote.get("ltp", 0) or 0)

    if ltp == 0:
        raise ValueError(
            "Got ltp=0 for Nifty 50 — Kotak session may be expired"
        )

    atm = round(ltp / strike_gap) * strike_gap
    strikes = [atm + (i * strike_gap) for i in range(-atm_range, atm_range + 1)]

    # ── 2. Build queries using pSymbol tokens from scrip master ───────────────
    # Kotak neosymbol API requires pSymbol (numeric token), NOT trading symbol.
    # "nse_fo|NIFTY26JUN24500CE" → returns nothing
    # "nse_fo|60517"             → returns live quote for NIFTY26JUN24500CE
    if not scrip or not scrip._loaded:
        log.warning(
            "option_chain.scrip_master_not_ready",
            note="Scrip master loads ~10s after startup. Will retry next tick.",
        )
        return []
    await scrip.ensure_fresh()

    # Use IST (the container clock is UTC) and fall back to NEXT month when the current
    # month's contract has expired / been removed from the scrip master.
    expiry_tag = ""
    token_to_symbol: dict[str, str] = {}  # pSymbol token → trading symbol
    queries: list[str] = []
    for tag in _candidate_expiry_tags(datetime.now(IST)):
        token_to_symbol, queries = {}, []
        for strike in strikes:
            for opt_type in ("CE", "PE"):
                trading_sym = f"NIFTY{tag}{int(strike)}{opt_type}"
                token = scrip.get_token(trading_sym)
                if token:
                    queries.append(f"nse_fo|{token}")
                    token_to_symbol[token] = trading_sym
        if queries:
            expiry_tag = tag
            break

    if not queries:
        log.warning(
            "option_chain.no_tokens_found",
            atm=atm,
            note="No NIFTY contracts found for this or next month in the scrip master.",
        )
        return []

    log.info("option_chain.fetching",
             queries=len(queries), atm=atm, ltp=round(ltp, 2))

    # ── 3. Batch fetch ────────────────────────────────────────────────────────
    all_quotes = await md.get_multiple_quotes(queries)

    if not all_quotes:
        log.warning("option_chain.empty_api_response",
                    queries_sent=len(queries),
                    sample=queries[0] if queries else "none")
        return []

    log.debug("option_chain.raw_response",
              count=len(all_quotes),
              sample_keys=list(all_quotes[0].keys()) if all_quotes else [])

    # ── 4. Parse quotes into OptionChainEntry list ───────────────────────────
    chain: dict[float, dict] = {}

    for q in all_quotes:
        # Kotak still returns pTrdSymbol in the response even when queried by token
        sym = (
                q.get("pTrdSymbol", "")
                or q.get("trading_symbol", "")
                or q.get("display_symbol", "")
                or token_to_symbol.get(str(q.get("pSymbol", "")), "")
                or token_to_symbol.get(str(q.get("token", "")), "")
        )

        if not sym or not (sym.endswith("CE") or sym.endswith("PE")):
            continue

        option_type = sym[-2:]
        ltp_val = float(q.get("ltp", 0) or 0)

        # Extract numeric strike from symbol (handles monthly + weekly formats)
        m = _re.search(r"(\d{4,6})(?:CE|PE)$", sym)
        if not m:
            continue
        try:
            strike_val = float(m.group(1))
        except ValueError:
            continue

        if strike_val not in chain:
            chain[strike_val] = {
                "call_oi": 0, "put_oi": 0,
                "call_ltp": 0.0, "put_ltp": 0.0,
                "call_iv": 0.0, "put_iv": 0.0,
            }

        # OI: Kotak returns "oi" for F&O contracts. If it is missing we SKIP the contract.
        # (It used to be replaced by buy+sell quantity, or a made-up 100,000, and the PCR
        # that real orders were based on was computed from those invented numbers.)
        oi_raw = q.get("oi") or q.get("open_interest") or q.get("open_int")
        try:
            oi_val = int(float(oi_raw)) if oi_raw not in (None, "") else 0
        except (TypeError, ValueError):
            oi_val = 0
        if oi_val <= 0:
            log.debug("option_chain.oi_missing", symbol=sym)
            continue
        iv_raw = (
                q.get("iv") or q.get("impliedVolatility")
                or q.get("implied_volatility") or q.get("ivPercent") or 0
        )
        try:
            iv_val = float(iv_raw)
        except (TypeError, ValueError):
            iv_val = 0.0

        if option_type == "CE":
            chain[strike_val]["call_ltp"] = ltp_val
            chain[strike_val]["call_oi"] = oi_val
            chain[strike_val]["call_iv"] = iv_val
        else:
            chain[strike_val]["put_ltp"] = ltp_val
            chain[strike_val]["put_oi"] = oi_val
            chain[strike_val]["put_iv"] = iv_val

    # Only strikes with BOTH sides present are usable: a one-sided strike skews the PCR.
    complete = {k: v for k, v in chain.items() if v["call_oi"] > 0 and v["put_oi"] > 0}
    log.info("option_chain.parsed", strikes_found=len(chain), complete=len(complete), atm=atm, expiry=expiry_tag)

    min_strikes = max(3, atm_range)
    if len(complete) < min_strikes:
        log.warning("option_chain.insufficient_data", complete=len(complete), required=min_strikes)
        return []

    return [
        OptionChainEntry(
            strike=strike,
            call_oi=data["call_oi"],
            put_oi=data["put_oi"],
            call_ltp=data["call_ltp"],
            put_ltp=data["put_ltp"],
            call_iv=data.get("call_iv", 0.0),
            put_iv=data.get("put_iv", 0.0),
            expiry_tag=expiry_tag,
        )
        for strike, data in sorted(complete.items())
    ]


def _candidate_expiry_tags(now: datetime) -> list[str]:
    """['26SEP', '26OCT'] - current month first, then next month (year rolls over in December)."""
    tags = []
    for offset in (0, 1):
        month_index = now.month - 1 + offset
        year = now.year + month_index // 12
        month = month_index % 12 + 1
        tags.append(datetime(year, month, 1).strftime("%y%b").upper())
    return tags


async def fetch_ltp(exchange_segment: str, token: str) -> Optional[float]:
    """LTP for an already-resolved (segment, token). Returns None if unavailable."""
    md = get_market_data()
    if md is None or not token:
        return None
    quote = await md.get_quote(exchange_segment, token)
    ltp = float(quote.get("ltp", 0) or 0)
    return ltp if ltp > 0 else None

def _map_symbol(symbol: str) -> tuple[str, str]:
    """Map strategy symbol → (exchange, kotak_token) for the quotes API."""
    index_mapping = {
        "NIFTY": ("nse_cm", "Nifty 50"),
        "BANKNIFTY": ("nse_cm", "Nifty Bank"),
        "SENSEX": ("bse_cm", "SENSEX"),
    }
    if symbol in index_mapping:
        return index_mapping[symbol]

    scrip = _scrip_master
    if scrip and scrip._loaded:
        # F&O trading symbol (e.g. NIFTY26SEP24500CE): the quotes API needs the numeric token
        if symbol.endswith(("CE", "PE")):
            token = scrip.get_token(symbol)
            if token:
                return "nse_fo", token
        # equities are keyed as e.g. "RELIANCE-EQ" in the cash-market master
        trading_symbol = scrip.resolve_trading_symbol(symbol)
        if trading_symbol:
            token = scrip.get_equity_token(trading_symbol)
            if token:
                return "nse_cm", token
        log.warning(
            "market_data.symbol_not_in_scrip_master",
            symbol=symbol,
            note="Falling back to legacy hardcoded token map, if any.",
        )

    legacy_fallback = {
        "RELIANCE": ("nse_cm", "2885"),
        "TCS": ("nse_cm", "11536"),
        "INFY": ("nse_cm", "1594"),
    }
    return legacy_fallback.get(symbol, ("nse_cm", symbol))