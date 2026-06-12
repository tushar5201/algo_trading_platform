import httpx
import structlog
from dataclasses import dataclass, field
from typing import Optional

from aiobreaker import CircuitBreakerError

from src.broker.circuit_breaker import kotak_quotes_breaker

log = structlog.get_logger()


@dataclass
class OptionChainEntry:
    strike:   float
    call_oi:  int
    put_oi:   int
    call_ltp: float
    put_ltp:  float
    call_iv:  float
    put_iv:   float


@dataclass
class MarketSnapshot:
    symbol:       str
    ltp:          float
    open:         float
    high:         float
    low:          float
    volume:       int
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
        self.base_url     = base_url.rstrip("/")
        self._client      = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            headers={"Authorization": access_token},
        )

    async def get_quote(self, exchange: str, symbol: str) -> dict:
        """
        Fetch live quote for one symbol.
        exchange: nse_cm | nse_fo | bse_cm | bse_fo
        symbol:   pSymbol token from scrip master (e.g. "26000" for Nifty 50)
        """
        query = f"{exchange}|{symbol}"
        url   = self.QUOTES_URL.format(base_url=self.base_url, query=query)

        try:
            resp = await kotak_quotes_breaker.call_async(self._client.get, url)
        except CircuitBreakerError:
            raise RuntimeError(
                "Kotak quotes circuit is OPEN — too many recent failures. "
                "Strategy will HOLD until circuit recovers (~60s)."
            )

        resp.raise_for_status()
        raw  = resp.json()

        # Kotak Neo wraps all responses: {"data": [...], "message": "", "status": 200}
        data = raw.get("data", raw) if isinstance(raw, dict) else raw

        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict):
            return data
        raise Exception(f"No quote data for {exchange}|{symbol}: {raw}")

    async def get_multiple_quotes(self, queries: list[str]) -> list[dict]:
        """
        Batch quote fetch.
        queries: ["nse_cm|26000", "nse_fo|60517", ...]  (pSymbol tokens)
        """
        query_str = ",".join(queries)
        url       = self.QUOTES_URL.format(base_url=self.base_url, query=query_str)

        try:
            resp = await kotak_quotes_breaker.call_async(self._client.get, url)
        except CircuitBreakerError:
            raise RuntimeError(
                "Kotak quotes circuit is OPEN — too many recent failures. "
                "Strategy will HOLD until circuit recovers (~60s)."
            )

        resp.raise_for_status()
        raw  = resp.json()

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
        self.base_url     = base_url.rstrip("/")
        self._cache: dict[str, dict] = {}   # trading_symbol → {token, lot_size, ...}
        self._loaded = False

    async def load(self) -> None:
        import csv, io

        auth_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers={"Authorization": self.access_token},
        )
        try:
            # Step 1: get file paths (needs auth)
            resp  = await auth_client.get(
                self.SCRIP_URL.format(base_url=self.base_url)
            )
            resp.raise_for_status()
            paths = resp.json()["data"]["filesPaths"]

            nse_fo_url = next((p for p in paths if "nse_fo" in p), None)
            if not nse_fo_url:
                log.warning("scrip_master.nse_fo_not_found")
                return
        finally:
            await auth_client.aclose()

        # Step 2: download CSV — public URL, NO auth header (auth causes 400)
        csv_client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        try:
            csv_resp = await csv_client.get(nse_fo_url)
            csv_resp.raise_for_status()
        finally:
            await csv_client.aclose()

        reader = csv.DictReader(io.StringIO(csv_resp.text))
        for row in reader:
            symbol = row.get("pTrdSymbol", "").strip()
            if symbol:
                self._cache[symbol] = {
                    "token":    row.get("pSymbol", "").strip(),
                    "exchange": row.get("pExchSeg", "nse_fo"),
                    "lot_size": int(row.get("lLotSize", 1) or 1),
                    "expiry":   row.get("lExpiryDate", ""),
                }

        self._loaded = True
        log.info("scrip_master.loaded", count=len(self._cache))

    def get_token(self, trading_symbol: str) -> Optional[str]:
        """Return numeric pSymbol token for a trading symbol, or None."""
        return self._cache.get(trading_symbol, {}).get("token") or None

    def get_lot_size(self, trading_symbol: str) -> int:
        return self._cache.get(trading_symbol, {}).get("lot_size", 75)


# ── Global singletons ─────────────────────────────────────────────────────────

_market_data:  Optional[KotakMarketData] = None
_scrip_master: Optional[ScripMaster]     = None


def init_market_data(access_token: str, base_url: str) -> None:
    global _market_data, _scrip_master
    if not base_url or not access_token:
        log.error(
            "market_data.init_skipped",
            reason="Missing base_url or access_token — complete Kotak TOTP→MPIN login first",
            has_base_url=bool(base_url),
            has_token=bool(access_token),
        )
        return
    _market_data  = KotakMarketData(access_token, base_url)
    _scrip_master = ScripMaster(access_token, base_url)
    log.info("market_data.initialized", base_url=base_url[:50])


def get_market_data() -> Optional[KotakMarketData]:
    return _market_data


def get_scrip_master() -> Optional[ScripMaster]:
    return _scrip_master


