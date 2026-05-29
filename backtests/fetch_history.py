"""
fetch_history.py — Download 15 months of OHLCV data for all 9 pairs.

Saves two CSVs per pair into backtests/data/:
  - {PAIR}_daily.csv   — daily candles (for regime classification)
  - {PAIR}_hourly.csv  — hourly candles (for strategy simulation)

Usage:
    python backtests/fetch_history.py

Data source: yfinance (Yahoo Finance) — free, no API key, 15+ months of hourly.
"""

import os
import time
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

# ── Pairs ─────────────────────────────────────────────────────────────────────
# Maps Binance pair → Yahoo Finance ticker
PAIRS = {
    "BTC/USDT" : "BTC-USD",
    "ETH/USDT" : "ETH-USD",
    "SOL/USDT" : "SOL-USD",
    "BNB/USDT" : "BNB-USD",
    "XRP/USDT" : "XRP-USD",
    "AVAX/USDT": "AVAX-USD",
    "DOGE/USDT": "DOGE-USD",
    "LINK/USDT": "LINK-USD",
    "ADA/USDT" : "ADA-USD",
}

OUTPUT_DIR    = "backtests/data"
LOOKBACK_DAYS = 450   # ~15 months


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Standardise column names and drop bad rows."""
    # Newer yfinance returns MultiIndex (metric, ticker) — flatten to first level
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [str(c).lower() for c in df.columns]

    df = df.rename(columns={"adj close": "adj_close", "adj_close": "adj_close"})

    # Keep only OHLCV columns that exist
    available = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[available].copy()
    df.dropna(inplace=True)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.index.name = "timestamp"
    return df


def fetch_pair(binance_pair: str, yf_ticker: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    end   = datetime.now()
    start = end - timedelta(days=LOOKBACK_DAYS)

    print(f"  [{binance_pair}] downloading daily ...", end=" ", flush=True)
    daily = yf.download(yf_ticker, start=start, end=end,
                        interval="1d", auto_adjust=True, progress=False)
    daily = clean(daily)
    print(f"{len(daily)} rows")

    time.sleep(1)   # be polite to Yahoo

    print(f"  [{binance_pair}] downloading hourly ...", end=" ", flush=True)
    # yfinance max 730 days for 1h; we take all available
    hourly = yf.download(yf_ticker, start=start, end=end,
                         interval="1h", auto_adjust=True, progress=False)
    hourly = clean(hourly)
    print(f"{len(hourly)} rows")

    return daily, hourly


def save(df: pd.DataFrame, path: str):
    df.to_csv(path)


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"\nFetching {len(PAIRS)} pairs — {LOOKBACK_DAYS} days lookback")
    print(f"Output → {OUTPUT_DIR}/\n")

    summary = []
    for binance_pair, yf_ticker in PAIRS.items():
        safe_name = binance_pair.replace("/", "")
        try:
            daily, hourly = fetch_pair(binance_pair, yf_ticker)
            save(daily,  f"{OUTPUT_DIR}/{safe_name}_daily.csv")
            save(hourly, f"{OUTPUT_DIR}/{safe_name}_hourly.csv")
            summary.append({
                "pair"        : binance_pair,
                "daily_rows"  : len(daily),
                "hourly_rows" : len(hourly),
                "daily_from"  : str(daily.index[0].date()),
                "daily_to"    : str(daily.index[-1].date()),
                "status"      : "✅ ok",
            })
        except Exception as e:
            print(f"  [{binance_pair}] ❌ failed: {e}")
            summary.append({"pair": binance_pair, "status": f"❌ {e}"})
        time.sleep(2)

    # Print summary table
    print("\n" + "─" * 64)
    print(f"  {'Pair':<12} {'Daily':>7} {'Hourly':>8}  {'From':>12}  {'To':>12}  Status")
    print("─" * 64)
    for s in summary:
        if "daily_rows" in s:
            print(f"  {s['pair']:<12} {s['daily_rows']:>7} {s['hourly_rows']:>8}  "
                  f"{s['daily_from']:>12}  {s['daily_to']:>12}  {s['status']}")
        else:
            print(f"  {s['pair']:<12}  {s['status']}")
    print("─" * 64)
    print(f"\nAll data saved to {OUTPUT_DIR}/\n")


if __name__ == "__main__":
    main()
