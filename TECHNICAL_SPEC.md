# 01 — Trading Bot: Technical Specification

## Overview

A Python trading bot running two strategies simultaneously:
1. **Grid Bot** — places buy/sell limit orders at fixed % intervals. Profits from price oscillation within a range.
2. **DCA Bot** — buys more of an asset on each price dip, then sells the whole position when price recovers above average entry.

Both strategies run as independent processes on a VPS and communicate P&L via Telegram.

---

## Tech Stack

| Component | Tool | Why |
|-----------|------|-----|
| Trading library | `ccxt` | Unified API for 100+ exchanges |
| Async market data | `ccxt.pro` | WebSocket streams for real-time prices |
| Data manipulation | `pandas`, `numpy` | Backtesting, OHLCV analysis |
| Scheduling | `schedule` | Periodic tasks (daily reports, cleanup) |
| Notifications | `python-telegram-bot` | Trade alerts to your phone |
| Config | `python-dotenv` | Secure API key loading |
| Logging | `loguru` | Better than Python's default logging |
| Storage | `sqlite3` (built-in) | Trade log, P&L history |
| Testing | `pytest` | Unit tests for core logic |

---

## Project Structure

```
01-trading-bot/
├── Architecture.md          ← Progress tracker (this file's sibling)
├── TECHNICAL_SPEC.md        ← This file
├── requirements.txt
├── .env.example
├── .env                     ← NEVER commit this
├── .gitignore
│
├── src/
│   ├── __init__.py
│   ├── config.py            ← Load and validate all config/env vars
│   ├── exchange.py          ← Exchange connection factory
│   ├── notifier.py          ← Telegram notification helper
│   ├── database.py          ← SQLite trade logger
│   ├── grid_bot.py          ← GridBot class
│   ├── dca_bot.py           ← DCABot class
│   └── test_connection.py   ← Quick sanity check script
│
├── backtests/
│   ├── backtest_grid.py
│   └── backtest_dca.py
│
├── deploy/
│   ├── grid_bot.service     ← systemd service file
│   └── dca_bot.service      ← systemd service file
│
└── logs/                    ← Auto-created at runtime
    ├── grid_bot.log
    └── dca_bot.log
```

---

## Step-by-Step Build Guide

### Step 1 — Local Environment Setup

```bash
# Clone or create the project folder
mkdir 01-trading-bot && cd 01-trading-bot

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate          # Mac/Linux
# venv\Scripts\activate           # Windows

# Install dependencies
pip install -r requirements.txt

# Copy env template
cp .env.example .env
```

---

### Step 2 — API Key Setup

**Binance:**
1. Go to binance.com → Profile → API Management
2. Create API key — name it `trading-bot`
3. Permissions: **Enable Spot & Margin Trading** only. Disable everything else. Do NOT enable withdrawals.
4. Restrict access to IP: add your VPS IP address
5. Copy API Key and Secret Key into `.env`

**Bybit:**
1. Go to bybit.com → Account → API
2. Create key — Read + Trade permissions (Spot only)
3. IP whitelist your VPS IP
4. Copy into `.env`

**Binance Testnet (for paper trading):**
1. Go to testnet.binance.vision
2. Sign in with GitHub
3. Generate HMAC keys
4. Add to `.env` as `BINANCE_TESTNET_API_KEY` / `BINANCE_TESTNET_SECRET`

**Telegram Bot:**
1. Message @BotFather on Telegram → `/newbot`
2. Follow prompts → get your Bot Token
3. Message your bot once, then visit: `https://api.telegram.org/bot<TOKEN>/getUpdates`
4. Copy your `chat_id` from the response
5. Add both to `.env`

---

### Step 3 — Core Files

