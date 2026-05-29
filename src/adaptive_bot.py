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

import time
import threading
from datetime import datetime, timedelta
from loguru import logger

from src.config import Config
from src.exchange import get_exchange
from src.market_classifier import MarketClassifier
from src.strategy_selector import StrategySelector, SIT_OUT
from src.circuit_breaker import CircuitBreaker
from src.dead_mans_switch import DeadMansSwitch
from src.notifier import notify
from src.grid_bot import GridBot
from src.dca_bot import DCABot

logger.add("logs/adaptive_bot.log", rotation="1 day", retention="30 days", level="INFO")

# ── Timing ─────────────────────────────────────────────────────────────────────
REGIME_CHECK_INTERVAL  = 4 * 3600   # 4 hours
CB_CHECK_INTERVAL      = 30         # seconds
DMS_CHECK_INTERVAL     = 10 * 60    # 10 minutes

# ── All pairs to monitor ───────────────────────────────────────────────────────
ALL_PAIRS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT",
    "BNB/USDT", "XRP/USDT", "AVAX/USDT",
    "DOGE/USDT", "LINK/USDT", "ADA/USDT",
]

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

    # ── Circuit breaker checks ────────────────────────────────────────────────

    def _check_circuit_breaker(self):
        if self.cb.is_tripped():
            return False

        # Portfolio drawdown check
        try:
            balance     = self.exchange.fetch_balance()
            usdt_free   = balance.get("USDT", {}).get("free", 0)
            usdt_total  = balance.get("USDT", {}).get("total", 0)
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

    def _emergency_stop(self, reason: str):
        """Halt all workers and cancel all exchange orders."""
        logger.critical(f"Emergency stop triggered: {reason}")
        for pair, worker in list(self.workers.items()):
            worker.stop()
        self.workers.clear()
        self.cb.emergency_cancel_all(self.exchange, ALL_PAIRS)

    # ── Dead man's switch ─────────────────────────────────────────────────────

    def _check_dms(self) -> bool:
        if (datetime.utcnow() - self._last_dms_check).total_seconds() < DMS_CHECK_INTERVAL:
            return True
        self._last_dms_check = datetime.utcnow()
        alive = self.dms.check()
        if not alive:
            self._emergency_stop("Dead man's switch expired")
        return alive

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

    # ── Main loop ────────────────────────────────────────────────────────────

    def run(self):
        logger.info("Adaptive Bot starting...")

        # Initial regime classification
        self._classify_all()
        self._send_startup_report()

        while True:
            try:
                # Circuit breaker — every 30s
                if self.cb.is_tripped():
                    logger.warning("System locked by circuit breaker. Sleeping...")
                    time.sleep(60)
                    continue

                if not self._check_circuit_breaker():
                    time.sleep(60)
                    continue

                # Dead man's switch — every 10m
                if not self._check_dms():
                    break

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


if __name__ == "__main__":
    bot = AdaptiveBot()
    bot.run()
