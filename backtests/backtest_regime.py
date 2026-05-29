"""
backtest_regime.py — Run Grid + DCA in every regime for every pair.

Builds a profitability matrix:
  pair × regime × strategy → approved? avg_return? win_rate?

Approval criteria:
  - Strategy must have been tested in >= MIN_OCCURRENCES regime periods
  - Must be profitable (net P&L > 0) in >= WIN_RATE_THRESHOLD of those periods

Output saved to: logs/profitability_matrix.json

Usage:
    python backtests/backtest_regime.py
"""

import json
import os
import pandas as pd
import numpy as np
from datetime import datetime

from src.market_classifier import MarketClassifier, ALL_REGIMES, UNKNOWN

# ── Config ─────────────────────────────────────────────────────────────────────
DATA_DIR             = "backtests/data"
OUTPUT_FILE          = "logs/profitability_matrix.json"
MIN_OCCURRENCES      = 3       # need at least 3 regime periods to approve
WIN_RATE_THRESHOLD   = 0.60    # must be profitable in 60%+ of periods
MIN_REGIME_DAYS      = 5       # ignore regime periods shorter than this

# Grid config
GRID_CAPITAL         = 100.0
GRID_LEVELS          = 10
GRID_SPACING_PCT     = 1.0 / 100
GRID_STOP_LOSS_PCT   = 8.0 / 100
FEE_PCT              = 0.1  / 100

# DCA config
DCA_BASE_ORDER       = 50.0
DCA_SAFETY_ORDER     = 30.0
DCA_MAX_SAFETY       = 5
DCA_DROP_PCT         = 2.5 / 100
DCA_TP_PCT           = 3.0 / 100
DCA_MAX_CAPITAL      = DCA_BASE_ORDER + DCA_SAFETY_ORDER * DCA_MAX_SAFETY

PAIRS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT",
    "BNB/USDT", "XRP/USDT", "AVAX/USDT",
    "DOGE/USDT", "LINK/USDT", "ADA/USDT",
]


# ── Loaders ────────────────────────────────────────────────────────────────────

def load_daily(pair: str) -> pd.DataFrame | None:
    path = f"{DATA_DIR}/{pair.replace('/', '')}_daily.csv"
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, index_col="timestamp", parse_dates=True)
    return df


def load_hourly(pair: str) -> pd.DataFrame | None:
    path = f"{DATA_DIR}/{pair.replace('/', '')}_hourly.csv"
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, index_col="timestamp", parse_dates=True)
    return df


# ── Strategy simulators ────────────────────────────────────────────────────────

def simulate_grid(hourly: pd.DataFrame) -> float:
    """Return net P&L for a grid bot on this slice of hourly data."""
    if len(hourly) < 10:
        return 0.0

    entry_price  = hourly["close"].iloc[0]
    stop_price   = entry_price * (1 - GRID_STOP_LOSS_PCT)
    order_size   = GRID_CAPITAL / GRID_LEVELS
    amount_each  = order_size / entry_price

    half = GRID_LEVELS // 2
    levels = sorted([
        round(entry_price * (1 + i * GRID_SPACING_PCT), 6)
        for i in range(-half, half + 1) if i != 0
    ])
    sides = {lvl: ("buy" if lvl < entry_price else "sell") for lvl in levels}

    pnl = 0.0
    fees = 0.0

    for _, row in hourly.iterrows():
        if row["low"] <= stop_price:
            break
        for lvl in levels:
            side = sides[lvl]
            hit = (side == "buy" and row["low"] <= lvl) or \
                  (side == "sell" and row["high"] >= lvl)
            if hit:
                fee   = amount_each * lvl * FEE_PCT
                trade = amount_each * lvl * GRID_SPACING_PCT - fee
                pnl  += trade
                fees += fee
                sides[lvl] = "sell" if side == "buy" else "buy"

    return round(pnl - fees, 4)


