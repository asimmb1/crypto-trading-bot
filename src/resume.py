"""
resume.py — Manually resume trading after a circuit breaker trip.

Usage:
    python -m src.resume --confirm

The --confirm flag is required to prevent accidental restarts.
"""

import sys
import json
import os
from src.circuit_breaker import CircuitBreaker
from src.config import Config


def main():
    if "--confirm" not in sys.argv:
        print("\n⚠️  Safety check: pass --confirm to resume trading.")
        print("   Usage: python -m src.resume --confirm\n")
        sys.exit(1)

    # Load state file to show what tripped it
    state_file = "logs/system_state.json"
    if os.path.exists(state_file):
        with open(state_file) as f:
            state = json.load(f)
        if not state.get("locked"):
            print("\n✅ System is not locked — nothing to resume.\n")
            sys.exit(0)
        print(f"\n  Trip reason  : {state.get('reason', 'unknown')}")
        print(f"  Tripped at   : {state.get('tripped_at', 'unknown')}")
        print(f"  Peak capital : ${state.get('peak_capital', 0):,.2f}\n")
    else:
        print("\nNo state file found.\n")

    capital = Config.GRID_TOTAL_CAPITAL or 100.0
    cb = CircuitBreaker(starting_capital=capital)
    cb.reset()

    print("✅ Circuit breaker cleared.")
    print("   Run `python main.py` to restart the bots.\n")


if __name__ == "__main__":
    main()
