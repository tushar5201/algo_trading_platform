import structlog

from src.execution.signal import Signal, SignalAction, OrderType, ProductType, Exchange
from src.strategies.base import BaseStrategy
from src.utils.market_data import MarketSnapshot, OptionChainEntry

log = structlog.get_logger()

# Strategy parameters
PCR_BULLISH_THRESHOLD = 1.3  # PCR > 1.3 -> too many puts -> bullish reversal signal
PCR_BEARISH_THRESHOLD = 0.7  # PCR < 0.7 -> too many calls -> bearish reversal signal
OI_CONCENTRATION_RATIO = 0.35  # top 3 strikes hold > 35% of total OI -> Strong level
MIN_CONFIDENCE = 0.55  # below this -> HOLD
LOT_SIZE = 65  # Nifty options lot size
DEFAULT_QTY = 1  # Num of lots


class OINiftyOptionsStrategy(BaseStrategy):
    @property
    def name(self) -> str:
        return "oi_nifty_options"

    def symbols(self) -> list[str]:
        return ["NIFTY"]

    async def analyze(self, snapshots: dict[str, MarketSnapshot]) -> Signal:
        snapshot = snapshots.get("NIFTY")
        if not snapshot:
            return self._hold("No market data available")
        chain = snapshot.option_chain
        if len(chain) < 5:
            return self._hold("insufficient option chain data available")

        # 1. Put-Call Ratio
        total_call_oi = sum(e.call_oi for e in chain)
        total_put_oi = sum(e.put_oi for e in chain)

        if total_call_oi == 0:
            return self._hold("Zero call data")

        # pcr = total_put_oi / total_call_oi
        pcr = total_put_oi / total_call_oi

        # 2. Find ATM Strike
        ltp = snapshot.ltp
        atm_entry = min(chain, key=lambda e: abs(e.strike - ltp))
        atm_strike = atm_entry.strike

        # 3. OI concentration around ATM (Top 3 strikes combined OI)
        sorted_by_oi = sorted(chain, key=lambda e: e.call_oi + e.put_oi, reverse=True)
        top3_oi = sum(e.call_oi + e.put_oi for e in sorted_by_oi[:3])
        total_oi = total_call_oi + total_put_oi
        concentration = top3_oi / total_oi if total_oi > 0 else 0

        self.log.info(
            "oi.analysis",
            atm_strike=atm_strike,
            ltp=round(ltp),
            pcr=round(pcr, 3),
            concentration=round(concentration, 3)
        )

        # 4. Signal Logic
        # High PCR + OI concentrated at call strikes -> bulls likely to win
        if pcr > PCR_BULLISH_THRESHOLD:
            # confidence = min(0.95, 0.5 + (pcr - PCR_BULLISH_THRESHOLD) * 0.5)
            confidence = min(0.95, 0.5 + (pcr - PCR_BULLISH_THRESHOLD) * 0.5)
            if confidence < MIN_CONFIDENCE:
                return self._hold(f"PCR={pcr:.2f} bullish but confidence too low: {confidence: .2f}")

            # BUY slightly OTM call
            target_strike = atm_strike + 100
            symbol = self._build_option_symbol("NIFTY", target_strike, "CE")
            entry_price = self._get_option_ltp(chain, target_strike, "CE")
            sl = round(entry_price * 0.70, 2)  # 30% SL
            tp = round(entry_price * 1.60, 2)  # 60% target

            return Signal(
                action=SignalAction.BUY,
                symbol=symbol,
                qty=DEFAULT_QTY * LOT_SIZE,
                price=0.0,  # market Order
                order_type=OrderType.MARKET,
                product_type=ProductType.MIS,
                exchange=Exchange.NFO,
                strategy_name=self.name,
                confidence=round(confidence, 3),
                reason=f"PCR={pcr:.2f} bullish. Buying {symbol}",
                stop_loss=sl,
                take_profit=tp,
                metadata={
                    "pcr": pcr,
                    "atm_strike": atm_strike,
                    "concentration": concentration,
                    "ltp": ltp,
                }
            )

        # Low PCR + OI concentrated at put strikes -> bears likely to win
        if pcr < PCR_BEARISH_THRESHOLD:
            # confidence = min(0.95, 0.5 + (pcr - PCR_BEARISH_THRESHOLD) * 0.5)
            confidence = min(0.95, 0.5 + (PCR_BEARISH_THRESHOLD - pcr) * 0.5)
            if confidence < MIN_CONFIDENCE:
                return self._hold(f"PCR={pcr: .2f} bearish but confidence too low: {confidence: .2f}")

            # BUY slightly OTM call
            target_strike = atm_strike - 100
            symbol = self._build_option_symbol("NIFTY", target_strike, "PE")
            entry_price = self._get_option_ltp(chain, target_strike, "PE")
            sl = round(entry_price * 0.70, 2)  # 30% SL
            tp = round(entry_price * 1.60, 2)  # 60% target

            return Signal(
                action=SignalAction.BUY,
                symbol=symbol,
                qty=DEFAULT_QTY * LOT_SIZE,
                price=0.0,  # market Order
                order_type=OrderType.MARKET,
                product_type=ProductType.MIS,
                exchange=Exchange.NFO,
                strategy_name=self.name,
                confidence=round(confidence, 3),
                reason=f"PCR={pcr:.2f} bullish. Buying {symbol}",
                stop_loss=sl,
                take_profit=tp,
                metadata={
                    "pcr": pcr,
                    "atm_strike": atm_strike,
                    "concentration": round(concentration, 3),
                    "ltp": round(ltp, 2),
                }
            )

        return self._hold(f"PCR {pcr:.2f} neutral. No clear direction")

    # Helpers
    def _hold(self, reason: str) -> Signal:
        return Signal(
            action=SignalAction.HOLD,
            symbol="",
            qty=0,
            price=0.0,
            strategy_name=self.name,
            reason=reason,
        )

    def _build_option_symbol(self, index: str, strike: float, option_type: str) -> str:
        """
        Build NSE NFO symbol string
        FORMAT: NIFTY24MAY22000CE
        """
        from datetime import datetime
        now = datetime.now()
        month = now.strftime("%b").upper()
        year = now.strftime("%Y")
        return f"{index}{year}{month}{int(strike)}{option_type}"

    def _get_option_ltp(self, chain: list[OptionChainEntry], strike: float, option_type: str) -> float:
        entry = next((e for e in chain if e.strike == strike), None)
        if not entry:
            return 100.0  # fallback price
        return entry.call_ltp if option_type == "CE" else entry.put_ltp
