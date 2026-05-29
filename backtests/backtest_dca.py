"""
DCA Bot Backtest — ETH/USDT — Month-by-Month + 6-Month Accumulated
Fetches 6 months of 1h OHLCV from Binance and simulates the DCA strategy.

Usage:
    python backtests/backtest_dca.py
"""

import json
import os
import ccxt
import pandas as pd

# ── Config ─────────────────────────────────────────────────────────────────────
PAIR              = "ETH/USDT"
TIMEFRAME         = "1h"
CANDLES           = 4380
BASE_ORDER        = 50.0
SAFETY_ORDER      = 30.0
MAX_SAFETY_ORDERS = 5
PRICE_DROP_PCT    = 2.5 / 100
TAKE_PROFIT_PCT   = 3.0 / 100
FEE_PCT           = 0.1  / 100

MAX_CAPITAL       = BASE_ORDER + SAFETY_ORDER * MAX_SAFETY_ORDERS  # $200 worst case


def fetch_ohlcv() -> pd.DataFrame:
    print(f"Fetching {CANDLES} × {TIMEFRAME} candles for {PAIR} ...")
    exchange = ccxt.binance()
    raw = exchange.fetch_ohlcv(PAIR, TIMEFRAME, limit=CANDLES)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    print(f"  {len(df)} candles  |  {df.index[0].date()}  →  {df.index[-1].date()}\n")
    return df


def simulate_month(df: pd.DataFrame) -> dict:
    """Run a full DCA simulation on a slice of OHLCV data."""
    position       = []
    safety_count   = 0
    last_buy_price = None
    cycles         = 0
    realised_pnl   = 0.0
    total_fees     = 0.0
    max_invested   = 0.0

    def avg_entry():
        if not position:
            return 0.0
        cost = sum(p["price"] * p["amount"] for p in position)
        amt  = sum(p["amount"] for p in position)
        return cost / amt if amt else 0.0

    def total_amount():
        return sum(p["amount"] for p in position)

    def total_invested():
        return sum(p["price"] * p["amount"] for p in position)

    def buy(price, usd):
        nonlocal total_fees, max_invested
        amount      = usd / price
        fee         = amount * price * FEE_PCT
        total_fees += fee
        position.append({"price": price, "amount": amount})
        invested = total_invested()
        if invested > max_invested:
            max_invested = invested

    # Initial base order
    buy(df["close"].iloc[0], BASE_ORDER)
    last_buy_price = df["close"].iloc[0]
    safety_count   = 0

    for ts, row in df.iloc[1:].iterrows():
        if not position:
            buy(row["close"], BASE_ORDER)
            last_buy_price = row["close"]
            safety_count   = 0
            continue

        # Safety order trigger
        if safety_count < MAX_SAFETY_ORDERS and last_buy_price:
            drop = (last_buy_price - row["low"]) / last_buy_price
            if drop >= PRICE_DROP_PCT:
                buy(row["low"], SAFETY_ORDER)
                last_buy_price = row["low"]
                safety_count  += 1

        # Take profit trigger
        target = avg_entry() * (1 + TAKE_PROFIT_PCT)
        if row["high"] >= target:
            sell_price  = target
            amt         = total_amount()
            pnl         = (sell_price - avg_entry()) * amt
            fee         = amt * sell_price * FEE_PCT
            total_fees += fee
            realised_pnl += pnl - fee
            cycles       += 1
            position      = []
            safety_count  = 0
            last_buy_price = None

    # Unrealised on open position at month end
    unrealised = 0.0
    if position:
        last_price = df["close"].iloc[-1]
        unrealised = (last_price - avg_entry()) * total_amount()

    net_pnl = realised_pnl + unrealised

    return {
        "entry_price"    : df["close"].iloc[0],
        "final_price"    : df["close"].iloc[-1],
        "price_change_pct": round(
            (df["close"].iloc[-1] - df["close"].iloc[0]) / df["close"].iloc[0] * 100, 2
        ),
        "cycles"         : cycles,
        "safety_orders"  : safety_count,
        "realised_pnl"   : round(realised_pnl, 2),
        "unrealised_pnl" : round(unrealised, 2),
        "net_pnl"        : round(net_pnl, 2),
        "fees"           : round(total_fees, 2),
        "return_pct"     : round(net_pnl / MAX_CAPITAL * 100, 2),
        "max_invested"   : round(max_invested, 2),
        "candles"        : len(df),
    }


def print_monthly_row(label: str, r: dict):
    open_pos = " (open)" if r["unrealised_pnl"] != 0 else ""
    print(
        f"  {label:<12} | "
        f"ETH {r['price_change_pct']:>+6.1f}%  | "
        f"Cycles: {r['cycles']:>2}  | "
        f"Net P&L: ${r['net_pnl']:>7.2f}  | "
        f"Return: {r['return_pct']:>+6.2f}%{open_pos}"
    )


def print_summary(months: list[dict], labels: list[str]):
    divider = "─" * 74

    print(f"\n{divider}")
    print(f"  DCA BOT BACKTEST — {PAIR}  |  Base: ${BASE_ORDER}  "
          f"Safety: ${SAFETY_ORDER} × {MAX_SAFETY_ORDERS}  |  "
          f"Max capital: ${MAX_CAPITAL:.0f}")
    print(divider)
    print(f"  {'Month':<12} | {'ETH Move':>10}  | {'Cycles':>8}  | "
          f"{'Net P&L':>12}  | {'Return':>8}")
    print(divider)

    total_cycles = 0
    total_pnl    = 0.0
    total_fees   = 0.0

    for label, r in zip(labels, months):
        print_monthly_row(label, r)
        total_cycles += r["cycles"]
        total_pnl    += r["net_pnl"]
        total_fees   += r["fees"]

    print(divider)
    total_return = total_pnl / MAX_CAPITAL * 100
    print(
        f"  {'6-MONTH TOTAL':<12} | {'':>10}  | "
        f"Cycles: {total_cycles:>2}  | "
        f"Net P&L: ${total_pnl:>7.2f}  | "
        f"Return: {total_return:>+6.2f}%"
    )
    print(divider)
    print(f"\n  Total fees paid  : ${total_fees:.2f}")
    print(f"  Avg cycles/month : {total_cycles/6:.1f}")
    print(f"  Avg P&L/month    : ${total_pnl/6:.2f}")
    print(divider)

    return {
        "pair"            : PAIR,
        "base_order"      : BASE_ORDER,
        "safety_order"    : SAFETY_ORDER,
        "max_safety_orders": MAX_SAFETY_ORDERS,
        "max_capital"     : MAX_CAPITAL,
        "total_cycles"    : total_cycles,
        "total_fees"      : round(total_fees, 2),
        "total_net_pnl"   : round(total_pnl, 2),
        "total_return_pct": round(total_return, 2),
        "months"          : [{"label": l, **r} for l, r in zip(labels, months)],
    }


def main():
    df = fetch_ohlcv()

    monthly_groups = df.groupby(pd.Grouper(freq="ME"))
    months  = []
    labels  = []

    for period, group in monthly_groups:
        if len(group) < 100:
            continue
        label = period.strftime("%b %Y")
        result = simulate_month(group)
        months.append(result)
        labels.append(label)

    summary = print_summary(months, labels)

    os.makedirs("logs", exist_ok=True)
    out = "logs/backtest_dca_results.json"
    with open(out, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\n  Saved to {out}\n")


if __name__ == "__main__":
    main()