def simulate_dca(hourly: pd.DataFrame) -> float:
    """Return net realised + unrealised P&L for a DCA bot on this slice."""
    if len(hourly) < 10:
        return 0.0

    position       = []
    safety_count   = 0
    last_buy       = None
    realised_pnl   = 0.0
    total_fees     = 0.0

    def avg_e():
        if not position:
            return 0.0
        cost = sum(p["p"] * p["a"] for p in position)
        amt  = sum(p["a"] for p in position)
        return cost / amt if amt else 0.0

    def total_a():
        return sum(p["a"] for p in position)

    def buy(price, usd):
        nonlocal total_fees
        amt         = usd / price
        fee         = amt * price * FEE_PCT
        total_fees += fee
        position.append({"p": price, "a": amt})

    buy(hourly["close"].iloc[0], DCA_BASE_ORDER)
    last_buy = hourly["close"].iloc[0]

    for _, row in hourly.iloc[1:].iterrows():
        if not position:
            buy(row["close"], DCA_BASE_ORDER)
            last_buy      = row["close"]
            safety_count  = 0
            continue

        if safety_count < DCA_MAX_SAFETY and last_buy:
            drop = (last_buy - row["low"]) / last_buy
            if drop >= DCA_DROP_PCT:
                buy(row["low"], DCA_SAFETY_ORDER)
                last_buy     = row["low"]
                safety_count += 1

        target = avg_e() * (1 + DCA_TP_PCT)
        if row["high"] >= target:
            amt            = total_a()
            sell_price     = target
            pnl            = (sell_price - avg_e()) * amt
            fee            = amt * sell_price * FEE_PCT
            total_fees    += fee
            realised_pnl  += pnl - fee
            position       = []
            safety_count   = 0
            last_buy       = None

    # Unrealised
    unrealised = 0.0
    if position:
        last_price = hourly["close"].iloc[-1]
        unrealised = (last_price - avg_e()) * total_a()

    return round(realised_pnl + unrealised, 4)


# ── Regime segmenter ───────────────────────────────────────────────────────────

def get_regime_segments(daily_tagged: pd.DataFrame) -> list[dict]:
    """
    Split the daily dataframe into contiguous regime segments.
    Returns list of {regime, start, end, days}.
    """
    segments = []
    current_regime = None
    start_date     = None

    for ts, row in daily_tagged.iterrows():
        regime = row["regime"]
        if regime == UNKNOWN:
            continue
        if regime != current_regime:
            if current_regime is not None:
                days = (ts - start_date).days
                if days >= MIN_REGIME_DAYS:
                    segments.append({
                        "regime": current_regime,
                        "start" : start_date,
                        "end"   : ts,
                        "days"  : days,
                    })
            current_regime = regime
            start_date     = ts

    # Final segment
    if current_regime and start_date:
        end  = daily_tagged.index[-1]
        days = (end - start_date).days
        if days >= MIN_REGIME_DAYS:
            segments.append({
                "regime": current_regime,
                "start" : start_date,
                "end"   : end,
                "days"  : days,
            })

    return segments


# ── Per-pair analysis ──────────────────────────────────────────────────────────

def analyse_pair(pair: str) -> dict | None:
    daily  = load_daily(pair)
    hourly = load_hourly(pair)

    if daily is None or hourly is None:
        print(f"  [{pair}] ⚠️  No data files found — skipping")
        return None

    clf    = MarketClassifier()
    tagged = clf.tag_all(daily)
    segs   = get_regime_segments(tagged)

    if not segs:
        print(f"  [{pair}] ⚠️  No valid regime segments found")
        return None

    # Collect results per regime per strategy
    results: dict[str, dict[str, list[float]]] = {
        r: {"grid": [], "dca": []} for r in ALL_REGIMES
    }

    for seg in segs:
        regime = seg["regime"]
        mask   = (hourly.index >= seg["start"]) & (hourly.index < seg["end"])
        h_slice = hourly[mask]

        if len(h_slice) < 20:
            continue

        grid_pnl = simulate_grid(h_slice)
        dca_pnl  = simulate_dca(h_slice)

        results[regime]["grid"].append(grid_pnl)
        results[regime]["dca"].append(dca_pnl)

    # Build approval matrix
    matrix = {}
    for regime in ALL_REGIMES:
        matrix[regime] = {}
        for strategy in ["grid", "dca"]:
            pnls = results[regime][strategy]
            if len(pnls) < MIN_OCCURRENCES:
                matrix[regime][strategy] = {
                    "approved"    : False,
                    "reason"      : f"only {len(pnls)} occurrences (need {MIN_OCCURRENCES})",
                    "occurrences" : len(pnls),
                    "avg_return"  : 0.0,
                    "win_rate"    : 0.0,
                }
                continue

            wins     = sum(1 for p in pnls if p > 0)
            win_rate = wins / len(pnls)
            avg_ret  = round(np.mean(pnls), 2)
            approved = win_rate >= WIN_RATE_THRESHOLD and avg_ret > 0

            matrix[regime][strategy] = {
                "approved"    : approved,
                "reason"      : "ok" if approved else f"win_rate {win_rate:.0%} or avg_return ${avg_ret:.2f}",
                "occurrences" : len(pnls),
                "avg_return"  : avg_ret,
                "win_rate"    : round(win_rate, 2),
                "all_returns" : [round(p, 2) for p in pnls],
            }

    return matrix