# ── Snapshot fetcher ──────────────────────────────────────────────────────────

async def _fetch_live_snapshot(symbol: str) -> MarketSnapshot:
    """Fetch real OHLC snapshot from Kotak Quotes API."""
    md = _market_data
    if md is None:
        raise RuntimeError(
            "Market data client not initialised — complete Kotak login first"
        )

    exchange, kotak_symbol = _map_symbol(symbol)
    log.info("market_data.fetching_live",
             symbol=symbol, kotak_symbol=kotak_symbol)

    quote = await md.get_quote(exchange, kotak_symbol)
    ohlc  = quote.get("ohlc", {})

    return MarketSnapshot(
        symbol = symbol,
        ltp    = float(quote.get("ltp",         0) or 0),
        open   = float(ohlc.get("open",         0) or 0),
        high   = float(ohlc.get("high",         0) or 0),
        low    = float(ohlc.get("low",          0) or 0),
        volume = int(quote.get("last_volume",   0) or 0),
        option_chain = [],  # populated separately by _fetch_live_option_chain
    )


# ── Option chain fetcher ──────────────────────────────────────────────────────

async def _fetch_live_option_chain(
    index:      str,
    atm_range:  int = 5,
    strike_gap: int = 50,
) -> list[OptionChainEntry]:
    """
    Fetch live option chain for Nifty / BankNifty using Kotak Quotes API.

    KEY POINT: Kotak's neosymbol endpoint requires the numeric pSymbol token,
    not the trading symbol string.  We look up each strike in the scrip master
    cache to build "nse_fo|{token}" queries.  If the scrip master hasn't loaded
    yet we return [] immediately — the strategy HOLDs and retries next minute.
    """
    import re as _re
    from datetime import datetime

    md    = _market_data
    scrip = _scrip_master

    if md is None:
        raise RuntimeError("Market data not initialised")

    # ── 1. Get Nifty LTP to find ATM ─────────────────────────────────────────
    index_quote = await md.get_quote("nse_cm", "Nifty 50")
    ltp         = float(index_quote.get("ltp", 0) or 0)

    if ltp == 0:
        raise ValueError(
            "Got ltp=0 for Nifty 50 — Kotak session may be expired"
        )

    atm     = round(ltp / strike_gap) * strike_gap
    strikes = [atm + (i * strike_gap) for i in range(-atm_range, atm_range + 1)]

    now   = datetime.now()
    month = now.strftime("%b").upper()   # JUN
    year  = now.strftime("%y")           # 26

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

    token_to_symbol: dict[str, str] = {}   # pSymbol token → trading symbol
    queries:         list[str]      = []

    for strike in strikes:
        for opt_type in ("CE", "PE"):
            trading_sym = f"NIFTY{year}{month}{int(strike)}{opt_type}"
            token       = scrip.get_token(trading_sym)
            if token:
                queries.append(f"nse_fo|{token}")
                token_to_symbol[token] = trading_sym
            else:
                log.debug("option_chain.token_not_found", symbol=trading_sym)

    if not queries:
        log.warning(
            "option_chain.no_tokens_found",
            month=month, year=year, atm=atm,
            note="Check scrip master has current expiry. "
                 "Monthly expiry changes last Thursday of month.",
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
            or token_to_symbol.get(str(q.get("token",   "")), "")
        )

        if not sym or not (sym.endswith("CE") or sym.endswith("PE")):
            continue

        option_type = sym[-2:]
        ltp_val     = float(q.get("ltp", 0) or 0)

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
            }

        # OI: Kotak returns "oi" for F&O contracts
        oi_raw = q.get("oi", 0) or q.get("open_interest", 0) or q.get("open_int", "") or 0
        if not oi_raw:
            oi_raw = (
                float(q.get("total_buy_quantity",  0) or 0) +
                float(q.get("total_sell_quantity", 0) or 0)
            )
        oi_val = int(float(oi_raw)) or 100_000

        if option_type == "CE":
            chain[strike_val]["call_ltp"] = ltp_val
            chain[strike_val]["call_oi"]  = oi_val
        else:
            chain[strike_val]["put_ltp"]  = ltp_val
            chain[strike_val]["put_oi"]   = oi_val

    log.info("option_chain.parsed",
             strikes_found=len(chain), atm=atm)

    return [
        OptionChainEntry(
            strike   = strike,
            call_oi  = data["call_oi"],
            put_oi   = data["put_oi"],
            call_ltp = data["call_ltp"],
            put_ltp  = data["put_ltp"],
            call_iv  = 0.0,
            put_iv   = 0.0,
        )
        for strike, data in sorted(chain.items())
    ]


def _map_symbol(symbol: str) -> tuple[str, str]:
    """Map strategy symbol → (exchange, kotak_symbol) for quotes API."""
    mapping = {
        "NIFTY":     ("nse_cm", "Nifty 50"),
        "BANKNIFTY": ("nse_cm", "Nifty Bank"),
        "SENSEX":    ("bse_cm", "SENSEX"),
        "RELIANCE":  ("nse_cm", "2885"),
        "TCS":       ("nse_cm", "11536"),
        "INFY":      ("nse_cm", "1594"),
    }
    return mapping.get(symbol, ("nse_cm", symbol))