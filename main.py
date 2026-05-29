"""
main.py — Trading bot entry point.

Modes:
  python main.py               → Adaptive mode (recommended): regime-aware,
                                 multi-pair, circuit breaker + dead man's switch
  python main.py --grid        → Single grid bot (BTC/USDT from .env)
  python main.py --dca         → Single DCA bot (ETH/USDT from .env)
  python main.py --backtest    → Run full regime backtest and update matrix
  python main.py --status      → Print system status and exit

Setup commands (run separately, not modes):
  python -m src.confirm        → Reset dead man's switch (run daily)
  python -m src.resume --confirm → Resume after circuit breaker trip
  python backtests/fetch_history.py   → Download historical data
  python backtests/backtest_regime.py → Build profitability matrix
"""

import argparse
import os
import sys

from loguru import logger
from src.config import Config

# Ensure logs/ directory exists (required on Railway fresh deploy)
os.makedirs("logs", exist_ok=True)


def run_adaptive():
    from src.adaptive_bot import AdaptiveBot
    bot = AdaptiveBot()
    bot.run()


def run_grid():
    from src.grid_bot import GridBot
    bot = GridBot()
    bot.run()


def run_dca():
    from src.dca_bot import DCABot
    bot = DCABot()
    bot.run()


def run_backtest():
    import subprocess
    print("\n[1/2] Fetching historical data...")
    subprocess.run([sys.executable, "backtests/fetch_history.py"], check=True)
    print("\n[2/2] Running regime backtest...")
    subprocess.run([sys.executable, "backtests/backtest_regime.py"], check=True)
    print("\n✅ Backtest complete. Run `python main.py` to start adaptive trading.\n")


def print_status():
    import json, os
    from src.dead_mans_switch import DeadMansSwitch
    from src.circuit_breaker import CircuitBreaker

    print("\n" + "═" * 50)
    print("  SYSTEM STATUS")
    print("═" * 50)
    print(f"  ENV: {Config.ENV.upper()}")

    # Circuit breaker
    state_file = "logs/system_state.json"
    if os.path.exists(state_file):
        with open(state_file) as f:
            state = json.load(f)
        locked = state.get("locked", False)
        print(f"  Circuit breaker: {'🔴 TRIPPED' if locked else '🟢 Armed'}")
        if locked:
            print(f"    Reason: {state.get('reason', '?')}")
            print(f"    At    : {state.get('tripped_at', '?')}")
    else:
        print("  Circuit breaker: 🟢 Armed (no trip on record)")

    # Dead man's switch
    dms = DeadMansSwitch()
    ds  = dms.status()
    print(f"  Dead man's switch: {'🔴 EXPIRED' if not ds['alive'] else '🟢 Alive'}")
    print(f"    Last confirmed : {ds['last_confirmed']}")
    print(f"    Hours since    : {ds['hours_since']:.1f}h")
    print(f"    Halts in       : {ds['hours_until_halt']:.1f}h")

    # Profitability matrix
    matrix_file = "logs/profitability_matrix.json"
    if os.path.exists(matrix_file):
        with open(matrix_file) as f:
            matrix = json.load(f)
        print(f"  Strategy matrix: ✅ Generated {matrix.get('generated_at', '?')[:10]}")
        pairs = matrix.get("pairs", {})
        approved = sum(
            1 for p in pairs.values()
            for r in p.values()
            for s in r.values()
            if s.get("approved")
        )
        print(f"    Approved strategies: {approved}")
    else:
        print("  Strategy matrix: ⚠️  Not built — run `python main.py --backtest`")

    print("═" * 50 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Adaptive Crypto Trading Bot")
    parser.add_argument("--grid",      action="store_true", help="Run grid bot only")
    parser.add_argument("--dca",       action="store_true", help="Run DCA bot only")
    parser.add_argument("--backtest",  action="store_true", help="Run regime backtest")
    parser.add_argument("--status",    action="store_true", help="Print system status")
    args = parser.parse_args()

    if args.status:
        print_status()
        return

    if args.backtest:
        run_backtest()
        return

    # Validate config before starting any bot
    try:
        Config.validate()
    except ValueError as e:
        print(f"\n❌ Config error: {e}")
        print("   → Edit your .env file and fill in the missing values.\n")
        sys.exit(1)

    if args.grid:
        print(f"\n🚀 Grid Bot only  [ENV={Config.ENV.upper()}]")
        run_grid()
    elif args.dca:
        print(f"\n🚀 DCA Bot only  [ENV={Config.ENV.upper()}]")
        run_dca()
    else:
        print(f"\n🚀 Adaptive Bot  [ENV={Config.ENV.upper()}]")
        print("   Circuit breaker + dead man's switch armed.")
        print("   Run `python -m src.confirm` daily (or POST /confirm) to keep bots alive.\n")
        # Start health server immediately so Railway health check passes
        from src.health_server import start_health_server
        start_health_server()
        # Generate profitability matrix on first deploy if missing
        matrix_file = "logs/profitability_matrix.json"
        if not os.path.exists(matrix_file):
            import subprocess
            logger.info("Profitability matrix not found — running backtest (first deploy)...")
            subprocess.run([sys.executable, "backtests/backtest_regime.py"], check=True)
        run_adaptive()


if __name__ == "__main__":
    main()