**`.env.example`**
```env
# Exchange credentials
BINANCE_API_KEY=your_key_here
BINANCE_SECRET=your_secret_here
BYBIT_API_KEY=your_key_here
BYBIT_SECRET=your_secret_here

# Testnet (for development)
BINANCE_TESTNET_API_KEY=your_testnet_key
BINANCE_TESTNET_SECRET=your_testnet_secret

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Bot configuration
GRID_PAIR=BTC/USDT
GRID_TOTAL_CAPITAL=          # Total USDT to allocate to the grid (e.g. 100, 500, 5000)
GRID_LEVELS=10
GRID_SPACING_PCT=1.0
GRID_STOP_LOSS_PCT=8.0

DCA_PAIR=ETH/USDT
DCA_BASE_ORDER=              # USDT size of first buy (e.g. 50, 200)
DCA_SAFETY_ORDER=            # USDT size of each dip buy (e.g. 20, 100)
DCA_MAX_SAFETY_ORDERS=5
DCA_PRICE_DROP_PCT=2.5
DCA_TAKE_PROFIT_PCT=3.0

# Environment: testnet or live
ENV=testnet
```

**`src/config.py`**
```python
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Exchange
    BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
    BINANCE_SECRET = os.getenv("BINANCE_SECRET")
    BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
    BYBIT_SECRET = os.getenv("BYBIT_SECRET")
    BINANCE_TESTNET_API_KEY = os.getenv("BINANCE_TESTNET_API_KEY")
    BINANCE_TESTNET_SECRET = os.getenv("BINANCE_TESTNET_SECRET")

    # Telegram
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    # Environment
    ENV = os.getenv("ENV", "testnet")  # "testnet" or "live"

    # Grid Bot
    GRID_PAIR = os.getenv("GRID_PAIR", "BTC/USDT")
    GRID_LEVELS = int(os.getenv("GRID_LEVELS", 10))
    GRID_SPACING_PCT = float(os.getenv("GRID_SPACING_PCT", 1.0))
    GRID_STOP_LOSS_PCT = float(os.getenv("GRID_STOP_LOSS_PCT", 8.0))
    # Capital — must be set by user, no default
    _grid_capital_raw = os.getenv("GRID_TOTAL_CAPITAL", "")
    GRID_TOTAL_CAPITAL = float(_grid_capital_raw) if _grid_capital_raw.strip() else None

    # DCA Bot
    DCA_PAIR = os.getenv("DCA_PAIR", "ETH/USDT")
    DCA_MAX_SAFETY_ORDERS = int(os.getenv("DCA_MAX_SAFETY_ORDERS", 5))
    DCA_PRICE_DROP_PCT = float(os.getenv("DCA_PRICE_DROP_PCT", 2.5))
    DCA_TAKE_PROFIT_PCT = float(os.getenv("DCA_TAKE_PROFIT_PCT", 3.0))
    # Capital — must be set by user, no default
    _base_raw   = os.getenv("DCA_BASE_ORDER",   "")
    _safety_raw = os.getenv("DCA_SAFETY_ORDER",  "")
    DCA_BASE_ORDER   = float(_base_raw)   if _base_raw.strip()   else None
    DCA_SAFETY_ORDER = float(_safety_raw) if _safety_raw.strip() else None

    @classmethod
    def validate(cls):
        required = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
        if cls.ENV == "live":
            required += ["BINANCE_API_KEY", "BINANCE_SECRET"]
        else:
            required += ["BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_SECRET"]

        missing = [k for k in required if not getattr(cls, k)]
        # Capital fields must not be None
        if cls.GRID_TOTAL_CAPITAL is None:
            missing.append("GRID_TOTAL_CAPITAL")
        if cls.DCA_BASE_ORDER is None:
            missing.append("DCA_BASE_ORDER")
        if cls.DCA_SAFETY_ORDER is None:
            missing.append("DCA_SAFETY_ORDER")
        if missing:
            raise ValueError(f"Missing required env vars: {missing}")
```

