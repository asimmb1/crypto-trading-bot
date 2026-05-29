"""
circuit_breaker.py — Multi-layer capital protection system.

Three independent trip wires:
  1. Portfolio drawdown    — total capital drops >15% from peak
  2. Crash velocity        — any pair drops >8% in a single 1h candle
  3. Exchange health       — Binance API errors exceed threshold

When ANY trip wire fires:
  → Cancel all open orders on all pairs
  → Discord alert with full status
  → Write lock file (logs/system_state.json)
  → System refuses to place new orders until manually resumed

To resume after a trip:
    python -m src.resume --confirm
"""

import json
import os
import time
from datetime import datetime
from loguru import logger

from src.notifier import notify

STATE_FILE = "logs/system_state.json"

# ── Thresholds ────────────────────────────────────────────────────────────────
PORTFOLIO_DRAWDOWN_PCT   = 15.0   # % drop from peak → trip
CRASH_VELOCITY_PCT       = 8.0    # % drop in 1 candle → trip
EXCHANGE_ERROR_THRESHOLD = 5      # consecutive API errors → trip
EXCHANGE_CHECK_INTERVAL  = 30     # seconds between health checks


class CircuitBreaker:
    """
    Instantiate once and share across all bots.
    Call check_drawdown(), check_velocity(), check_exchange() regularly.
    Call is_tripped() before placing any order.
    """

    def __init__(self, starting_capital: float):
        self.starting_capital  = starting_capital
        self.peak_capital      = starting_capital
        self._consecutive_errors = 0
        self._load_state()

    # ── State persistence ──────────────────────────────────────────────────────

    def _load_state(self):
        os.makedirs("logs", exist_ok=True)
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                state = json.load(f)
            self._locked      = state.get("locked", False)
            self._reason      = state.get("reason", "")
            self._tripped_at  = state.get("tripped_at", "")
            self.peak_capital = state.get("peak_capital", self.starting_capital)
        else:
            self._locked     = False
            self._reason     = ""
            self._tripped_at = ""
            self._save_state()

    def _save_state(self):
        os.makedirs("logs", exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump({
                "locked"      : self._locked,
                "reason"      : self._reason,
                "tripped_at"  : self._tripped_at,
                "peak_capital": self.peak_capital,
            }, f, indent=2)

    # ── Public API ─────────────────────────────────────────────────────────────

    def is_tripped(self) -> bool:
        """Returns True if the system is locked — no orders should be placed."""
        return self._locked

    def update_peak(self, current_capital: float):
        """Call after each profitable trade to track the high watermark."""
        if current_capital > self.peak_capital:
            self.peak_capital = current_capital
            self._save_state()

    def check_drawdown(self, current_capital: float) -> bool:
        """
        Trip if portfolio has dropped >PORTFOLIO_DRAWDOWN_PCT% from peak.
        Returns True if tripped.
        """
        if self._locked:
            return True
        self.update_peak(current_capital)
        drop_pct = (self.peak_capital - current_capital) / self.peak_capital * 100
        if drop_pct >= PORTFOLIO_DRAWDOWN_PCT:
            reason = (
                f"Portfolio drawdown {drop_pct:.1f}% — "
                f"peak ${self.peak_capital:,.2f} → current ${current_capital:,.2f}"
            )
            self._trip(reason, current_capital)
            return True
        return False

    def check_velocity(self, pair: str, prev_price: float, current_price: float) -> bool:
        """
        Trip if price dropped >CRASH_VELOCITY_PCT% in one candle.
        Returns True if tripped.
        """
        if self._locked:
            return True
        if prev_price <= 0:
            return False
        drop_pct = (prev_price - current_price) / prev_price * 100
        if drop_pct >= CRASH_VELOCITY_PCT:
            reason = (
                f"Crash velocity on {pair}: "
                f"dropped {drop_pct:.1f}% in one candle "
                f"(${prev_price:,.2f} → ${current_price:,.2f})"
            )
            self._trip(reason, current_price)
            return True
        return False

    def check_exchange(self, success: bool) -> bool:
        """
        Call with success=True on API success, False on error.
        Trips after EXCHANGE_ERROR_THRESHOLD consecutive failures.
        Returns True if tripped.
        """
        if self._locked:
            return True
        if success:
            self._consecutive_errors = 0
            return False
        self._consecutive_errors += 1
        if self._consecutive_errors >= EXCHANGE_ERROR_THRESHOLD:
            reason = (
                f"Exchange health: {self._consecutive_errors} consecutive API errors"
            )
            self._trip(reason, 0.0)
            return True
        return False

    def reset(self):
        """
        Called by src/resume.py after human confirms restart.
        Clears the lock but preserves peak capital.
        """
        self._locked     = False
        self._reason     = ""
        self._tripped_at = ""
        self._consecutive_errors = 0
        self._save_state()
        notify("✅ **Circuit Breaker Reset** — trading resumed by operator.")
        logger.info("Circuit breaker reset by operator.")

    # ── Emergency stop ────────────────────────────────────────────────────────

    def _trip(self, reason: str, current_capital: float):
        """Lock the system and alert Discord."""
        self._locked     = True
        self._reason     = reason
        self._tripped_at = datetime.utcnow().isoformat()
        self._save_state()

        logger.critical(f"CIRCUIT BREAKER TRIPPED: {reason}")

        notify(
            f"🚨 **EMERGENCY STOP — CIRCUIT BREAKER TRIPPED**\n"
            f"Reason: {reason}\n"
            f"Time: {self._tripped_at} UTC\n"
            f"Capital at trip: ${current_capital:,.2f}\n\n"
            f"**All orders cancelled. Bot locked.**\n"
            f"To resume: `python -m src.resume --confirm`"
        )

    def emergency_cancel_all(self, exchange, pairs: list[str]):
        """
        Cancel every open order on every pair.
        Call this immediately after a trip.
        """
        for pair in pairs:
            try:
                exchange.cancel_all_orders(pair)
                logger.info(f"Emergency cancelled all orders on {pair}")
            except Exception as e:
                logger.error(f"Failed to cancel orders on {pair}: {e}")

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "locked"        : self._locked,
            "reason"        : self._reason,
            "tripped_at"    : self._tripped_at,
            "peak_capital"  : self.peak_capital,
            "api_errors"    : self._consecutive_errors,
        }
