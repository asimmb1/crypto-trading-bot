import json
import math
import os
import threading
import time
from loguru import logger
from src.exchange import get_exchange
from src.config import Config
from src.notifier import notify, notify_trade
from src.database import log_trade, init_db

# All GridBot instances share one lock for cancel_queue.json so concurrent
# pair threads cannot interleave reads and writes, causing items to reappear.
_CANCEL_QUEUE_LOCK = threading.Lock()

# Shared file written on reconcile so the dashboard can flag orphaned orders.
# Each entry: {id, pair, side, price, amount, reconciled_at}
# Entries are removed as those orders fill.
RECONCILED_FILE = "logs/reconciled_orders.json"

# Binance charges 0.1% fee on the received asset when a BUY fills.
# Counter-sells must account for this or the amount will exceed free balance.
# Use 0.075 on live if BNB fee payment is enabled.
MAKER_FEE_RATE = 0.001

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

    @staticmethod
    def _price_decimals(price: float, spacing_pct: float) -> int:
        """
        Return the number of decimal places needed so that one spacing step
        shows up as at least 2 distinct ticks at this price.

        Examples:
          ADA @ $0.237, 1% spacing → step=$0.00237 → need 4dp (rounds to $0.0024)
          XRP @ $1.34,  1% spacing → step=$0.0134  → need 3dp
          BNB @ $672,   1% spacing → step=$6.72     → 2dp is fine
        """
        step = price * spacing_pct
        if step <= 0:
            return 2
        # ceil(-log10(step)) gives the first dp where step rounds to non-zero,
        # +1 ensures at least 2 ticks of separation between adjacent levels.
        return max(2, math.ceil(-math.log10(step)) + 1)

    def calculate_grid(self, center_price: float) -> list[float]:
        """Return price levels symmetrically above and below center_price."""
        dp = self._price_decimals(center_price, self.spacing_pct)
        levels = []
        half = self.num_levels // 2
        for i in range(-half, half + 1):
            if i != 0:
                level = center_price * (1 + i * self.spacing_pct)
                levels.append(round(level, dp))
        return sorted(levels)

    # ── Order placement ──────────────────────────────────────────────────────

    def place_initial_orders(self):
        current_price = self.get_price()
        self.entry_price = current_price
        self.grid_levels = self.calculate_grid(current_price)

        # Check whether we hold any base asset to cover initial sell orders.
        # On a fresh start with USDT only, sell-side orders will always fail
        # ("insufficient balance" for the base asset). Skip them — they will be
        # placed naturally by check_fills_and_reorder as buys fill.
        base_asset = self.pair.split("/")[0]
        try:
            bal       = self.exchange.fetch_balance()
            base_free = float((bal.get(base_asset) or {}).get("free") or 0)
            usdt_free = float((bal.get("USDT") or {}).get("free") or 0)
        except Exception:
            base_free = 0.0
            usdt_free = self.total_capital

        # Order size in base asset — needed for both the inventory check and order placement.
        amount_per_order = self.order_size / current_price

        # Only place initial sell orders if we hold ENOUGH of the base asset to cover
        # at least one full standard-size sell (accounting for the 0.1% maker fee that
        # Binance deducts from the received base asset on a BUY fill).
        # Holding less than one full order qty means sell orders will fail with
        # "insufficient balance" — skip them and let _place_uncovered_sells handle the
        # partial inventory with a single recovery sell at the right size.
        has_inventory = base_free >= amount_per_order * (1 - MAKER_FEE_RATE)

        # Cap buy orders to what USDT can actually afford right now.
        affordable_buys = max(1, int(usdt_free / self.order_size))

        logger.info(
            f"Starting grid at ${current_price:,.2f} | "
            f"Levels: {len(self.grid_levels)} | "
            f"Spacing: {Config.GRID_SPACING_PCT}% | "
            f"Capital: ${self.total_capital}"
            + (f" | {base_free:.4f} {base_asset} held — placing sell orders" if has_inventory else f" | No {base_asset} held — buy-only grid")
            + (f" | USDT capped at {affordable_buys} buy orders" if affordable_buys < self.num_levels // 2 else "")
        )
        notify(
            f"🚀 **Grid Bot Started**\n"
            f"Pair: {self.pair}\n"
            f"Center: ${current_price:,.2f}\n"
            f"Spacing: {Config.GRID_SPACING_PCT}% | Capital: ${self.total_capital}\n"
            f"Mode: {'buy + sell' if has_inventory else 'buy-only (no inventory — sells placed as buys fill)'}\n"
            f"Stop loss: {Config.GRID_STOP_LOSS_PCT}% below entry"
        )
        buys_placed = 0

        for level in self.grid_levels:
            try:
                if level < current_price:
                    if buys_placed >= affordable_buys:
                        logger.warning(f"[{self.pair}] Skipping BUY @ ${level:,.2f} — USDT limit reached ({affordable_buys} orders)")
                        continue
                    order = self.exchange.create_limit_buy_order(
                        self.pair, amount_per_order, level
                    )
                    self.open_orders[order["id"]] = {
                        "side": "buy", "price": level, "amount": amount_per_order
                    }
                    logger.info(f"BUY  limit @ ${level:,.2f}")
                    buys_placed += 1
                else:
                    if not has_inventory:
                        # No base asset — skip sell side, counter-sells placed on fill
                        continue
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
                self._remove_reconciled(order_id)  # clear from orphaned list if present

                # Realized P&L: only calculable when a counter-SELL fills,
                # because that's when we know both the buy price (entry_price)
                # and the sell price (order_info["price"]).
                # Initial SELL orders placed at startup have no entry_price → pnl=None.
                # NOTE (live trading): this is gross P&L before exchange fees.
                # On live, replace with exchange.fetch_my_trades() to get net P&L
                # including actual Binance commission (typically 0.1% per side, or
                # 0.075% if paying in BNB). Net P&L = gross − (buy_value + sell_value) × fee_rate.
                entry = order_info.get("entry_price")
                pnl = None
                if order_info["side"] == "sell" and entry is not None:
                    pnl = round(order_info["amount"] * (order_info["price"] - entry), 6)

                logger.info(
                    f"Filled: {order_info['side'].upper()} @ ${order_info['price']:,.2f}"
                    + (f" | P&L: ${pnl:.6f}" if pnl is not None else "")
                )
                notify_trade(
                    f"Grid {order_info['side'].upper()} Filled",
                    self.pair,
                    order_info["price"],
                    order_info["amount"],
                    pnl=pnl,
                )
                log_trade(
                    "grid", self.pair,
                    order_info["side"],
                    order_info["price"],
                    order_info["amount"],
                    pnl=pnl,
                )

                # Place counter-order
                try:
                    if order_info["side"] == "buy":
                        counter_price = round(
                            order_info["price"] * (1 + self.spacing_pct), 2
                        )
                        counter_side = "sell"
                        # Binance deducts the 0.1% maker fee from the received base
                        # asset when a BUY fills. Selling the full ordered amount
                        # exceeds the actual free balance → "insufficient balance".
                        counter_amount = round(order_info["amount"] * (1 - MAKER_FEE_RATE), 8)
                        order = self.exchange.create_limit_sell_order(
                            self.pair, counter_amount, counter_price
                        )
                    else:
                        counter_price = round(
                            order_info["price"] * (1 - self.spacing_pct), 2
                        )
                        counter_side = "buy"
                        counter_amount = order_info["amount"]
                        order = self.exchange.create_limit_buy_order(
                            self.pair, counter_amount, counter_price
                        )

                    self.open_orders[order["id"]] = {
                        "side": counter_side,
                        "price": counter_price,
                        "amount": counter_amount,
                        "entry_price": order_info["price"] if order_info["side"] == "buy" else None,
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

    # ── Reconciled-order file helpers ────────────────────────────────────────

    def _write_reconciled(self, orders: list):
        """
        Write reconciled orders for THIS PAIR to RECONCILED_FILE.

        Replaces (not appends) this pair's entries so the file always reflects
        the actual exchange state — not accumulated history from previous sessions.
        Other pairs' entries are preserved untouched.
        """
        existing: list = []
        if os.path.exists(RECONCILED_FILE):
            try:
                with open(RECONCILED_FILE) as f:
                    existing = json.load(f)
            except Exception:
                existing = []
        # Drop all stale entries for this pair, then add the fresh set
        other_pairs = [o for o in existing if o.get("pair") != self.pair]
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        fresh = [
            {
                "id":            o["id"],
                "pair":          self.pair,
                "side":          o["side"],
                "price":         o["price"],
                "amount":        o["amount"],
                "reconciled_at": now,
            }
            for o in orders
        ]
        os.makedirs(os.path.dirname(RECONCILED_FILE) or ".", exist_ok=True)
        with open(RECONCILED_FILE, "w") as f:
            json.dump(other_pairs + fresh, f, indent=2)

    def _clear_reconciled_for_pair(self):
        """Remove all RECONCILED_FILE entries for this pair (called on fresh grid start)."""
        if not os.path.exists(RECONCILED_FILE):
            return
        try:
            with open(RECONCILED_FILE) as f:
                existing = json.load(f)
            filtered = [o for o in existing if o.get("pair") != self.pair]
            with open(RECONCILED_FILE, "w") as f:
                json.dump(filtered, f, indent=2)
        except Exception:
            pass

    def _remove_reconciled(self, order_id: str):
        """Remove a filled order from RECONCILED_FILE."""
        if not os.path.exists(RECONCILED_FILE):
            return
        try:
            with open(RECONCILED_FILE) as f:
                orders = json.load(f)
            filtered = [o for o in orders if o["id"] != order_id]
            with open(RECONCILED_FILE, "w") as f:
                json.dump(filtered, f, indent=2)
        except Exception:
            pass

    # ── Uncovered position recovery ───────────────────────────────────────────

    def _place_uncovered_sells(self):
        """
        Called once after reconcile. Detects base-asset holdings that have no
        open SELL order covering them, then places a recovery sell.

        WHY THIS IS NEEDED:
          fetch_open_orders() only returns orders still OPEN on the exchange.
          If a BUY order filled while the bot was down (e.g. price dropped during
          the 2-minute Railway redeploy window), the fill is invisible to the
          reconcile step. The result: we hold the base asset (e.g. SOL) with no
          counter-SELL and no stop-loss protection — infinite downside.

        HOW IT WORKS:
          balance[base]['free'] = base asset NOT locked in any open sell order.
          If free > dust threshold → place a sell at current_price × (1 + spacing).
          The bot then tracks this sell normally; stop-loss still fires if price
          continues to drop past the threshold.

        NOTE: entry_price for the recovery sell is None (we don't know what the
        original buy price was), so P&L won't be tracked for this cycle. But the
        position is fully managed going forward.
        """
        base_asset = self.pair.split("/")[0]  # "SOL" from "SOL/USDT"

        try:
            balance = self.exchange.fetch_balance()
            free = balance.get(base_asset, {}).get("free", 0.0) or 0.0
        except Exception as e:
            logger.warning(f"[{self.pair}] Balance check for uncovered sells failed: {e}")
            return

        # Ignore dust — two checks:
        #   1. Qty dust: less than 10% of one standard order qty → skip
        #   2. Notional dust: qty × price < $6 → skip (Binance minimum is $5; $6 adds buffer)
        #      Catches cases like 0.006 BNB × $711 = $4.27 which passes qty check but
        #      fails Binance's NOTIONAL filter with code -1013.
        try:
            current_price = self.get_price()
        except Exception:
            return
        dust_threshold  = (self.order_size / current_price) * 0.10
        notional_value  = free * current_price
        MIN_NOTIONAL    = 6.0   # $6 buffer above Binance's $5 minimum notional

        if free <= dust_threshold:
            return  # qty dust

        if notional_value < MIN_NOTIONAL:
            logger.info(
                f"[{self.pair}] {free:.6f} {base_asset} (${notional_value:.2f}) "
                f"is below minimum notional ${MIN_NOTIONAL} — treating as dust, skipping recovery sell."
            )
            return  # notional dust — would fail exchange filter

        logger.warning(
            f"[{self.pair}] {free:.6f} {base_asset} is FREE (no open sell covering it). "
            f"Likely a BUY filled during downtime. Placing recovery sell."
        )

        try:
            sell_price = round(current_price * (1 + self.spacing_pct), 2)
            order = self.exchange.create_limit_sell_order(self.pair, free, sell_price)
            self.open_orders[order["id"]] = {
                "side":        "sell",
                "price":       sell_price,
                "amount":      free,
                "entry_price": None,  # unknown — downtime fill
            }
            notify(
                f"🔄 **Recovery SELL Placed** `{self.pair}`\n"
                f"Found {free:.6f} {base_asset} with no sell order "
                f"(BUY likely filled during bot downtime).\n"
                f"Recovery sell @ ${sell_price:,.4f} "
                f"(+{self.spacing_pct * 100:.1f}% above current).\n"
                f"Stop-loss and counter-orders active from here."
            )
            logger.info(
                f"[{self.pair}] Recovery sell placed: {free:.6f} {base_asset} @ ${sell_price:,.4f}"
            )
        except Exception as e:
            logger.error(f"[{self.pair}] Recovery sell FAILED: {e}")
            notify(
                f"🚨 **Recovery SELL FAILED** `{self.pair}`\n"
                f"Holding {free:.6f} {base_asset} with NO sell order and NO stop-loss.\n"
                f"Error: {e}\n"
                f"**Manual action required** — set a sell on the exchange UI immediately."
            )

    # ── Cancel queue (dashboard-initiated cancellations) ─────────────────────

    def _process_cancel_queue(self):
        """
        Process external cancellation requests written by the dashboard's
        POST /cancel_orphan endpoint into logs/cancel_queue.json.

        WHY THIS EXISTS — the safety problem:
          If the dashboard cancelled an order directly on the exchange,
          check_fills_and_reorder() would see the order gone on the next 30s cycle,
          assume it FILLED, and place a counter-order + log a fake trade.
          This method intercepts the cancellation BEFORE that happens:
          1. Cancel on exchange
          2. Remove from self.open_orders (so no fake fill is detected)
          3. Remove from reconciled_orders.json (clears the dashboard panel)
          4. Remove from cancel_queue.json (mark as processed)

        Called at the TOP of each main loop iteration, before check_fills_and_reorder.
        """
        queue_file = "logs/cancel_queue.json"
        if not os.path.exists(queue_file):
            return

        # Lock covers the full read→process→write cycle so no two pair threads
        # can interleave, which would cause processed items to reappear next cycle.
        with _CANCEL_QUEUE_LOCK:
            try:
                with open(queue_file) as f:
                    queue = json.load(f)
            except Exception:
                return

            remaining = []
            for item in queue:
                if item.get("pair") != self.pair:
                    remaining.append(item)  # belongs to a different pair's bot
                    continue

                order_id    = item["order_id"]
                market_sell = item.get("market_sell", False)
                amount      = float(item.get("amount", 0.0))
                base_asset  = self.pair.split("/")[0]

                try:
                    self.exchange.cancel_order(order_id, self.pair)
                    logger.info(f"[{self.pair}] Cancelled orphaned order {order_id} via dashboard.")
                except Exception as e:
                    logger.warning(f"[{self.pair}] cancel_order({order_id}) error (may already be gone): {e}")

                # Always remove from in-memory fill-tracking so check_fills_and_reorder
                # never treats a missing order as a fill.
                self.open_orders.pop(order_id, None)

                if market_sell and amount > 0:
                    try:
                        bal  = self.exchange.fetch_balance()
                        free = float((bal.get(base_asset) or {}).get("free") or 0)
                    except Exception:
                        free = amount  # if balance check fails, attempt the sell anyway

                    dust = amount * 0.05
                    if free < dust:
                        logger.info(
                            f"[{self.pair}] No {base_asset} in account (free={free:.6f}) — "
                            f"nothing to market-sell for orphan {order_id}. Clearing from dashboard."
                        )
                        self._remove_reconciled(order_id)
                    else:
                        sell_qty = min(free, amount)
                        try:
                            order = self.exchange.create_market_sell_order(self.pair, sell_qty)
                            logger.info(
                                f"[{self.pair}] Market sold {sell_qty:.6f} {base_asset} "
                                f"after orphan SELL cancel — order {order.get('id')}"
                            )
                            notify(
                                f"💱 **Orphan Cleared** `{self.pair}`\n"
                                f"Limit SELL `{order_id}` cancelled.\n"
                                f"Market sold {sell_qty:.6f} {base_asset} to USDT immediately.\n"
                                f"No tokens left unattended."
                            )
                            self._remove_reconciled(order_id)
                        except Exception as e:
                            logger.error(f"[{self.pair}] Market sell after orphan cancel FAILED: {e}")
                            notify(
                                f"🚨 **Market Sell FAILED** `{self.pair}`\n"
                                f"Limit SELL `{order_id}` cancelled but market sell failed: {e}\n"
                                f"Order remains on dashboard — click **Market Sell** again to retry.\n"
                                f"Or manually sell {sell_qty:.6f} {base_asset} on the exchange."
                            )
                else:
                    self._remove_reconciled(order_id)
                    notify(f"🗑 **Order Cancelled** `{self.pair}`\nOrphan BUY order `{order_id}` cancelled. USDT returned by exchange.")

            if len(remaining) != len(queue):
                with open(queue_file, "w") as f:
                    json.dump(remaining, f, indent=2)

    # ── Startup reconcile ────────────────────────────────────────────────────

    def _reconcile_or_init(self):
        """
        Called once at startup instead of place_initial_orders() directly.

        Problem this solves:
          On Railway redeploy (or any unclean shutdown), the Python process is
          killed before cancel_all_orders() can run. The exchange still holds all
          the open limit orders. If we call place_initial_orders() blindly on the
          new process, we end up with 2× the intended orders: the orphaned set from
          before the restart PLUS a brand-new full grid. This doubles deployed
          capital and breaks fill-tracking entirely.

        What we do instead:
          1. Fetch open orders for this pair from the exchange.
          2. If none found → clean start, call place_initial_orders() as normal.
          3. If orders found → reconcile: rebuild open_orders dict from exchange
             state, skip re-placing. P&L tracking is lost for those pre-restart
             orders (entry_price unknown), but operation continues correctly.
        """
        try:
            existing = self.exchange.fetch_open_orders(self.pair)
        except Exception as e:
            logger.error(
                f"[{self.pair}] fetch_open_orders on startup failed: {e}. "
                f"Falling back to fresh grid."
            )
            self.place_initial_orders()
            return

        if not existing:
            logger.info(f"[{self.pair}] No existing orders — placing fresh grid.")
            self._clear_reconciled_for_pair()  # nothing is orphaned — scrub stale dashboard entries
            self.place_initial_orders()
            # Cover any base asset that place_initial_orders couldn't sell
            # (e.g. partial inventory below one standard order size).
            self._place_uncovered_sells()
            return

        # ── Reconcile path ───────────────────────────────────────────────────
        logger.warning(
            f"[{self.pair}] Found {len(existing)} open orders on exchange. "
            f"Reconciling — skipping fresh grid placement."
        )

        for order in existing:
            self.open_orders[order["id"]] = {
                "side":        order["side"],
                "price":       order["price"],
                "amount":      order["amount"],
                "entry_price": None,  # filled in below for SELL orders
            }

        # ── Restore entry_price for reconciled SELL orders ───────────────────
        # Counter-SELLs were placed by the previous bot run at exactly
        # buy_price × (1 + spacing).  So buy_price = sell_price / (1 + spacing).
        # We can recover this without any stored state — it's pure grid geometry.
        for info in self.open_orders.values():
            if info["side"] == "sell":
                info["entry_price"] = round(info["price"] / (1 + self.spacing_pct), 4)

        # Best-effort entry_price for stop-loss: highest open BUY = closest to center
        buy_prices = [o["price"] for o in existing if o["side"] == "buy"]
        self.entry_price = max(buy_prices) if buy_prices else self.get_price()

        sells_restored = sum(1 for o in self.open_orders.values() if o["side"] == "sell")
        notify(
            f"♻️ **Grid Bot Resumed** `{self.pair}`\n"
            f"Reconciled {len(existing)} existing orders "
            f"({sells_restored} sells with P&L tracking restored).\n"
            f"Entry ref: ${self.entry_price:,.2f}"
        )
        logger.info(
            f"[{self.pair}] Reconciled {len(self.open_orders)} orders "
            f"({sells_restored} sells with inferred entry_price). "
            f"entry_price=${self.entry_price:,.2f}"
        )
        # Write to file so the dashboard can flag these as unmanaged
        self._write_reconciled(existing)

        # ── Place recovery sells for fills that happened during downtime ──────
        # fetch_open_orders only returns still-open orders. If a BUY filled while
        # the bot was dead, the crypto is now in our balance with no counter-SELL.
        # _place_uncovered_sells() detects and covers these.
        self._place_uncovered_sells()

    # ── Active orders snapshot (for dashboard) ───────────────────────────────

    def _write_active_orders(self):
        """Write current open_orders to a per-pair file read by the dashboard."""
        slug = self.pair.replace("/", "")
        path = f"logs/active_{slug}.json"
        try:
            os.makedirs("logs", exist_ok=True)
            snapshot = sorted(
                [
                    {
                        "pair":      self.pair,
                        "id":        oid,
                        "side":      info["side"],
                        "price":     info["price"],
                        "amount":    round(info["amount"], 6),
                        "value_usd": round(info["price"] * info["amount"], 2),
                    }
                    for oid, info in self.open_orders.items()
                ],
                key=lambda o: o["price"],
            )
            with open(path, "w") as f:
                json.dump(snapshot, f)
        except Exception:
            pass

    def _clear_active_orders(self):
        """Clear active orders file on shutdown so dashboard shows nothing stale."""
        slug = self.pair.replace("/", "")
        path = f"logs/active_{slug}.json"
        try:
            with open(path, "w") as f:
                json.dump([], f)
        except Exception:
            pass

    # ── Main loop ────────────────────────────────────────────────────────────

    def run(self):
        logger.info("Grid Bot starting...")
        self._reconcile_or_init()  # safe on redeploy — see method docstring

        while self._running:
            try:
                if self.check_stop_loss():
                    break
                self._process_cancel_queue()  # must run before check_fills_and_reorder
                self.check_fills_and_reorder()
                self._write_active_orders()   # snapshot for dashboard (after fill updates)
                time.sleep(30)   # Poll every 30 seconds
            except KeyboardInterrupt:
                logger.info("Grid Bot stopped by user")
                notify("⛔ Grid Bot stopped manually.")
                break
            except Exception as e:
                logger.error(f"Unhandled error in main loop: {type(e).__name__}: {e}")
                notify(f"⚠️ Grid Bot error: {e}")
                for _ in range(12):
                    if not self._running:
                        break
                    time.sleep(5)

        # Clean up open orders when stopping
        self._clear_active_orders()
        try:
            self.exchange.cancel_all_orders(self.pair)
            logger.info(f"Grid Bot [{self.pair}] cancelled all open orders on exit.")
        except Exception as e:
            logger.error(f"Grid Bot [{self.pair}] failed to cancel orders on exit: {e}")


if __name__ == "__main__":
    bot = GridBot()
    bot.run()
