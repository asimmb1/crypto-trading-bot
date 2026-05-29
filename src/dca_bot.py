import time
from loguru import logger
from src.exchange import get_exchange
from src.config import Config
from src.notifier import notify, notify_trade, notify_daily_summary
from src.database import log_trade, init_db, get_daily_summary

# Configure loguru to write to file
logger.add("logs/dca_bot.log", rotation="1 day", retention="14 days", level="INFO")


class DCABot:
    def __init__(self, pair: str = None, base_order: float = None,
                 safety_order: float = None, exchange=None):
        self.exchange = exchange or get_exchange("binance")
        self.pair = pair or Config.DCA_PAIR
        self.base_order_size = base_order or Config.DCA_BASE_ORDER
        self.safety_order_size = safety_order or Config.DCA_SAFETY_ORDER
        self.max_safety_orders = Config.DCA_MAX_SAFETY_ORDERS
        self.price_drop_pct = Config.DCA_PRICE_DROP_PCT / 100
        self.take_profit_pct = Config.DCA_TAKE_PROFIT_PCT / 100
        self._running = True

    def stop(self):
        """Signal the bot to exit its main loop cleanly."""
        self._running = False
        logger.info(f"DCA Bot [{self.pair}] stop signal received.")

        self.position: list[dict] = []   # [{price, amount}, ...]
        self.safety_orders_placed: int = 0
        self.last_buy_price: float = None
        self._last_summary_day: str = None
        init_db()

    # ── Price ────────────────────────────────────────────────────────────────

    def get_price(self) -> float:
        return self.exchange.fetch_ticker(self.pair)["last"]

    # ── Position stats ───────────────────────────────────────────────────────

    @property
    def avg_entry(self) -> float:
        if not self.position:
            return 0.0
        total_cost   = sum(p["price"] * p["amount"] for p in self.position)
        total_amount = sum(p["amount"] for p in self.position)
        return total_cost / total_amount if total_amount else 0.0

    @property
    def total_amount(self) -> float:
        return sum(p["amount"] for p in self.position)

    @property
    def total_invested(self) -> float:
        return sum(p["price"] * p["amount"] for p in self.position)

    # ── Orders ───────────────────────────────────────────────────────────────

    def place_base_order(self):
        price  = self.get_price()
        amount = self.base_order_size / price
        self.exchange.create_market_buy_order(self.pair, amount)
        self.position.append({"price": price, "amount": amount})
        self.last_buy_price = price
        self.safety_orders_placed = 0

        logger.info(f"Base order: bought {amount:.6f} @ ${price:,.2f}")
        notify(
            f"🟢 **DCA Base Order**\n"
            f"Pair: {self.pair}\n"
            f"Bought {amount:.6f} @ ${price:,.2f}\n"
            f"Invested: ${self.base_order_size:.2f}"
        )
        log_trade("dca", self.pair, "buy", price, amount)

    def check_safety_order(self):
        """Buy more if price dropped enough from last buy price."""
        if self.safety_orders_placed >= self.max_safety_orders:
            return
        if not self.last_buy_price:
            return

        current_price = self.get_price()
        drop = (self.last_buy_price - current_price) / self.last_buy_price

        if drop >= self.price_drop_pct:
            amount = self.safety_order_size / current_price
            self.exchange.create_market_buy_order(self.pair, amount)
            self.position.append({"price": current_price, "amount": amount})
            self.last_buy_price = current_price
            self.safety_orders_placed += 1

            logger.info(
                f"Safety order #{self.safety_orders_placed}: "
                f"{amount:.6f} @ ${current_price:,.2f} | "
                f"Avg entry: ${self.avg_entry:,.2f}"
            )
            notify(
                f"🟡 **DCA Safety Order #{self.safety_orders_placed}**\n"
                f"Pair: {self.pair}\n"
                f"Bought {amount:.6f} @ ${current_price:,.2f}\n"
                f"Avg Entry: ${self.avg_entry:,.2f}\n"
                f"Total Invested: ${self.total_invested:.2f}\n"
                f"Safety orders left: {self.max_safety_orders - self.safety_orders_placed}"
            )
            log_trade("dca", self.pair, "buy", current_price, amount)

    def check_take_profit(self):
        """Sell entire position when price is above average entry + take-profit %."""
        if not self.position:
            return

        current_price = self.get_price()
        target = self.avg_entry * (1 + self.take_profit_pct)

        if current_price >= target:
            pnl = (current_price - self.avg_entry) * self.total_amount
            amt = self.total_amount

            self.exchange.create_market_sell_order(self.pair, amt)
            logger.info(
                f"Take profit: sold {amt:.6f} @ ${current_price:,.2f} | P&L: ${pnl:.2f}"
            )
            notify_trade("DCA Take Profit ✅", self.pair, current_price, amt, pnl)
            log_trade("dca", self.pair, "sell", current_price, amt, pnl)

            # Reset state
            self.position = []
            self.safety_orders_placed = 0
            self.last_buy_price = None

    # ── Daily summary ────────────────────────────────────────────────────────

    def maybe_send_daily_summary(self):
        """Send a daily P&L summary once per UTC day."""
        from datetime import datetime
        today = datetime.utcnow().strftime("%Y-%m-%d")
        hour  = datetime.utcnow().hour
        if self._last_summary_day != today and hour >= 23:
            summary = get_daily_summary("dca")
            notify_daily_summary(
                "DCA Bot",
                summary["count"],
                summary["pnl"],
                open_orders=1 if self.position else 0,
            )
            self._last_summary_day = today

    # ── Main loop ────────────────────────────────────────────────────────────

    def run(self):
        logger.info("DCA Bot starting...")
        self.place_base_order()

        while self._running:
            try:
                if self.position:
                    self.check_safety_order()
                    self.check_take_profit()
                else:
                    # No position open — place a fresh base order
                    logger.info("No open position. Placing new base order...")
                    self.place_base_order()

                self.maybe_send_daily_summary()
                time.sleep(60)   # Poll every 60 seconds

            except KeyboardInterrupt:
                logger.info("DCA Bot stopped by user")
                notify("⛔ DCA Bot stopped manually.")
                break
            except Exception as e:
                logger.error(f"DCA Bot unhandled error: {e}")
                notify(f"⚠️ DCA Bot error: {e}")
                time.sleep(60)


if __name__ == "__main__":
    bot = DCABot()
    bot.run()