**`src/exchange.py`**
```python
import ccxt
from src.config import Config

def get_exchange(exchange_name: str = "binance", testnet: bool = None) -> ccxt.Exchange:
    """Returns a configured exchange instance."""
    use_testnet = testnet if testnet is not None else (Config.ENV == "testnet")

    if exchange_name == "binance":
        if use_testnet:
            exchange = ccxt.binance({
                "apiKey": Config.BINANCE_TESTNET_API_KEY,
                "secret": Config.BINANCE_TESTNET_SECRET,
            })
            exchange.set_sandbox_mode(True)
        else:
            exchange = ccxt.binance({
                "apiKey": Config.BINANCE_API_KEY,
                "secret": Config.BINANCE_SECRET,
            })

    elif exchange_name == "bybit":
        exchange = ccxt.bybit({
            "apiKey": Config.BYBIT_API_KEY,
            "secret": Config.BYBIT_SECRET,
        })
    else:
        raise ValueError(f"Unsupported exchange: {exchange_name}")

    exchange.load_markets()
    return exchange
```

**`src/notifier.py`**
```python
import asyncio
from telegram import Bot
from src.config import Config

_bot = Bot(token=Config.TELEGRAM_BOT_TOKEN)

async def _send(message: str):
    await _bot.send_message(chat_id=Config.TELEGRAM_CHAT_ID, text=message, parse_mode="HTML")

def notify(message: str):
    """Send a Telegram notification. Call from sync code."""
    try:
        asyncio.get_event_loop().run_until_complete(_send(message))
    except Exception as e:
        print(f"[Notifier] Failed to send: {e}")

def notify_trade(action: str, pair: str, price: float, amount: float, pnl: float = None):
    msg = f"<b>🤖 {action}</b>\n"
    msg += f"Pair: {pair}\n"
    msg += f"Price: ${price:,.4f}\n"
    msg += f"Amount: {amount}\n"
    if pnl is not None:
        emoji = "✅" if pnl >= 0 else "🔴"
        msg += f"P&L: {emoji} ${pnl:.2f}"
    notify(msg)
```

**`src/database.py`**
```python
import sqlite3
from datetime import datetime
from src.config import Config

DB_PATH = "logs/trades.db"

def init_db():
    import os
    os.makedirs("logs", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            bot_type TEXT,
            pair TEXT,
            side TEXT,
            price REAL,
            amount REAL,
            pnl REAL,
            notes TEXT
        )
    """)
    conn.commit()
    conn.close()

def log_trade(bot_type: str, pair: str, side: str, price: float, amount: float, pnl: float = None, notes: str = ""):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO trades (timestamp, bot_type, pair, side, price, amount, pnl, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (datetime.utcnow().isoformat(), bot_type, pair, side, price, amount, pnl, notes))
    conn.commit()
    conn.close()
```

