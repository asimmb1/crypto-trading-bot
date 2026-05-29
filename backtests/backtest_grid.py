"""
Grid Bot Backtest — BTC/USDT — Month-by-Month + 6-Month Accumulated
Fetches 6 months of 1h OHLCV from Binance and simulates the grid strategy.

Usage:
    python backtests/backtest_grid.py
"""

import json
import os
import ccxt
import pandas as pd

# ── Config ─────────────────────────────────────────────────────────────────────
PAIR           = "BTC/USDT"
TIMEFRAME      = "1h"
CANDLES        = 4380          # ~6 months of hourly data
TOTAL_CAPITAL  = 100.0         # USDT
NUM_LEVELS     = 10
SPACING_PCT    = 1.0 / 100
STOP_LOSS_PCT  = 8.0 / 100
FEE_PCT        = 0.1  / 100


def fetch_ohlcv() -> pd.DataFrame:
    print(f"Fetching {CANDLES} × {TIMEFRAME} candles for {PAIR} ...")
    exchange = ccxt.binance()
    raw = exchange.fetch_ohlcv(PAIR, TIMEFRAME, limit=CANDLES)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    print(f"  {len(df)} candles  |  {df.index[0].date()}  →  {df.index[-1].date()}\n")
    return df


def calculate_grid(center: float) -> list[float]:
    half = NUM_LEVELS // 2
    levels = []
    for i in range(-half, half + 1):
        if i != 0:
            levels.append(round(center * (1 + i * SPACING_PCT), 2))
    return sorted(levels)


def simulate_month(df: pd.DataFrame) -> dict:
    """Run a full grid simulation on a slice of OHLCV data."""
    entry_price = df["close"].iloc[0]
    grid_levels = calculate_grid(entry_price)
    order_size  = TOTAL_CAPITAL / NUM_LEVELS
    amount_each = order_size / entry_price
    stop_price  = entry_price * (1 - STOP_LOSS_PCT)

    order_sides = {
        lvl: ("buy" if lvl < entry_price else "sell")
        for lvl in grid_levels
    }

    fills      = 0
    grid_pnl   = 0.0
    total_fees = 0.0
    stopped    = False

    for ts, row in df.iterrows():
        if row["low"] <= stop_price:
            stopped = True
            break

        for lvl in grid_levels:
            side = order_sides[lvl]
            hit  = (side == "buy" and row["low"] <= lvl) or \
                   (side == "sell" and row["high"] >= lvl)
            if hit:
                fee        = amount_each * lvl * FEE_PCT
                pnl        = amount_each * lvl * SPACING_PCT - fee
                total_fees += fee
                grid_pnl   += pnl
                fills      += 1
                order_sides[lvl] = "sell" if side == "buy" else "buy"

    final_price  = df["close"].iloc[-1]
    price_change = (final_price - entry_price) / entry_price * 100
    net_pnl      = grid_pnl - total_fees

    return {
        "entry_price"     : entry_price,
        "final_price"     : final_price,
        "price_change_pct": round(price_change, 2),
        "fills"           : fills,
        "grid_pnl"        : round(grid_pnl, 2),
        "fees"            : round(total_fees, 2),
        "net_pnl"         : round(net_pnl, 2),
        "return_pct"      : round(net_pnl / TOTAL_CAPITAL * 100, 2),
        "stop_loss_hit"   : stopped,
        "candles"         : len(df),
    }


def print_monthly_row(label: str, r: dict):
    sl = " 🛑 STOPPED" if r["stop_loss_hit"] else ""
    print(
        f"  {label:<12} | "
        f"BTC {r['price_change_pct']:>+6.1f}%  | "
        f"Fills: {r['fills']:>3}  | "
        f"Net P&L: ${r['net_pnl']:>7.2f}  | "
        f"Return: {r['return_pct']:>+6.2f}%{sl}"
    )


def print_summary(months: list[dict], labels: list[str]):
    divider = "─" * 72

    print(f"\n{divider}")
    print(f"  GRID BOT BACKTEST — {PAIR}  |  ${TOTAL_CAPITAL} capital  |  "
          f"{NUM_LEVELS} levels @ {SPACING_PCT*100}% spacing")
    print(divider)
    print(f"  {'Month':<12} | {'BTC Move':>10}  | {'Fills':>8}  | "
          f"{'Net P&L':>12}  | {'Return':>8}")
    print(divider)

    total_fills  = 0
    total_pnl    = 0.0
    total_fees   = 0.0
    stops_hit    = 0

    for label, r in zip(labels, months):
        print_monthly_row(label, r)
        total_fills += r["fills"]
        total_pnl   += r["net_pnl"]
        total_fees  += r["fees"]
        if r["stop_loss_hit"]:
            stops_hit += 1

    print(divider)
    total_return = total_pnl / TOTAL_CAPITAL * 100
    print(
        f"  {'6-MONTH TOTAL':<12} | {'':>10}  | "
        f"Fills: {total_fills:>3}  | "
        f"Net P&L: ${total_pnl:>7.2f}  | "
        f"Return: {total_return:>+6.2f}%"
    )
    print(divider)
    print(f"\n  Total fees paid : ${total_fees:.2f}")
    print(f"  Stop losses hit : {stops_hit} / 6 months")
    avg_fills = total_fills / 6
    print(f"  Avg fills/month : {avg_fills:.0f}")
    print(f"  Avg P&L/month   : ${total_pnl/6:.2f}")
    print(divider)

    return {
        "pair"           : PAIR,
        "capital"        : TOTAL_CAPITAL,
        "num_levels"     : NUM_LEVELS,
        "spacing_pct"    : SPACING_PCT * 100,
        "stop_loss_pct"  : STOP_LOSS_PCT * 100,
        "total_fills"    : total_fills,
        "total_fees"     : round(total_fees, 2),
        "total_net_pnl"  : round(total_pnl, 2),
        "total_return_pct": round(total_return, 2),
        "stops_hit"      : stops_hit,
        "months"         : [{"label": l, **r} for l, r in zip(labels, months)],
    }


def main():
    df = fetch_ohlcv()

    # Split into calendar months
    monthly_groups = df.groupby(pd.Grouper(freq="ME"))
    months  = []
    labels  = []

    for period, group in monthly_groups:
        if len(group) < 100:   # skip tiny partial months
            continue
        label = period.strftime("%b %Y")
        result = simulate_month(group)
        months.append(result)
        labels.append(label)

    summary = print_summary(months, labels)

    os.makedirs("logs", exist_ok=True)
    out = "logs/backtest_grid_results.json"
    with open(out, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\n  Saved to {out}\n")


if __name__ == "__main__":
    main()
