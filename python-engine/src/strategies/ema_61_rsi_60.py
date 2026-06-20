from datetime import datetime

import structlog
from nselib import capital_market
import pandas as pd
import yfinance as yf

from src.execution.signal import OrderType, Exchange
from src.execution.signal import Signal, SignalAction, ProductType
from src.strategies.base import BaseStrategy
from src.utils.market_data import MarketSnapshot

log = structlog.getLogger()
all_symbols_df = capital_market.equity_list()
all_symbols = all_symbols_df['SYMBOL'].tolist()

class Ema61Rsi60(BaseStrategy):

    @property
    def name(self) -> str:
        return "61-ema-60-rsi"

    def symbols(self) -> list[str]:
        return []

    async def analyze(self, snapshots: dict[str, MarketSnapshot]) -> Signal:
        now = datetime.now()
        # if not (now.hour == 15 and now.minute >= 20):
        #     return self._hold("Wainting for 3.20 PM")

        selected = []

        for symbol in all_symbols[:200]:
            try:
                ticker = f"{symbol}.NS"
                data = yf.Ticker(ticker)

                df = data.history(period="6mo", interval="1d")

                if len(df) < 100:
                    continue
                df = self.calculate_indicators(df)
                last = df.iloc[-1]
                prev = df.iloc[-2]

                condition = (
                        (prev['Close'] <= prev['EMA_61']) and
                        (last['Close'] > last['EMA_61']) and
                        (last['RSI'] > 60) and
                        (prev['RSI'] <= 60)
                )
                if not condition:
                    continue
                prev_close = prev['Close']
                curr_price = last['Close']

                pct_change = ((curr_price - prev_close) / prev_close) * 100
                selected.append({
                    "symbol": symbol,
                    "pct_change": pct_change,
                    "ltp": curr_price,
                    "rsi": last['RSI'],
                })
                log.warning(selected)
            except Exception as e:
                log.warning(f"{symbol} failed: {str(e)}")
                continue
        if not selected:
            return self._hold("No stocks matched")
        top_stocks = sorted(selected, key=lambda x: x['pct_change'], reverse=True)[:3]
        log.info("Top 3 stocks", stocks=top_stocks)
        best = top_stocks[0]

        sl = round(best['ltp'] * 0.95, 2)
        tp = round(best['ltp'] * 1.10, 2)

        return Signal(
            action=SignalAction.BUY,
            symbol=best['symbol'],
            qty=1,
            price=0.0,
            order_type=OrderType.MARKET,
            product_type=ProductType.NRML,
            exchange=Exchange.NSE,
            strategy_name=self.name,
            confidence=0.85,
            reason=f"Top gainer + EMA61 RSI60 brekout",
            stop_loss=sl,
            take_profit=tp,
            metadata={
                "top_3": top_stocks,
            }
        )

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df['EMA_61'] = df["Close"].ewm(span=61, adjust=False).mean()
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)

        avg_gain = gain.rolling(12).mean()
        avg_loss = loss.rolling(12).mean()

        rs = avg_gain / avg_loss
        df['RSI'] = 100 - (100 / (1 + rs))
        return df

    def _hold(self, reason: str) -> Signal:
        return Signal(
            action=SignalAction.HOLD,
            symbol="",
            qty=0,
            price=0.0,
            strategy_name=self.name,
            reason=reason
        )
