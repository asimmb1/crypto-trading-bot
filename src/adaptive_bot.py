"""
adaptive_bot.py — Intelligent multi-pair trading orchestrator.

What it does every cycle:
  Every 30s  → Check circuit breaker (drawdown, exchange health)
  Every 10m  → Check dead man's switch (human confirmation)
  Every 4h   → Re-classify market regime for each active pair
               → Switch strategy if regime changed
               → Start new bot / stop old one

Pair lifecycle:
  approved pair + regime → select strategy → run in thread
  regime changes         → stop old thread → start new strategy
  circuit breaker trips  → stop ALL threads → lock system

Capital allocation:
  Total capital split equally across approved pairs.
  If 3 pairs active: each gets 1/3 of allocated capital.
"""

import json
import os
import time
import threading
import signal
from datetime import datetime, timedelta
from loguru import logger

from src.config import Config
from src.exchange import get_exchange
from src.market_classifier import MarketClassifier
from src.strategy_selector import StrategySelector, SIT_OUT
from src.circuit_breaker import CircuitBreaker
from src.dead_mans_switch import DeadMansSwitch
from src.notifier import notify
from src.database import get_daily_summary_by_pair
from src.health_server import register_shutdown_callback
from src.grid_bot import GridBot
from src.dca_bot import DCABot

logger.add("logs/adaptive_bot.log", rotation="1 day", retention="30 days", level="INFO")

# ── Timing ─────────────────────────────────────────────────────────────────────
REGIME_CHECK_INTERVAL  = 4 * 3600   # 4 hours
CB_CHECK_INTERVAL      = 30         # seconds
DMS_CHECK_INTERVAL     = 10 * 60    # 10 minutes

# ── All pairs to monitor ───────────────────────────────────────────────────────
# ACTIVE_PAIRS env var lets you restrict pairs without code changes.
# Default: all 9 pairs. Live start: ACTIVE_PAIRS=SOL/USDT,LINK/USDT
_env_pairs = os.environ.get("ACTIVE_PAIRS", "").strip()
ALL_PAIRS = (
    [p.strip() for p in _env_pairs.split(",") if p.strip()]
    if _env_pairs else
    ["BTC/USDT", "ETH/USDT", "SOL/USDT",
     "BNB/USDT", "XRP/USDT", "AVAX/USDT",
     "DOGE/USDT", "LINK/USDT", "ADA/USDT"]
)

# Capital per bot (from .env)
GRID_CAPITAL_PER_BOT = Config.GRID_TOTAL_CAPITAL or 100.0
DCA_BASE_PER_BOT     = Config.DCA_BASE_ORDER     or 50.0
DCA_SAFETY_PER_BOT   = Config.DCA_SAFETY_ORDER   or 30.0


class PairWorker:
    """Holds the thread and bot instance for one trading pair."""

    def __init__(self, pair: str, strategy: str, exchange):
        self.pair     = pair
        self.strategy = strategy
        self.bot      = None
        self.thread   = None
        self._start(exchange)

    def _start(self, exchange):
        if self.strategy == "grid":
            self.bot = GridBot(
                pair=self.pair,
                capital=GRID_CAPITAL_PER_BOT,
                exchange=exchange,
            )
            target = self.bot.run
        elif self.strategy == "dca":
            self.bot = DCABot(
                pair=self.pair,
                base_order=DCA_BASE_PER_BOT,
                safety_order=DCA_SAFETY_PER_BOT,
                exchange=exchange,
            )
            target = self.bot.run
        else:
            return

        self.thread = threading.Thread(
            target=target,
            name=f"{self.strategy.upper()}_{self.pair}",
            daemon=True,
        )
        self.thread.start()
        logger.info(f"Started {self.strategy.upper()} on {self.pair}")

    def stop(self):
        if self.bot:
            self.bot.stop()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=10)
        logger.info(f"Stopped {self.strategy.upper()} on {self.pair}")

    def is_alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