**`src/grid_bot.py`**
```python
import time
import math
from loguru import logger
from src.exchange import get_exchange
from src.config import Config
from src.notifier import notify, notify_trade
from src.database import log_trade, init_db

class GridBot:
    def __init__(self):
        self.exchange = get_exchange("binance")
        self.pair = Config.GRID_PAIR
        self.total_capital = Config.GRID_TOTAL_CAPITAL
        self.num_levels = Config.GRID_LEVELS
        self.spacing_pct = Config.GRID_SPACING_PCT / 100
        self.stop_loss_pct = Config.GRID_STOP_LOSS_PCT / 100
        self.order_size = self.total_capital / self.num_levels
        self.grid_levels = []
        self.open_orders = {}
        self.entry_price = None
        init_db()

    def get_price(self) -> float:
        ticker = self.exchange.fetch_ticker(self.pair)
        return ticker["last"]

    def calculate_grid(self, center_price: float) -> list[float]:
        """Calculate price levels above and below center price."""
        levels = []
        half = self.num_levels // 2
        for i in range(-half, half + 1):
            if i != 0:
                level = center_price * (1 + i * self.spacing_pct)
                levels.append(round(level, 2))
        return sorted(levels)

    def place_initial_orders(self):
        current_price = self.get_price()
        self.entry_price = current_price
        self.grid_levels = self.calculate_grid(current_price)
        
        logger.info(f"Starting grid at ${current_price:,.2f} | Levels: {len(self.grid_levels)}")
        notify(f"🚀 <b>Grid Bot Started</b>\nPair: {self.pair}\nCenter: ${current_price:,.2f}\nLevels: {len(self.grid_levels)}\nCapital: ${self.total_capital}")

        amount_per_order = self.order_size / current_price

        for level in self.grid_levels:
            try:
                if level < current_price:
                    order = self.exchange.create_limit_buy_order(self.pair, amount_per_order, level)
                    self.open_orders[order["id"]] = {"side": "buy", "price": level, "amount": amount_per_order}
                    logger.info(f"BUY order @ ${level:,.2f}")
                else:
                    order = self.exchange.create_limit_sell_order(self.pair, amount_per_order, level)
                    self.open_orders[order["id"]] = {"side": "sell", "price": level, "amount": amount_per_order}
                    logger.info(f"SELL order @ ${level:,.2f}")
                time.sleep(0.1)  # Rate limit protection
            except Exception as e:
                logger.error(f"Failed to place order at {level}: {e}")

    def check_fills_and_reorder(self):
        """Check which orders filled and place counter-orders."""
        filled = []
        current_orders = self.exchange.fetch_open_orders(self.pair)
        current_ids = {o["id"] for o in current_orders}

        for order_id, order_info in list(self.open_orders.items()):
            if order_id not in current_ids:
                # Order was filled
                filled.append(order_info)
                del self.open_orders[order_id]
                logger.info(f"Order filled: {order_info['side']} @ ${order_info['price']:,.2f}")
                notify_trade(
                    f"Grid {'BUY' if order_info['side'] == 'buy' else 'SELL'} Filled",
                    self.pair, order_info["price"], order_info["amount"]
                )
                log_trade("grid", self.pair, order_info["side"], order_info["price"], order_info["amount"])

                # Place counter-order
                try:
                    counter_price = round(
                        order_info["price"] * (1 + self.spacing_pct) if order_info["side"] == "buy"
                        else order_info["price"] * (1 - self.spacing_pct), 2
                    )
                    counter_side = "sell" if order_info["side"] == "buy" else "buy"
                    if counter_side == "sell":
                        order = self.exchange.create_limit_sell_order(self.pair, order_info["amount"], counter_price)
                    else:
                        order = self.exchange.create_limit_buy_order(self.pair, order_info["amount"], counter_price)
                    self.open_orders[order["id"]] = {"side": counter_side, "price": counter_price, "amount": order_info["amount"]}
                    logger.info(f"Counter {counter_side.upper()} order placed @ ${counter_price:,.2f}")
                except Exception as e:
                    logger.error(f"Failed to place counter order: {e}")

    def check_stop_loss(self) -> bool:
        """Returns True if stop loss triggered."""
        if not self.entry_price:
            return False
        current_price = self.get_price()
        drop = (self.entry_price - current_price) / self.entry_price
        if drop >= self.stop_loss_pct:
            logger.warning(f"STOP LOSS triggered! Price dropped {drop*100:.1f}% from entry")
            notify(f"🛑 <b>GRID STOP LOSS</b>\nPrice dropped {drop*100:.1f}% from entry ${self.entry_price:,.2f}\nCancelling all orders.")
            self.exchange.cancel_all_orders(self.pair)
            return True
        return False

    def run(self):
        logger.info("Grid Bot starting...")
        self.place_initial_orders()
        
        while True:
            try:
                if self.check_stop_loss():
                    break
                self.check_fills_and_reorder()
                time.sleep(30)  # Poll every 30 seconds
            except KeyboardInterrupt:
                logger.info("Grid Bot stopped by user")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                notify(f"⚠️ Grid Bot error: {e}")
                time.sleep(60)

if __name__ == "__main__":
    bot = GridBot()
    bot.run()
```

