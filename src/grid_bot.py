import time
from loguru import logger
from src.exchange import get_exchange
from src.config import Config
from src.notifier import notify, notify_trade, notify_daily_summary
from src.database import log_trade, init_db, get_daily_summary

# Configure loguru to write to file
logger.add("logs/grid_bot.log", rotation="1 day", retention="14 days", level="INFO")


class GridBot:
    def __init__(self, pair: str = None, capital: float = None, exchange=None):
        self.exchange = exchange or get_exchange("binance")
        self.pair = pair or Config.GRID_PAIR
        self.total_capital = capital or Config.GRID_TOTAL_CAPITAL
        self.num_levels = Config.GRID_LEVELS
        self.spacing_pct = Config.GRID_SPACING_PCT / 100
        self.stop_loss_pct = Config.GRID_STOP_LOSS_PCT / 100
        self.order_size = self.total_capital / self.num_levels
        self.grid_levels: list[float] = []
        self.open_orders: dict = {}        # order_id -> {side, price, amount}
        self.entry_price: float = None
        self._last_summary_day: str = None
        self._running = True
        init_db()

    def stop(self):
        """Signal the bot to exit its main loop cleanly."""
        self._running = False
        logger.info(f"Grid Bot [{self.pair}] stop signal received.")

    # ── Price ────────────────────────────────────────────────────────────────

    def get_price(self) -> float:
        ticker = self.exchange.fetch_ticker(self.pair)
        return ticker["last"]

    # ── Grid calculation ─────────────────────────────────────────────────────

    def calculate_grid(self, center_price: float) -> list[float]:
        """Return price levels symmetrically above and below center_price."""
        levels = []
        half = self.num_levels // 2
        for i in range(-half, half + 1):
            if i != 0:
                level = center_price * (1 + i * self.spacing_pct)
                levels.append(round(level, 2))
        return sorted(levels)

    # ── Order placement ──────────────────────────────────────────────────────

    def place_initial_orders(self):
        current_price = self.get_price()
        self.entry_price = current_price
        self.grid_levels = self.calculate_grid(current_price)

        logger.info(
            f"Starting grid at ${current_price:,.2f} | "
            f"Levels: {len(self.grid_levels)} | "
            f"Spacing: {Config.GRID_SPACING_PCT}% | "
            f"Capital: ${self.total_capital}"
        )
        notify(
            f"🚀 **Grid Bot Started**\n"
            f"Pair: {self.pair}\n"
            f"Center: ${current_price:,.2f}\n"
            f"Levels: {len(self.grid_levels)}\n"
            f"Spacing: {Config.GRID_SPACING_PCT}%\n"
            f"Capital: ${self.total_capital}\n"
            f"Stop loss: {Config.GRID_STOP_LOSS_PCT}% below entry"
        )

        amount_per_order = self.order_size / current_price

        for level in self.grid_levels:
            try:
                if level < current_price:
                    order = self.exchange.create_limit_buy_order(
                        self.pair, amount_per_order, level
                    )
                    self.open_orders[order["id"]] = {
                        "side": "buy", "price": level, "amount": amount_per_order
                    }
                    logger.info(f"BUY  limit @ ${level:,.2f}")
                else:
                    order = self.exchange.create_limit_sell_order(
                        self.pair, amount_per_order, level
                    )
                    self.open_orders[order["id"]] = {
                        "side": "sell", "price": level, "amount": amount_per_order
                    }
                    logger.info(f"SELL limit @ ${level:,.2f}")
                time.sleep(0.1)  # Rate-limit protection
            except Exception as e:
                logger.error(f"Failed to place order at {level}: {e}")

    # ── Fill detection & counter-orders ──────────────────────────────────────

    def check_fills_and_reorder(self):
        """Detect filled orders and place the opposite counter-order."""
        try:
            current_orders = self.exchange.fetch_open_orders(self.pair)
        except Exception as e:
            logger.error(f"Failed to fetch open orders: {e}")
            return

        current_ids = {o["id"] for o in current_orders}

        for order_id, order_info in list(self.open_orders.items()):
            if order_id not in current_ids:
                # Order was filled
                del self.open_orders[order_id]
                logger.info(
                    f"Filled: {order_info['side'].upper()} @ ${order_info['price']:,.2f}"
                )
                notify_trade(
                    f"Grid {order_info['side'].upper()} Filled",
                    self.pair,
                    order_info["price"],
                    order_info["amount"],
                )
                log_trade(
                    "grid", self.pair,
                    order_info["side"],
                    order_info["price"],
                    order_info["amount"],
                )

                # Place counter-order
                try:
                    if order_info["side"] == "buy":
                        counter_price = round(
                            order_info["price"] * (1 + self.spacing_pct), 2
                        )
                        counter_side = "sell"
                        order = self.exchange.create_limit_sell_order(
                            self.pair, order_info["amount"], counter_price
                        )
                    else:
                        counter_price = round(
                            order_info["price"] * (1 - self.spacing_pct), 2
                        )
                        counter_side = "buy"
                        order = self.exchange.create_limit_buy_order(
                            self.pair, order_info["amount"], counter_price
                        )

                    self.open_orders[order["id"]] = {
                        "side": counter_side,
                        "price": counter_price,
                        "amount": order_info["amount"],
                    }
                    logger.info(
                        f"Counter {counter_side.upper()} placed @ ${counter_price:,.2f}"
                    )
                except Exception as e:
                    logger.error(f"Failed to place counter-order: {e}")

    # ── Stop loss ────────────────────────────────────────────────────────────

    def check_stop_loss(self) -> bool:
        """Returns True if stop-loss triggered and all orders cancelled."""
        if not self.entry_price:
            return False
        current_price = self.get_price()
        drop = (self.entry_price - current_price) / self.entry_price
        if drop >= self.stop_loss_pct:
            logger.warning(
                f"STOP LOSS triggered! Price dropped {drop * 100:.1f}% "
                f"from entry ${self.entry_price:,.2f}"
            )
            notify(
                f"🛑 **GRID STOP LOSS**\n"
                f"Price dropped {drop * 100:.1f}% from entry ${self.entry_price:,.2f}\n"
                f"Cancelling all orders."
            )
            try:
                self.exchange.cancel_all_orders(self.pair)
            except Exception as e:
                logger.error(f"Failed to cancel all orders: {e}")
            return True
        return False

    # ── Daily summary ────────────────────────────────────────────────────────

    def maybe_send_daily_summary(self):
        """Send a daily P&L summary once per UTC day."""
        from datetime import datetime
        today = datetime.utcnow().strftime("%Y-%m-%d")
        hour  = datetime.utcnow().hour
        if self._last_summary_day != today and hour >= 23:
            summary = get_daily_summary("grid")
            notify_daily_summary(
                "Grid Bot",
                summary["count"],
                summary["pnl"],
                len(self.open_orders),
            )
            self._last_summary_day = today

    # ── Main loop ────────────────────────────────────────────────────────────

    def run(self):
        logger.info("Grid Bot starting...")
        self.place_initial_orders()

        while self._running:
            try:
                if self.check_stop_loss():
                    break
                self.check_fills_and_reorder()
                self.maybe_send_daily_summary()
                time.sleep(30)   # Poll every 30 seconds
            except KeyboardInterrupt:
                logger.info("Grid Bot stopped by user")
                notify("⛔ Grid Bot stopped manually.")
                break
            except Exception as e:
                logger.error(f"Unhandled error in main loop: {e}")
                notify(f"⚠️ Grid Bot error: {e}")
                time.sleep(60)

        # Clean up open orders when stopping
        try:
            self.exchange.cancel_all_orders(self.pair)
            logger.info(f"Grid Bot [{self.pair}] cancelled all open orders on exit.")
        except Exception as e:
            logger.error(f"Grid Bot [{self.pair}] failed to cancel orders on exit: {e}")


if __name__ == "__main__":
    bot = GridBot()
    bot.run()