# ── Main ───────────────────────────────────────────────────────────────────────

def print_matrix(pair: str, matrix: dict):
    print(f"\n  {pair}")
    print(f"  {'Regime':<14} {'Grid':>20}  {'DCA':>20}")
    print(f"  {'─'*14} {'─'*20}  {'─'*20}")
    for regime in ALL_REGIMES:
        g = matrix[regime]["grid"]
        d = matrix[regime]["dca"]
        g_str = f"✅ ${g['avg_return']:+.2f} ({g['win_rate']:.0%})" \
                if g["approved"] else f"❌ ${g['avg_return']:+.2f} ({g.get('win_rate', 0):.0%})"
        d_str = f"✅ ${d['avg_return']:+.2f} ({d['win_rate']:.0%})" \
                if d["approved"] else f"❌ ${d['avg_return']:+.2f} ({d.get('win_rate', 0):.0%})"
        print(f"  {regime:<14} {g_str:>20}  {d_str:>20}")


def main():
    print(f"\n{'═'*60}")
    print(f"  REGIME BACKTEST — {len(PAIRS)} pairs")
    print(f"  Criteria: {MIN_OCCURRENCES}+ occurrences, {WIN_RATE_THRESHOLD:.0%}+ win rate")
    print(f"{'═'*60}")

    os.makedirs("logs", exist_ok=True)
    full_matrix = {
        "generated_at"       : datetime.utcnow().isoformat(),
        "min_occurrences"    : MIN_OCCURRENCES,
        "win_rate_threshold" : WIN_RATE_THRESHOLD,
        "pairs"              : {},
    }

    approved_summary = []

    for pair in PAIRS:
        print(f"\n  Analysing {pair} ...", end=" ", flush=True)
        result = analyse_pair(pair)
        if result is None:
            continue
        full_matrix["pairs"][pair] = result
        print("done")
        print_matrix(pair, result)

        # Collect what's approved for summary
        for regime in ALL_REGIMES:
            for strategy in ["grid", "dca"]:
                if result[regime][strategy]["approved"]:
                    approved_summary.append({
                        "pair"      : pair,
                        "regime"    : regime,
                        "strategy"  : strategy,
                        "avg_return": result[regime][strategy]["avg_return"],
                    })

    # Save matrix — convert numpy types to native Python for JSON
    def to_native(obj):
        if isinstance(obj, dict):
            return {k: to_native(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [to_native(v) for v in obj]
        if hasattr(obj, "item"):          # numpy scalar
            return obj.item()
        if isinstance(obj, bool):
            return bool(obj)
        return obj

    with open(OUTPUT_FILE, "w") as f:
        json.dump(to_native(full_matrix), f, indent=2)

    # Print approved deployments
    print(f"\n{'═'*60}")
    print(f"  APPROVED DEPLOYMENTS ({len(approved_summary)} total)")
    print(f"{'═'*60}")
    if approved_summary:
        for a in sorted(approved_summary, key=lambda x: -x["avg_return"]):
            print(f"  ✅  {a['pair']:<12} {a['regime']:<14} {a['strategy']:<6} "
                  f"avg ${a['avg_return']:+.2f}")
    else:
        print("  ⚠️  No strategies approved. Market conditions unfavourable.")
        print("  Adaptive bot will sit out all pairs until conditions improve.")

    print(f"\n  Matrix saved to {OUTPUT_FILE}\n")


if __name__ == "__main__":
    main()
