import functools
from concurrent.futures.thread import ThreadPoolExecutor
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import structlog
from nselib import capital_market
import pandas as pd
import yfinance as yf
import asyncio
import threading
import time
from src.execution.signal import OrderType, Exchange
from src.execution.signal import Signal, SignalAction, ProductType
from src.strategies.base import BaseStrategy, RetryLater
from src.utils.market_data import MarketSnapshot

log = structlog.getLogger()
IST = ZoneInfo("Asia/Kolkata")

@functools.lru_cache(maxsize=1)
def _get_all_symbols() -> list[str]:
    all_symbols_df = capital_market.equity_list()
    return all_symbols_df['SYMBOL'].tolist()

TOP_N_TO_TRADE = 3
RSI_PERIOD = 14
EMA_SPAN = 61
RSI_THRESHOLD = 60
MIN_BARS = 100
SCAN_WORKERS = 3                  # was 8: Yahoo Finance throttles bursts of parallel requests
REQUEST_DELAY_SEC = 0.25          # pause after each request, per worker
MAX_FAILED_RATIO = 0.10           # more failures than this => the scan result cannot be trusted
RATE_LIMIT_BACKOFF_START = 20.0   # seconds; doubles for each consecutive pause
RATE_LIMIT_BACKOFF_MAX = 120.0
MAX_RATE_LIMIT_PAUSES = 6         # then give up and retry later
PER_SYMBOL_RETRIES = 3

_scan_cache: dict[str, list[dict]] = {}
_scan_lock = asyncio.Lock()


def _is_rate_limited(exc: BaseException) -> bool:
    text = str(exc)
    return type(exc).__name__ == "YFRateLimitError" or "Too Many Requests" in text or "Rate limited" in text


class _RateGate:
    """Shared by all scan threads. When Yahoo answers 'Too Many Requests' EVERY thread pauses
    (with growing back-off) instead of each one hammering on and failing."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._blocked_until = 0.0
        self.pauses = 0
        self.aborted = False

    def wait(self) -> None:
        while not self.aborted:
            with self._lock:
                delay = self._blocked_until - time.monotonic()
            if delay <= 0:
                return
            time.sleep(min(delay, 1.0))

    def trip(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._blocked_until:
                return  # another thread already started a pause for this burst
            self.pauses += 1
            if self.pauses > MAX_RATE_LIMIT_PAUSES:
                self.aborted = True
                return
            backoff = min(RATE_LIMIT_BACKOFF_START * 2 ** (self.pauses - 1), RATE_LIMIT_BACKOFF_MAX)
            self._blocked_until = now + backoff
        log.warning("ema_scan.rate_limited", pause_sec=backoff, pause_no=self.pauses)

    def success(self) -> None:
        with self._lock:
            if time.monotonic() >= self._blocked_until:
                self.pauses = 0

def wilder_rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """Wilder's RSI. A window with no losses is 100 (not NaN / inf)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(~((avg_loss == 0) & (avg_gain > 0)), 100.0)
    rsi = rsi.where(~((avg_loss == 0) & (avg_gain == 0)), 50.0)
    return rsi


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['EMA_61'] = df["Close"].ewm(span=EMA_SPAN, adjust=False).mean()
    df['RSI'] = wilder_rsi(df["Close"])
    return df


def evaluate_frame(symbol: str, df: pd.DataFrame) -> Optional[dict]:
    """Pure breakout test on a daily OHLC frame. Returns the candidate dict or None."""
    if len(df) < MIN_BARS:
        return None
    df = add_indicators(df)
    last = df.iloc[-1]
    prev = df.iloc[-2]
    if pd.isna(last['RSI']) or pd.isna(prev['RSI']):
        return None

    condition = (
            (prev['Close'] <= prev['EMA_61']) and
            (last['Close'] > last['EMA_61']) and
            (last['RSI'] > RSI_THRESHOLD) and
            (prev['RSI'] <= RSI_THRESHOLD)
    )
    if not condition:
        return None

    prev_close = float(prev['Close'])
    curr_price = float(last['Close'])
    return {
        "symbol": symbol,
        "pct_change": ((curr_price - prev_close) / prev_close) * 100,
        "ltp": curr_price,
        "rsi": float(last['RSI']),
    }

