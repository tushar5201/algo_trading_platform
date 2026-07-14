import structlog

from src.execution.signal import Signal, SignalAction, OrderType, ProductType, Exchange
from src.strategies.base import BaseStrategy
from src.utils.market_data import (
    MarketSnapshot, OptionChainEntry,
    get_market_data, _fetch_live_option_chain,
)

log = structlog.get_logger()

# ── Strategy parameters ───────────────────────────────────────────────────────
PCR_BULLISH_THRESHOLD  = 1.3    # PCR > 1.3 → heavy put OI → expect bullish reversal
PCR_BEARISH_THRESHOLD  = 0.7    # PCR < 0.7 → heavy call OI → expect bearish reversal
OI_CONCENTRATION_RATIO = 0.35   # top-3 strikes > 35 % of total OI → strong level
MIN_CONFIDENCE         = 0.55   # below this → HOLD
LOT_SIZE               = 65     
DEFAULT_QTY            = 1      # Number of lots per trade


class OINiftyOptionsStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "oi_nifty_options"

    def symbols(self) -> list[str]:
        return ["NIFTY"]

    async def analyze(self, snapshots: dict[str, MarketSnapshot]) -> Signal:
        snapshot = snapshots.get("NIFTY")
        if not snapshot:
            return self._hold("No NIFTY market data")

        if get_market_data() is None:
            return self._hold(
                "Market data client not ready — "
                "complete Kotak TOTP→MPIN login first"
            )

        # ── Fetch live option chain via scrip-master tokens ───────────────────
        try:
            chain = await _fetch_live_option_chain(
                "NIFTY", atm_range=5, strike_gap=50
            )
            self.log.info("oi.using_live_chain", strikes=len(chain))
        except Exception as e:
            self.log.error("oi.live_chain_failed", error=str(e))
            return self._hold(f"Option chain fetch error: {e}")

        if len(chain) < 5:
            return self._hold(
                f"Insufficient option chain data (got {len(chain)} strikes, need ≥5)"
            )

        # ── 1. Put-Call Ratio ─────────────────────────────────────────────────
        total_call_oi = sum(e.call_oi for e in chain)
        total_put_oi  = sum(e.put_oi  for e in chain)

        if total_call_oi == 0:
            return self._hold("Zero call OI — market data may be stale")

        pcr = total_put_oi / total_call_oi

        # ── 2. ATM strike ─────────────────────────────────────────────────────
        ltp       = snapshot.ltp
        atm_entry = min(chain, key=lambda e: abs(e.strike - ltp))
        atm_strike = atm_entry.strike

        # ── 3. OI concentration (top-3 strikes) ──────────────────────────────
        sorted_by_oi  = sorted(chain, key=lambda e: e.call_oi + e.put_oi, reverse=True)
        top3_oi       = sum(e.call_oi + e.put_oi for e in sorted_by_oi[:3])
        total_oi      = total_call_oi + total_put_oi
        concentration = top3_oi / total_oi if total_oi > 0 else 0

        self.log.info(
            "oi.analysis",
            atm_strike=atm_strike,
            ltp=round(ltp, 2),
            pcr=round(pcr, 3),
            concentration=round(concentration, 3),
            total_call_oi=total_call_oi,
            total_put_oi=total_put_oi,
        )

        # ── 4. Signal logic ───────────────────────────────────────────────────

        # High PCR → writers sold more puts than calls → bullish bias
        if pcr > PCR_BULLISH_THRESHOLD:
            confidence = min(0.95, 0.5 + (pcr - PCR_BULLISH_THRESHOLD) * 0.5)
            if confidence < MIN_CONFIDENCE:
                return self._hold(
                    f"PCR={pcr:.2f} bullish but confidence too low ({confidence:.2f})"
                )

            target_strike = atm_strike + 100      # slightly OTM call
            symbol        = self._build_symbol("NIFTY", target_strike, "CE")
            entry_price   = self._get_ltp(chain, target_strike, "CE")
            sl = round(entry_price * 0.70, 2)     # 30 % stop-loss
            tp = round(entry_price * 1.60, 2)     # 60 % target

            return Signal(
                action       = SignalAction.BUY,
                symbol       = symbol,
                qty          = DEFAULT_QTY * LOT_SIZE,
                price        = 0.0,
                order_type   = OrderType.MARKET,
                product_type = ProductType.MIS,
                exchange     = Exchange.NFO,
                strategy_name = self.name,
                confidence   = round(confidence, 3),
                reason       = f"PCR={pcr:.2f} bullish. Buying {symbol}",
                stop_loss    = sl,
                take_profit  = tp,
                metadata     = {
                    "pcr": round(pcr, 3),
                    "atm_strike": atm_strike,
                    "concentration": round(concentration, 3),
                    "ltp": round(ltp, 2),
                    "entry_price": entry_price,
                },
            )

        # Low PCR → writers sold more calls than puts → bearish bias
        if pcr < PCR_BEARISH_THRESHOLD:
            confidence = min(0.95, 0.5 + (PCR_BEARISH_THRESHOLD - pcr) * 0.5)
            if confidence < MIN_CONFIDENCE:
                return self._hold(
                    f"PCR={pcr:.2f} bearish but confidence too low ({confidence:.2f})"
                )

            target_strike = atm_strike - 100      # slightly OTM put
            symbol        = self._build_symbol("NIFTY", target_strike, "PE")
            entry_price   = self._get_ltp(chain, target_strike, "PE")
            sl = round(entry_price * 0.70, 2)
            tp = round(entry_price * 1.60, 2)

            return Signal(
                action       = SignalAction.BUY,
                symbol       = symbol,
                qty          = DEFAULT_QTY * LOT_SIZE,
                price        = 0.0,
                order_type   = OrderType.MARKET,
                product_type = ProductType.MIS,
                exchange     = Exchange.NFO,
                strategy_name = self.name,
                confidence   = round(confidence, 3),
                reason       = f"PCR={pcr:.2f} bearish. Buying {symbol}",
                stop_loss    = sl,
                take_profit  = tp,
                metadata     = {
                    "pcr": round(pcr, 3),
                    "atm_strike": atm_strike,
                    "concentration": round(concentration, 3),
                    "ltp": round(ltp, 2),
                    "entry_price": entry_price,
                },
            )

        return self._hold(f"PCR {pcr:.2f} neutral — no clear directional bias")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _hold(self, reason: str) -> Signal:
        return Signal(
            action        = SignalAction.HOLD,
            symbol        = "",
            qty           = 0,
            price         = 0.0,
            strategy_name = self.name,
            reason        = reason,
        )

    def _build_symbol(self, index: str, strike: float, option_type: str) -> str:
        """
        Build NSE NFO trading symbol.
        Format: NIFTY26JUN24500CE  (2-digit year, 3-letter month)
        """
        from datetime import datetime
        now   = datetime.now()
        month = now.strftime("%b").upper()   # JUN
        year  = now.strftime("%y")           # 26
        return f"{index}{year}{month}{int(strike)}{option_type}"

    def _get_ltp(
        self,
        chain: list[OptionChainEntry],
        strike: float,
        option_type: str,
    ) -> float:
        entry = next((e for e in chain if e.strike == strike), None)
        if not entry:
            return 100.0   # safe fallback for SL/TP calculation
        return entry.call_ltp if option_type == "CE" else entry.put_ltp