**`src/dca_bot.py`**
```python
import time
from loguru import logger
from src.exchange import get_exchange
from src.config import Config
from src.notifier import notify, notify_trade
from src.database import log_trade, init_db

class DCABot:
    def __init__(self):
        self.exchange = get_exchange("binance")
        self.pair = Config.DCA_PAIR
        self.base_order_size = Config.DCA_BASE_ORDER
        self.safety_order_size = Config.DCA_SAFETY_ORDER
        self.max_safety_orders = Config.DCA_MAX_SAFETY_ORDERS
        self.price_drop_pct = Config.DCA_PRICE_DROP_PCT / 100
        self.take_profit_pct = Config.DCA_TAKE_PROFIT_PCT / 100
        
        self.position = []  # List of {price, amount} dicts
        self.safety_orders_placed = 0
        self.last_buy_price = None
        init_db()

    def get_price(self) -> float:
        return self.exchange.fetch_ticker(self.pair)["last"]

    @property
    def avg_entry(self) -> float:
        if not self.position:
            return 0.0
        total_cost = sum(p["price"] * p["amount"] for p in self.position)
        total_amount = sum(p["amount"] for p in self.position)
        return total_cost / total_amount if total_amount else 0.0

    @property
    def total_amount(self) -> float:
        return sum(p["amount"] for p in self.position)

    def place_base_order(self):
        price = self.get_price()
        amount = self.base_order_size / price
        self.exchange.create_market_buy_order(self.pair, amount)
        self.position.append({"price": price, "amount": amount})
        self.last_buy_price = price
        logger.info(f"Base order: bought {amount:.6f} @ ${price:,.2f}")
        notify(f"🟢 <b>DCA Base Order</b>\n{self.pair}\nBought {amount:.6f} @ ${price:,.2f}")
        log_trade("dca", self.pair, "buy", price, amount)

    def check_safety_order(self):
        if self.safety_orders_placed >= self.max_safety_orders:
            return
        current_price = self.get_price()
        drop = (self.last_buy_price - current_price) / self.last_buy_price
        if drop >= self.price_drop_pct:
            amount = self.safety_order_size / current_price
            self.exchange.create_market_buy_order(self.pair, amount)
            self.position.append({"price": current_price, "amount": amount})
            self.last_buy_price = current_price
            self.safety_orders_placed += 1
            logger.info(f"Safety order #{self.safety_orders_placed}: {amount:.6f} @ ${current_price:,.2f} | Avg entry: ${self.avg_entry:,.2f}")
            notify(f"🟡 <b>DCA Safety Order #{self.safety_orders_placed}</b>\n{self.pair}\n${current_price:,.2f}\nAvg Entry: ${self.avg_entry:,.2f}")
            log_trade("dca", self.pair, "buy", current_price, amount)

    def check_take_profit(self):
        if not self.position:
            return
        current_price = self.get_price()
        target = self.avg_entry * (1 + self.take_profit_pct)
        if current_price >= target:
            pnl = (current_price - self.avg_entry) * self.total_amount
            self.exchange.create_market_sell_order(self.pair, self.total_amount)
            logger.info(f"Take profit: sold {self.total_amount:.6f} @ ${current_price:,.2f} | P&L: ${pnl:.2f}")
            notify_trade("DCA Take Profit ✅", self.pair, current_price, self.total_amount, pnl)
            log_trade("dca", self.pair, "sell", current_price, self.total_amount, pnl)
            self.position = []
            self.safety_orders_placed = 0
            self.last_buy_price = None

    def run(self):
        logger.info("DCA Bot starting...")
        self.place_base_order()

        while True:
            try:
                if self.position:
                    self.check_safety_order()
                    self.check_take_profit()
                else:
                    self.place_base_order()
                time.sleep(60)
            except KeyboardInterrupt:
                logger.info("DCA Bot stopped by user")
                break
            except Exception as e:
                logger.error(f"DCA Bot error: {e}")
                notify(f"⚠️ DCA Bot error: {e}")
                time.sleep(60)

if __name__ == "__main__":
    bot = DCABot()
    bot.run()
```