class Ema61Rsi60(BaseStrategy):
    run_times = ["15:20"]
    max_open_positions = TOP_N_TO_TRADE

    @property
    def name(self) -> str:
        return "61-ema-60-rsi"

    def symbols(self) -> list[str]:
        return []

    def _scan_one(self, symbol: str, gate: "_RateGate") -> tuple[str, Optional[dict]]:
        """Returns (status, candidate): status is 'ok', 'nodata' (delisted / no history) or 'failed'."""
        for _ in range(PER_SYMBOL_RETRIES):
            gate.wait()
            if gate.aborted:
                return "failed", None
            try:
                df = yf.Ticker(f"{symbol}.NS").history(period="6mo", interval="1d")
            except Exception as e:
                if _is_rate_limited(e):
                    gate.trip()
                    continue
                log.warning(f"{symbol} failed: {str(e)}")
                return "failed", None
            gate.success()
            time.sleep(REQUEST_DELAY_SEC)
            if df is None or df.empty:
                return "nodata", None
            return "ok", evaluate_frame(symbol, df)
        return "failed", None

    def _scan_symbols(self) -> list[dict]:
        symbols = _get_all_symbols()
        gate = _RateGate()
        found: list[dict] = []
        counts = {"ok": 0, "nodata": 0, "failed": 0}
        with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
            for status, candidate in pool.map(lambda sym: self._scan_one(sym, gate), symbols):
                counts[status] += 1
                if candidate:
                    found.append(candidate)
        log.info("ema_scan.done", total=len(symbols), matched=len(found), **counts)
        # A scan where many symbols failed says nothing about the market. It must never be cached
        # (it used to be: an all-failed scan was stored as "no stocks matched" for the whole day).
        if symbols and counts["failed"] / len(symbols) > MAX_FAILED_RATIO:
            raise RetryLater(
                f"Yahoo Finance rate-limited the scan ({counts['failed']}/{len(symbols)} symbols failed); will retry"
            )
        return found

    async def _selected(self) -> list[dict]:
        key = datetime.now(IST).strftime("%Y-%m-%d")
        async with _scan_lock:
            if key in _scan_cache:
                return _scan_cache[key]
            selected = await asyncio.to_thread(self._scan_symbols)
            _scan_cache.clear()
            _scan_cache[key] = selected
            return selected

    def _build_signal(self, stock: dict, rank: int, top_stocks:list[dict]) -> Signal:
        sl = round(stock['ltp'] * 0.95, 2)
        tp = round(stock['ltp'] * 1.10, 2)

        return Signal(
            action=SignalAction.BUY,
            symbol=stock['symbol'],
            qty=1,
            price=0.0,
            order_type=OrderType.MARKET,
            product_type=ProductType.NRML,
            exchange=Exchange.NSE,
            strategy_name=self.name,
            confidence=round(0.85 - (rank * 0.03), 2),  # slightly lower confidence for lower-ranked picks
            reason=f"Rank #{rank + 1} gainer + EMA61 RSI60 breakout",
            stop_loss=sl,
            take_profit=tp,
            metadata={
                "rank": rank + 1,
                "pct_change": stock['pct_change'],
                "entry_price": stock['ltp'],
                "top_n": top_stocks,
            }
        )

    async def analyze_many(self, snapshots: dict[str, MarketSnapshot]) -> list[Signal] | None:
        selected = await self._selected()
        if not selected:
            return [self._hold("No stocks matched")]

        top_stocks = sorted(selected, key=lambda x: x['pct_change'], reverse=True)[:TOP_N_TO_TRADE]
        log.info("Top stocks", stocks=top_stocks)
        return [
            self._build_signal(stock, rank, top_stocks)
            for rank, stock in enumerate(top_stocks)
        ]

    async def analyze(self, snapshots: dict[str, MarketSnapshot]) -> Signal:
        signals = await self.analyze_many(snapshots)
        return signals[0] if signals else self._hold("No stocks matched")

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        return add_indicators(df)

    def _hold(self, reason: str) -> Signal:
        return Signal(
            action=SignalAction.HOLD,
            symbol="",
            qty=0,
            price=0.0,
            strategy_name=self.name,
            reason=reason
        )
