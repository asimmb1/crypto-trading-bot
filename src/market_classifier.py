"""
market_classifier.py — Detect current market regime from OHLCV data.

Regimes:
    BULL_TREND  — ADX > 25, price above SMA200, +DI > -DI
    BEAR_TREND  — ADX > 25, price below SMA200, -DI > +DI
    RANGING     — ADX < 20, price oscillating
    HIGH_VOL    — ATR > 2× its 90-day average (regardless of trend)
    LOW_VOL     — ATR < 0.5× its 90-day average

Classification uses DAILY candles for stability.
A regime must hold for MIN_CONFIRMATION_DAYS before it is reported.
"""

import numpy as np
import pandas as pd

# ── Constants ─────────────────────────────────────────────────────────────────
BULL_TREND = "BULL_TREND"
BEAR_TREND = "BEAR_TREND"
RANGING    = "RANGING"
HIGH_VOL   = "HIGH_VOL"
LOW_VOL    = "LOW_VOL"
UNKNOWN    = "UNKNOWN"

ALL_REGIMES = [BULL_TREND, BEAR_TREND, RANGING, HIGH_VOL, LOW_VOL]

ADX_TREND_THRESHOLD  = 25
ADX_RANGE_THRESHOLD  = 20
HIGH_VOL_MULTIPLIER  = 2.0
LOW_VOL_MULTIPLIER   = 0.5
ATR_BASELINE_PERIOD  = 90    # days for "normal" ATR average
ADX_PERIOD           = 14
SMA_PERIOD           = 200
MIN_CONFIRMATION_DAYS = 5


# ── Indicator calculations ────────────────────────────────────────────────────

def compute_atr(df: pd.DataFrame, period: int = ADX_PERIOD) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def compute_adx(df: pd.DataFrame, period: int = ADX_PERIOD) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (ADX, +DI, -DI)."""
    high, low, close = df["high"], df["low"], df["close"]

    up   = high - high.shift(1)
    down = low.shift(1) - low

    plus_dm  = pd.Series(np.where((up > down) & (up > 0), up, 0.0),   index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)

    atr      = compute_atr(df, period)
    plus_di  = 100 * plus_dm.ewm(span=period,  adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / atr

    dx  = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di)).replace([np.inf, -np.inf], np.nan).fillna(0)
    adx = dx.ewm(span=period, adjust=False).mean()

    return adx, plus_di, minus_di


def compute_sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


# ── Core classifier ───────────────────────────────────────────────────────────

class MarketClassifier:
    """
    Classifies market regime from a DataFrame of daily OHLCV candles.

    Usage:
        clf = MarketClassifier()
        regime = clf.classify(daily_df)          # latest regime
        tagged = clf.tag_all(daily_df)           # regime per row
    """

    def _indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["atr"]      = compute_atr(df)
        out["atr_base"] = out["atr"].rolling(ATR_BASELINE_PERIOD).mean()
        adx, pdi, mdi   = compute_adx(df)
        out["adx"]      = adx
        out["plus_di"]  = pdi
        out["minus_di"] = mdi
        out["sma200"]   = compute_sma(df["close"], SMA_PERIOD)
        return out

    def _row_regime(self, row: pd.Series) -> str:
        """Classify a single row (requires all indicator columns present)."""
        if pd.isna(row["adx"]) or pd.isna(row["sma200"]):
            return UNKNOWN

        atr      = row["atr"]
        atr_base = row["atr_base"] if not pd.isna(row["atr_base"]) else atr
        adx      = row["adx"]
        close    = row["close"]
        sma200   = row["sma200"]
        plus_di  = row["plus_di"]
        minus_di = row["minus_di"]

        # Volatility check takes priority
        if atr_base > 0:
            if atr > HIGH_VOL_MULTIPLIER * atr_base:
                return HIGH_VOL
            if atr < LOW_VOL_MULTIPLIER * atr_base:
                return LOW_VOL

        # Trend vs ranging
        if adx >= ADX_TREND_THRESHOLD:
            if close > sma200 and plus_di > minus_di:
                return BULL_TREND
            else:
                return BEAR_TREND
        elif adx <= ADX_RANGE_THRESHOLD:
            return RANGING
        else:
            # ADX between 20–25: lean on price vs MA
            return BULL_TREND if close > sma200 else BEAR_TREND

    def tag_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Returns df with a 'regime' column per row.
        Applies MIN_CONFIRMATION_DAYS smoothing — a regime only
        officially starts after holding for N consecutive days.
        """
        ind = self._indicators(df)
        ind["raw_regime"] = ind.apply(self._row_regime, axis=1)

        # Confirmation filter: require N consecutive days same regime
        confirmed = []
        buffer    = []
        last_confirmed = UNKNOWN

        for regime in ind["raw_regime"]:
            if buffer and regime != buffer[-1]:
                buffer = []
            buffer.append(regime)
            if len(buffer) >= MIN_CONFIRMATION_DAYS:
                last_confirmed = regime
            confirmed.append(last_confirmed)

        ind["regime"] = confirmed
        return ind

    def classify(self, df: pd.DataFrame) -> str:
        """Return the current (latest) confirmed regime."""
        tagged = self.tag_all(df)
        latest = tagged["regime"].iloc[-1]
        return latest if latest != UNKNOWN else RANGING

    def classify_live(self, exchange, pair: str, lookback_days: int = 250) -> str:
        """
        Fetch recent daily candles from the exchange and classify.
        Convenience wrapper for the adaptive bot.
        """
        import ccxt
        limit  = min(lookback_days, 500)
        ohlcv  = exchange.fetch_ohlcv(pair, "1d", limit=limit)
        df     = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        return self.classify(df)


# ── CLI quick-check ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    pair = sys.argv[1] if len(sys.argv) > 1 else "BTC/USDT"
    path = f"backtests/data/{pair.replace('/', '')}_daily.csv"

    try:
        df = pd.read_csv(path, index_col="timestamp", parse_dates=True)
    except FileNotFoundError:
        print(f"No data file found at {path}. Run fetch_history.py first.")
        sys.exit(1)

    clf    = MarketClassifier()
    tagged = clf.tag_all(df)
    regime = clf.classify(df)

    # Show last 30 days
    print(f"\nLast 30 days — {pair}")
    print("─" * 40)
    for ts, row in tagged.tail(30).iterrows():
        print(f"  {str(ts.date()):<12}  close=${row['close']:>10,.2f}  "
              f"adx={row['adx']:>5.1f}  regime={row['regime']}")
    print("─" * 40)
    print(f"\n  Current regime: {regime}\n")