class AdaptiveBot:
    """
    Main orchestrator. Runs forever until circuit breaker trips
    or dead man's switch expires.
    """

    def __init__(self):
        self.exchange   = get_exchange("binance")
        self.classifier = MarketClassifier()
        self.selector   = StrategySelector()
        self.cb         = CircuitBreaker(
            starting_capital=GRID_CAPITAL_PER_BOT * len(ALL_PAIRS)
        )
        self.dms        = DeadMansSwitch()

        self.workers: dict[str, PairWorker] = {}  # pair → PairWorker
        self.regimes: dict[str, str]        = {}  # pair → current regime
        self.strategies: dict[str, str]     = {}  # pair → current strategy

        self._last_regime_check = datetime.utcnow() - timedelta(hours=5)
        self._last_dms_check    = datetime.utcnow() - timedelta(minutes=15)
        self._prev_prices: dict[str, float] = {}
        self._last_summary_day: str = None
        self._shutdown: bool = False

        # Railway sends SIGTERM before SIGKILL on redeploy (10s grace period).
        # Without a handler, the process dies immediately and cancel_all_orders()
        # never runs. This handler sets _shutdown = True so the main loop exits
        # cleanly through its normal shutdown path, which cancels all orders.
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT,  self._handle_signal)

        # Register dashboard shutdown callback with the health server.
        # The server starts before AdaptiveBot is constructed (see main.py),
        # so we register here instead of at server start time.
        register_shutdown_callback(self._initiate_shutdown)

    # ── Regime management ─────────────────────────────────────────────────────

    def _classify_all(self):
        """Classify regime for every pair and handle strategy switches."""
        logger.info("Running regime classification for all pairs...")
        changes = []

        for pair in ALL_PAIRS:
            try:
                regime = self.classifier.classify_live(self.exchange, pair)
            except Exception as e:
                logger.error(f"[{pair}] Regime classification failed: {e}")
                continue

            prev_regime   = self.regimes.get(pair)
            strategy      = self.selector.select(pair, regime)
            prev_strategy = self.strategies.get(pair)

            self.regimes[pair]    = regime
            self.strategies[pair] = strategy

            if strategy != prev_strategy or regime != prev_regime:
                changes.append((pair, prev_regime, regime, prev_strategy, strategy))
                self._switch_pair(pair, strategy)

        if changes:
            msg = "📊 **Regime Update**\n"
            for pair, pr, r, ps, s in changes:
                arrow = "→"
                msg += f"`{pair}`:  {pr or 'NEW'} {arrow} **{r}**  |  {ps or 'none'} {arrow} **{s}**\n"
            notify(msg)
        else:
            logger.info("No regime changes — all pairs unchanged.")

        self._last_regime_check = datetime.utcnow()

    def _switch_pair(self, pair: str, new_strategy: str):
        """Stop existing worker for a pair and start the new strategy."""
        # Stop existing
        if pair in self.workers:
            logger.info(f"[{pair}] Stopping current worker...")
            self.workers[pair].stop()
            del self.workers[pair]

        if new_strategy == SIT_OUT:
            logger.info(f"[{pair}] Sitting out — no approved strategy.")
            return

        # Start new
        try:
            worker = PairWorker(pair, new_strategy, self.exchange)
            self.workers[pair] = worker
        except Exception as e:
            logger.error(f"[{pair}] Failed to start {new_strategy}: {e}")

    # ── Balance snapshot ──────────────────────────────────────────────────────

    def _fetch_and_snapshot_balance(self) -> dict:
        """
        Fetch USDT balance from the exchange and write logs/balance.json so
        the dashboard Free USDT card always has current data.

        Called from both the normal CB-check path AND the CB-tripped sleep branch
        so the card updates even when the system is locked.

        Returns the raw balance dict (or {} on failure).
        """
        try:
            balance = self.exchange.fetch_balance()
            usdt    = balance.get("USDT", {})
            os.makedirs("logs", exist_ok=True)
            with open("logs/balance.json", "w") as f:
                json.dump({
                    "usdt_free":  round(float(usdt.get("free",  0)), 2),
                    "usdt_used":  round(float(usdt.get("used",  0)), 2),
                    "usdt_total": round(float(usdt.get("total", 0)), 2),
                    "updated_at": datetime.utcnow().isoformat(),
                }, f)
            return balance
        except Exception:
            return {}

    # ── Circuit breaker checks ────────────────────────────────────────────────

    def _check_circuit_breaker(self):
        if self.cb.is_tripped():
            return False

        # Portfolio drawdown check
        try:
            balance       = self._fetch_and_snapshot_balance()
            usdt_free     = balance.get("USDT", {}).get("free",  0)
            usdt_total    = balance.get("USDT", {}).get("total", 0)
            total_capital = usdt_free + usdt_total
            if self.cb.check_drawdown(total_capital):
                self._emergency_stop("Portfolio drawdown limit reached")
                return False
        except Exception as e:
            if self.cb.check_exchange(success=False):
                self._emergency_stop("Exchange health failure")
                return False
            logger.warning(f"Balance check failed: {e}")
            return True

        self.cb.check_exchange(success=True)

        # Crash velocity — check each active pair
        for pair in list(self.workers.keys()):
            try:
                ticker = self.exchange.fetch_ticker(pair)
                price  = ticker["last"]
                prev   = self._prev_prices.get(pair)
                if prev and self.cb.check_velocity(pair, prev, price):
                    self._emergency_stop(f"Crash velocity on {pair}")
                    return False
                self._prev_prices[pair] = price
            except Exception:
                pass

        return True

    def _clear_reconciled_orders(self):
        """
        Wipe logs/reconciled_orders.json after a full order cancellation.
        All orders are gone from the exchange — nothing is orphaned, so the
        dashboard panel should be empty on next load.
        """
        try:
            os.makedirs("logs", exist_ok=True)
            with open("logs/reconciled_orders.json", "w") as f:
                json.dump([], f)
            logger.info("Cleared reconciled_orders.json (all orders cancelled).")
        except Exception:
            pass

    def _emergency_stop(self, reason: str):
        """Halt all workers and cancel all exchange orders."""
        logger.critical(f"Emergency stop triggered: {reason}")
        for pair, worker in list(self.workers.items()):
            worker.stop()
        self.workers.clear()
        self.cb.emergency_cancel_all(self.exchange, ALL_PAIRS)
        self._clear_reconciled_orders()

    # ── Dead man's switch ─────────────────────────────────────────────────────

    def _check_dms(self) -> bool:
        if (datetime.utcnow() - self._last_dms_check).total_seconds() < DMS_CHECK_INTERVAL:
            return True
        self._last_dms_check = datetime.utcnow()
        alive = self.dms.check()
        if not alive:
            self._emergency_stop("Dead man's switch expired")
        return alive

    # ── Signal handling ───────────────────────────────────────────────────────

    def _handle_signal(self, signum, frame):
        """
        Handle SIGTERM (Railway redeploy) and SIGINT (Ctrl+C) gracefully.
        Sets _shutdown = True so the main loop exits through its normal path,
        which calls worker.stop() on every pair and cancel_all_orders() on exit.
        Railway allows 10 seconds between SIGTERM and SIGKILL — enough time for
        order cancellation if the exchange responds promptly.
        """
        sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        logger.warning(f"{sig_name} received — initiating clean shutdown")
        notify(f"⚠️ **Bot shutting down** ({sig_name}) — cancelling all orders before exit.")
        self._shutdown = True

    # ── Dashboard-initiated emergency shutdown ────────────────────────────────

    def _initiate_shutdown(self, sell_positions: bool):
        """
        Called from the health server thread when POST /shutdown is received.
        Runs in its own daemon thread so the HTTP response returns immediately.

        Sequence:
          1. Set _shutdown=True immediately (prevents watchdog from restarting stopped workers)
          2. Notify Discord
          3. Stop all pair workers (each cleans up its own orders via cancel_all_orders)
          4. If sell_positions=True → market sell all base assets to USDT
          5. Write system_state.json locked=True (prevents bot restart)
          6. Notify portfolio monitor (halt file on VPS, HTTP on Railway)
        """
        # Set shutdown flag immediately so the main loop's watchdog doesn't
        # restart workers that this method is in the process of stopping.
        self._shutdown = True
        logger.critical(f"Dashboard shutdown initiated (sell_positions={sell_positions})")
        notify(
            f"🛑 **Emergency Shutdown Initiated**\n"
            f"Source: Dashboard\n"
            f"Mode: {'Cancel orders + sell all to USDT' if sell_positions else 'Cancel orders only — positions held'}\n"
            f"Stopping all pair workers..."
        )

        # Stop all workers — each bot's cleanup code calls cancel_all_orders()
        for pair, worker in list(self.workers.items()):
            try:
                worker.stop()
                if worker.thread and worker.thread.is_alive():
                    worker.thread.join(timeout=15)  # wait up to 15s for cancel_all_orders
                logger.info(f"Worker stopped: {pair}")
            except Exception as e:
                logger.error(f"Error stopping worker {pair}: {e}")
        self.workers.clear()
        self._clear_reconciled_orders()  # all orders cancelled — dashboard should be empty

        # Market sell all positions if requested
        if sell_positions:
            self._market_sell_all()

        # Lock the system so the bot won't restart trading on next run
        os.makedirs("logs", exist_ok=True)
        with open("logs/system_state.json", "w") as f:
            json.dump({
                "locked":     True,
                "reason":     "Manual shutdown via dashboard",
                "tripped_at": datetime.utcnow().isoformat(),
            }, f, indent=2)

        # Notify portfolio monitor ─────────────────────────────────────────
        # VPS: write halt file into monitor's logs directory (shared filesystem)
        monitor_halt = "../02-portfolio-monitor/logs/portfolio_halt.json"
        try:
            os.makedirs(os.path.dirname(monitor_halt), exist_ok=True)
            with open(monitor_halt, "w") as f:
                json.dump({
                    "reason":   "Trading bot emergency shutdown from dashboard",
                    "halted_at": datetime.utcnow().isoformat(),
                }, f)
            logger.info("Portfolio monitor halt file written.")
        except Exception:
            pass  # not on VPS — try HTTP instead

        # Railway: POST to monitor's /halt endpoint if URL is configured
        monitor_url = os.environ.get("MONITOR_URL", "").rstrip("/")
        if monitor_url:
            try:
                import requests
                requests.post(
                    f"{monitor_url}/halt",
                    json={"reason": "Trading bot emergency shutdown from dashboard"},
                    timeout=5,
                )
                logger.info(f"Portfolio monitor halted via HTTP: {monitor_url}/halt")
            except Exception as e:
                logger.warning(f"Could not reach portfolio monitor at {monitor_url}: {e}")

        notify(
            f"✅ **Shutdown Complete**\n"
            f"All orders cancelled.\n"
            f"{'Positions converted to USDT.' if sell_positions else 'Positions held — review on exchange.'}\n"
            f"System locked. Run `python -m src.resume --confirm` to restart."
        )
        logger.critical("Shutdown complete.")

    def _market_sell_all(self):
        """
        Market sell all base-asset holdings across every pair.
        Called only when the user explicitly checks 'sell all to USDT'.

        Uses market orders — accepts current bid price with potential slippage.
        Skips any pair where held value < $1 (exchange minimum / dust).
        """
        notify("💱 **Converting all positions to USDT** (market orders)...")
        sold, failed = [], []

        for pair in ALL_PAIRS:
            base = pair.split("/")[0]  # "SOL" from "SOL/USDT"
            try:
                balance = self.exchange.fetch_balance()
                free = (balance.get(base) or {}).get("free") or 0.0
                if free <= 0:
                    continue
                # Skip dust below $1 equivalent
                try:
                    price = self.exchange.fetch_ticker(pair)["last"]
                except Exception:
                    price = 1.0
                if free * price < 1.0:
                    continue

                order = self.exchange.create_market_sell_order(pair, free)
                usd_value = free * price
                sold.append(f"{free:.4f} {base} (~${usd_value:,.2f})")
                logger.info(f"Market sold {free} {base} — order {order.get('id')}")
            except Exception as e:
                logger.error(f"Market sell failed for {pair}: {e}")
                failed.append(f"{base}: {e}")

        if sold:
            notify("✅ **Sold:**\n" + "\n".join(f"  • {s}" for s in sold))
        if failed:
            notify(
                "⚠️ **Sell failures — manual action needed:**\n"
                + "\n".join(f"  • {f}" for f in failed)
            )

    # ── Status report ─────────────────────────────────────────────────────────

    def _send_startup_report(self):
        active = [(p, s) for p, s in self.strategies.items() if s != SIT_OUT]
        sitting = [p for p, s in self.strategies.items() if s == SIT_OUT]

        msg = (
            f"🤖 **Adaptive Bot Started**\n"
            f"Pairs scanned: {len(ALL_PAIRS)}\n"
            f"Active trades: {len(active)}\n"
            f"Sitting out: {len(sitting)}\n\n"
        )
        for pair, strategy in active:
            regime = self.regimes.get(pair, "?")
            msg += f"  ✅ `{pair}` → **{strategy.upper()}** ({regime})\n"
        for pair in sitting:
            regime = self.regimes.get(pair, "?")
            msg += f"  ⏸ `{pair}` → sit out ({regime})\n"

        msg += f"\n🛡 Circuit breaker: armed\n⏰ Dead man's switch: armed (confirm every 24h)"
        notify(msg)

    # ── Daily summary ─────────────────────────────────────────────────────────

    def _maybe_send_daily_summary(self):
        """Send one consolidated daily P&L summary at 23:00 UTC, once per UTC day."""
        now = datetime.utcnow()
        today = now.strftime("%Y-%m-%d")
        if self._last_summary_day == today or now.hour < 23:
            return

        pairs_data = get_daily_summary_by_pair()
        total_trades = sum(p["count"] for p in pairs_data)
        total_pnl = sum(p["pnl"] for p in pairs_data)

        pnl_emoji = "✅" if total_pnl >= 0 else "🔴"
        msg = (
            f"📊 **Daily Summary — {today} UTC**\n"
            f"Trades: {total_trades} | P&L: {pnl_emoji} ${total_pnl:.4f}\n"
            f"Active pairs: {len(self.workers)}\n\n"
        )
        if pairs_data:
            for p in pairs_data:
                sign = "+" if p["pnl"] >= 0 else ""
                msg += f"  `{p['pair']}`: {p['count']} trades | {sign}${p['pnl']:.4f}\n"
        else:
            msg += "_No trades today._"

        notify(msg)
        self._last_summary_day = today
        logger.info(f"Daily summary sent: {total_trades} trades, P&L ${total_pnl:.4f}")

    # ── Main loop ────────────────────────────────────────────────────────────

    def run(self):
        logger.info("Adaptive Bot starting...")

        # Check if the system was locked by a previous session before starting any workers.
        # If we start workers first and then discover the CB is tripped, they run without
        # portfolio drawdown / DMS / velocity monitoring — dangerous for live trading.
        if self.cb.is_tripped():
            state_file = "logs/system_state.json"
            reason = "unknown"
            try:
                with open(state_file) as f:
                    reason = json.load(f).get("reason", reason)
            except Exception:
                pass
            logger.warning(f"System is locked from previous session ({reason}). No workers started.")
            notify(
                f"⚠️ **Bot started in LOCKED state**\n"
                f"Reason: {reason}\n"
                f"No trading workers started.\n"
                f"Run `python -m src.resume --confirm` to unlock and resume trading."
            )
            # Fall through to the main loop — the CB-tripped branch will handle it.
        else:
            # Normal startup — classify regimes and launch workers.
            self._classify_all()
            self._send_startup_report()

        while True:
            try:
                # Clean shutdown requested (SIGTERM / SIGINT)
                if self._shutdown:
                    break

                # Circuit breaker — every 30s
                if self.cb.is_tripped():
                    logger.warning("System locked by circuit breaker. Sleeping...")
                    self._fetch_and_snapshot_balance()  # keep dashboard current while locked
                    time.sleep(60)
                    continue

                if not self._check_circuit_breaker():
                    time.sleep(60)
                    continue

                # Dead man's switch — every 10m
                if not self._check_dms():
                    break

                # Daily summary — once at 23:00 UTC
                self._maybe_send_daily_summary()

                # Regime re-check — every 4h
                since_regime = (datetime.utcnow() - self._last_regime_check).total_seconds()
                if since_regime >= REGIME_CHECK_INTERVAL:
                    self._classify_all()

                # Restart any workers that crashed unexpectedly
                for pair, worker in list(self.workers.items()):
                    if not worker.is_alive():
                        strategy = self.strategies.get(pair, SIT_OUT)
                        if strategy != SIT_OUT:
                            logger.warning(f"[{pair}] Worker died unexpectedly — restarting {strategy}")
                            notify(f"⚠️ `{pair}` {strategy} worker crashed — restarting...")
                            self._switch_pair(pair, strategy)

                time.sleep(CB_CHECK_INTERVAL)

            except KeyboardInterrupt:
                logger.info("Adaptive Bot stopped by user.")
                notify("⛔ **Adaptive Bot stopped manually.**")
                break
            except Exception as e:
                logger.error(f"Adaptive Bot main loop error: {e}")
                notify(f"⚠️ Adaptive Bot error: {e}")
                time.sleep(60)

        # Shutdown all workers
        logger.info("Shutting down all pair workers...")
        for worker in self.workers.values():
            worker.stop()
        self.workers.clear()
        self._clear_reconciled_orders()  # all orders cancelled on exit — nothing is orphaned


if __name__ == "__main__":
    bot = AdaptiveBot()
    bot.run()