**`src/test_connection.py`**
```python
"""Run this first to verify exchange connection."""
from src.exchange import get_exchange
from src.config import Config

def main():
    print(f"Environment: {Config.ENV}")
    ex = get_exchange("binance")
    ticker = ex.fetch_ticker("BTC/USDT")
    print(f"✅ Binance connected | BTC/USDT: ${ticker['last']:,.2f}")
    balance = ex.fetch_balance()
    print(f"✅ USDT Balance: ${balance['USDT']['free']:.2f}")

if __name__ == "__main__":
    main()
```

---

### Step 4 — Backtesting

```python
# backtests/backtest_grid.py
import ccxt
import pandas as pd

exchange = ccxt.binance()
ohlcv = exchange.fetch_ohlcv("BTC/USDT", "1h", limit=4380)  # ~6 months
df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])

# Simulate grid: count how many times price crosses each grid level
# Each crossing = ~1% profit (minus fees)
# Run this before going live to validate your spacing config
```

---

### Step 5 — VPS Deployment

```bash
# 1. Get a VPS — Hetzner CX11 (€3.79/mo) or DigitalOcean $4/mo droplet
# Choose Ubuntu 22.04 LTS

# 2. SSH in and set up
ssh root@your-vps-ip
apt update && apt upgrade -y
apt install python3.11 python3.11-venv git -y

# 3. Clone your repo (use private GitHub repo)
git clone https://github.com/yourusername/crypto-bots.git
cd crypto-bots/01-trading-bot
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 4. Set env vars
nano .env  # Paste your production .env content

# 5. Test connection
python src/test_connection.py

# 6. Create systemd service
```

**`deploy/grid_bot.service`**
```ini
[Unit]
Description=Crypto Grid Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/crypto-bots/01-trading-bot
Environment="PATH=/root/crypto-bots/01-trading-bot/venv/bin"
ExecStart=/root/crypto-bots/01-trading-bot/venv/bin/python src/grid_bot.py
Restart=on-failure
RestartSec=30s

[Install]
WantedBy=multi-user.target
```

```bash
# Install and start service
cp deploy/grid_bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable grid_bot
systemctl start grid_bot
systemctl status grid_bot   # Should show "active (running)"

# View live logs
journalctl -u grid_bot -f
```

---

## Requirements

**`requirements.txt`**
```
ccxt>=4.3.0
ccxt[async]>=4.3.0
pandas>=2.1.0
numpy>=1.26.0
python-dotenv>=1.0.0
python-telegram-bot>=20.7
loguru>=0.7.2
schedule>=1.2.1
pytest>=7.4.0
```

---

## Testing

```bash
# Unit tests
pytest tests/ -v

# Test on Binance Testnet before going live
# Set ENV=testnet in .env
# Run for at least 48 hours before switching to ENV=live
```

---

## Key Decisions & Tuning

| Parameter | Conservative | Balanced | Aggressive |
|-----------|-------------|----------|------------|
| Grid spacing | 1.5% | 1.0% | 0.5% |
| Grid levels | 6 | 10 | 20 |
| Stop loss | 5% | 8% | 12% |
| DCA drop trigger | 3% | 2.5% | 1.5% |
| DCA take profit | 2% | 3% | 5% |
| Max safety orders | 3 | 5 | 8 |

---

## What to Do After First Week Live

1. Check logs: `journalctl -u grid_bot --since "1 week ago"` 
2. Count fills — should be >10 for a volatile week
3. Calculate actual P&L from `logs/trades.db`
4. Compare to expected P&L from backtest
5. Adjust spacing if fills are too rare (reduce spacing) or fees are eating profit (increase spacing)
