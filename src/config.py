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

    # Discord
    DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

    # Environment
    ENV = os.getenv("ENV", "testnet")  # "testnet" or "live"

    # Grid Bot
    GRID_PAIR = os.getenv("GRID_PAIR", "BTC/USDT")
    GRID_LEVELS = int(float(os.getenv("GRID_LEVELS", 10)))
    GRID_SPACING_PCT = float(os.getenv("GRID_SPACING_PCT", 1.0))
    GRID_STOP_LOSS_PCT = float(os.getenv("GRID_STOP_LOSS_PCT", 8.0))
    _grid_capital_raw = os.getenv("GRID_TOTAL_CAPITAL", "")
    GRID_TOTAL_CAPITAL = float(_grid_capital_raw) if _grid_capital_raw.strip() else None

    # DCA Bot
    DCA_PAIR = os.getenv("DCA_PAIR", "ETH/USDT")
    DCA_MAX_SAFETY_ORDERS = int(float(os.getenv("DCA_MAX_SAFETY_ORDERS", 5)))
    DCA_PRICE_DROP_PCT = float(os.getenv("DCA_PRICE_DROP_PCT", 2.5))
    DCA_TAKE_PROFIT_PCT = float(os.getenv("DCA_TAKE_PROFIT_PCT", 3.0))
    _base_raw   = os.getenv("DCA_BASE_ORDER",   "")
    _safety_raw = os.getenv("DCA_SAFETY_ORDER",  "")
    DCA_BASE_ORDER   = float(_base_raw)   if _base_raw.strip()   else None
    DCA_SAFETY_ORDER = float(_safety_raw) if _safety_raw.strip() else None

    @classmethod
    def validate(cls):
        required = []
        if cls.ENV == "live":
            required += ["BINANCE_API_KEY", "BINANCE_SECRET"]
        else:
            required += ["BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_SECRET"]

        missing = [k for k in required if not getattr(cls, k)]

        if cls.GRID_TOTAL_CAPITAL is None:
            missing.append("GRID_TOTAL_CAPITAL")
        if cls.DCA_BASE_ORDER is None:
            missing.append("DCA_BASE_ORDER")
        if cls.DCA_SAFETY_ORDER is None:
            missing.append("DCA_SAFETY_ORDER")

        if not cls.DISCORD_WEBHOOK_URL:
            print("[Config] Warning: DISCORD_WEBHOOK_URL not set — notifications disabled")

        if missing:
            raise ValueError(f"Missing required env vars: {missing}")
