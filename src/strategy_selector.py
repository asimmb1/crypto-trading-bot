"""
strategy_selector.py — Select the best approved strategy for a pair + regime.

Loads the profitability matrix built by backtest_regime.py and answers:
  "Given BTC/USDT is in RANGING regime, should I run grid, dca, or sit out?"

Decision logic:
  1. If both grid and dca are approved → pick the higher avg_return
  2. If only one is approved → use that
  3. If neither approved → sit_out
"""

import json
import os
from loguru import logger

MATRIX_FILE = "logs/profitability_matrix.json"

SIT_OUT = "sit_out"
GRID    = "grid"
DCA     = "dca"


class StrategySelector:
    """
    Usage:
        sel = StrategySelector()
        strategy = sel.select("BTC/USDT", "RANGING")
        # → "grid" / "dca" / "sit_out"
    """

    def __init__(self, matrix_path: str = MATRIX_FILE):
        self._matrix = {}
        self._load(matrix_path)

    def _load(self, path: str):
        if not os.path.exists(path):
            logger.warning(
                f"Profitability matrix not found at {path}. "
                f"Run backtest_regime.py first. Defaulting to sit_out."
            )
            return
        with open(path) as f:
            data = json.load(f)
        self._matrix = data.get("pairs", {})
        logger.info(
            f"Strategy matrix loaded — {len(self._matrix)} pairs, "
            f"generated {data.get('generated_at', 'unknown')}"
        )

    def select(self, pair: str, regime: str) -> str:
        """
        Returns the best strategy string: "grid", "dca", or "sit_out".
        """
        if pair not in self._matrix:
            logger.warning(f"[{pair}] Not in profitability matrix → sit_out")
            return SIT_OUT

        regime_data = self._matrix[pair].get(regime, {})
        if not regime_data:
            logger.warning(f"[{pair}] No data for regime {regime} → sit_out")
            return SIT_OUT

        grid_info = regime_data.get(GRID, {})
        dca_info  = regime_data.get(DCA,  {})

        grid_ok  = grid_info.get("approved", False)
        dca_ok   = dca_info.get("approved",  False)

        if not grid_ok and not dca_ok:
            logger.info(f"[{pair}] {regime} → sit_out (no strategy approved)")
            return SIT_OUT

        if grid_ok and dca_ok:
            # Pick the higher average return
            grid_ret = grid_info.get("avg_return", 0)
            dca_ret  = dca_info.get("avg_return",  0)
            chosen   = GRID if grid_ret >= dca_ret else DCA
            logger.info(
                f"[{pair}] {regime} → {chosen} "
                f"(grid ${grid_ret:.2f} vs dca ${dca_ret:.2f})"
            )
            return chosen

        chosen = GRID if grid_ok else DCA
        logger.info(f"[{pair}] {regime} → {chosen} (only approved strategy)")
        return chosen

    def select_info(self, pair: str, regime: str) -> dict:
        """Extended version of select() that also returns performance stats."""
        strategy = self.select(pair, regime)
        info = {"strategy": strategy, "pair": pair, "regime": regime}

        if strategy != SIT_OUT and pair in self._matrix:
            regime_data = self._matrix[pair].get(regime, {})
            strat_data  = regime_data.get(strategy, {})
            info.update({
                "avg_return" : strat_data.get("avg_return",  0),
                "win_rate"   : strat_data.get("win_rate",    0),
                "occurrences": strat_data.get("occurrences", 0),
            })
        return info

    def approved_pairs(self) -> list[dict]:
        """Return all pair/regime/strategy combinations that are approved."""
        approved = []
        for pair, regimes in self._matrix.items():
            for regime, strategies in regimes.items():
                for strategy, info in strategies.items():
                    if info.get("approved"):
                        approved.append({
                            "pair"      : pair,
                            "regime"    : regime,
                            "strategy"  : strategy,
                            "avg_return": info.get("avg_return", 0),
                            "win_rate"  : info.get("win_rate",   0),
                        })
        return sorted(approved, key=lambda x: -x["avg_return"])

    def reload(self, matrix_path: str = MATRIX_FILE):
        """Hot-reload the matrix (call after running a fresh backtest)."""
        self._matrix = {}
        self._load(matrix_path)

    def summary(self) -> str:
        """Human-readable summary of the matrix."""
        lines = [f"Strategy Matrix — {len(self._matrix)} pairs\n"]
        for pair in self._matrix:
            line = f"  {pair:<12}"
            for regime, strategies in self._matrix[pair].items():
                for strat, info in strategies.items():
                    if info.get("approved"):
                        line += f"  ✅{regime[:4]}/{strat[:1]}"
            lines.append(line)
        return "\n".join(lines)
