"""
dead_mans_switch.py — Requires daily human confirmation to keep bots running.

Flow:
  Every 24h → bot sends Discord ping asking you to confirm
  You have 6h to run: python -m src.confirm
  If you don't confirm within 30h total → system halts automatically

This guarantees a human reviews the system every single day.
If you're unavailable (travel, illness, emergency) — the bot stops
itself rather than trading blind.

Heartbeat state stored in: logs/heartbeat.json
"""

import json
import os
from datetime import datetime, timedelta
from loguru import logger

from src.notifier import notify

HEARTBEAT_FILE       = "logs/heartbeat.json"
CONFIRM_INTERVAL_H   = 24    # send reminder every 24 hours
GRACE_PERIOD_H       = 6     # user has 6h to confirm after reminder
MAX_SILENCE_H        = 30    # hard stop if no confirm in 30h total


class DeadMansSwitch:
    """
    Instantiate once in the adaptive bot.
    Call check() every 10 minutes in the main loop.
    """

    def __init__(self):
        self._load()

    # ── State ─────────────────────────────────────────────────────────────────

    def _load(self):
        os.makedirs("logs", exist_ok=True)
        if os.path.exists(HEARTBEAT_FILE):
            with open(HEARTBEAT_FILE) as f:
                data = json.load(f)
            self._last_confirmed  = datetime.fromisoformat(data["last_confirmed"])
            self._last_reminder   = datetime.fromisoformat(data["last_reminder"]) \
                                    if data.get("last_reminder") else None
            self._reminder_sent   = data.get("reminder_sent", False)
        else:
            # First run — initialise as confirmed now
            self._last_confirmed = datetime.utcnow()
            self._last_reminder  = None
            self._reminder_sent  = False
            self._save()

    def _save(self):
        os.makedirs("logs", exist_ok=True)
        with open(HEARTBEAT_FILE, "w") as f:
            json.dump({
                "last_confirmed": self._last_confirmed.isoformat(),
                "last_reminder" : self._last_reminder.isoformat() if self._last_reminder else None,
                "reminder_sent" : self._reminder_sent,
            }, f, indent=2)

    # ── Public API ────────────────────────────────────────────────────────────

    def confirm(self):
        """Reset the timer. Called by src/confirm.py."""
        self._last_confirmed = datetime.utcnow()
        self._last_reminder  = None
        self._reminder_sent  = False
        self._save()
        logger.info("Dead man's switch confirmed by operator.")
        notify("✅ **Heartbeat confirmed** — bots will continue running for the next 24h.")

    def check(self) -> bool:
        """
        Call every 10 minutes from the main loop.
        Returns True if system should keep running.
        Returns False if it should halt (no confirmation received).
        Also sends a reminder Discord ping when due.
        """
        now             = datetime.utcnow()
        hours_since     = (now - self._last_confirmed).total_seconds() / 3600

        # Hard stop — silence exceeded 30h
        if hours_since >= MAX_SILENCE_H:
            logger.critical(
                f"Dead man's switch expired — {hours_since:.1f}h since last confirmation."
            )
            notify(
                f"🛑 **DEAD MAN'S SWITCH — AUTO HALT**\n"
                f"No confirmation received in {hours_since:.1f} hours.\n"
                f"All bots stopped. To restart:\n"
                f"1. Run `python -m src.confirm`\n"
                f"2. Run `python main.py`"
            )
            return False

        # Send reminder when due (every 24h)
        if hours_since >= CONFIRM_INTERVAL_H and not self._reminder_sent:
            self._last_reminder = now
            self._reminder_sent = True
            self._save()
            hours_left = MAX_SILENCE_H - hours_since
            logger.info("Dead man's switch reminder sent.")
            notify(
                f"⏰ **Daily Heartbeat Check**\n"
                f"Bots have been running for {hours_since:.0f}h since last confirmation.\n"
                f"You have **{hours_left:.0f}h** to confirm before auto-halt.\n\n"
                f"To confirm: run `python -m src.confirm` in your terminal."
            )

        return True

    def hours_since_confirm(self) -> float:
        return (datetime.utcnow() - self._last_confirmed).total_seconds() / 3600

    def status(self) -> dict:
        hours = self.hours_since_confirm()
        return {
            "last_confirmed"    : self._last_confirmed.isoformat(),
            "hours_since"       : round(hours, 1),
            "reminder_sent"     : self._reminder_sent,
            "hours_until_halt"  : round(MAX_SILENCE_H - hours, 1),
            "alive"             : hours < MAX_SILENCE_H,
        }
