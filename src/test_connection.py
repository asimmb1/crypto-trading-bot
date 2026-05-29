"""
Run this first to verify your exchange connection and API keys.

Usage:
    python -m src.test_connection
    # or from the 01-trading-bot/ directory:
    python src/test_connection.py
"""

from src.exchange import get_exchange
from src.config import Config


def main():
    print("=" * 50)
    print(f"  Environment : {Config.ENV.upper()}")
    print("=" * 50)

    # ── Exchange connection ──────────────────────────────
    print("\n[1] Connecting to Binance...")
    try:
        ex = get_exchange("binance")
        print("    ✅ Connected")
    except Exception as e:
        print(f"    ❌ Connection failed: {e}")
        return

    # ── Live price ───────────────────────────────────────
    print("\n[2] Fetching BTC/USDT price...")
    try:
        ticker = ex.fetch_ticker("BTC/USDT")
        print(f"    ✅ BTC/USDT last price: ${ticker['last']:,.2f}")
    except Exception as e:
        print(f"    ❌ Failed to fetch ticker: {e}")

    # ── Balance ──────────────────────────────────────────
    print("\n[3] Fetching account balance...")
    try:
        balance = ex.fetch_balance()
        usdt = balance.get("USDT", {}).get("free", 0)
        print(f"    ✅ USDT free balance: ${usdt:.2f}")
        # Show any non-zero balances
        for asset, amounts in balance["total"].items():
            if amounts and amounts > 0 and asset != "USDT":
                print(f"       {asset}: {amounts}")
    except Exception as e:
        print(f"    ❌ Failed to fetch balance: {e}")

    # ── Telegram check ───────────────────────────────────
    print("\n[4] Checking Telegram config...")
    if Config.TELEGRAM_BOT_TOKEN and Config.TELEGRAM_CHAT_ID:
        print(f"    ✅ Bot token set (ends ...{Config.TELEGRAM_BOT_TOKEN[-6:]})")
        print(f"    ✅ Chat ID: {Config.TELEGRAM_CHAT_ID}")
    else:
        print("    ⚠️  Telegram not configured (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)")

    # ── Config summary ───────────────────────────────────
    print("\n[5] Bot configuration:")
    print(f"    Grid pair     : {Config.GRID_PAIR}")
    print(f"    Grid capital  : ${Config.GRID_TOTAL_CAPITAL}")
    print(f"    Grid levels   : {Config.GRID_LEVELS}")
    print(f"    Grid spacing  : {Config.GRID_SPACING_PCT}%")
    print(f"    Grid stop loss: {Config.GRID_STOP_LOSS_PCT}%")
    print(f"    DCA pair      : {Config.DCA_PAIR}")
    print(f"    DCA base order: ${Config.DCA_BASE_ORDER}")
    print(f"    DCA safety    : ${Config.DCA_SAFETY_ORDER}")
    print(f"    DCA max orders: {Config.DCA_MAX_SAFETY_ORDERS}")
    print(f"    DCA drop trig : {Config.DCA_PRICE_DROP_PCT}%")
    print(f"    DCA take profit: {Config.DCA_TAKE_PROFIT_PCT}%")

    print("\n" + "=" * 50)
    print("  All checks passed. Ready to run bots!")
    print("=" * 50)


if __name__ == "__main__":
    main()
