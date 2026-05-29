import requests
from src.config import Config


def _send(content: str = None, embeds: list = None):
    """POST a message to the Discord webhook."""
    if not Config.DISCORD_WEBHOOK_URL:
        print(f"[Notifier] No webhook configured — message: {content}")
        return

    payload = {}
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds

    try:
        resp = requests.post(Config.DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[Notifier] Failed to send Discord message: {e}")


def notify(message: str):
    """Send a plain text notification to Discord (supports **markdown**)."""
    # Strip any leftover HTML tags
    message = message.replace("<b>", "**").replace("</b>", "**")
    _send(content=message)


def notify_trade(action: str, pair: str, price: float, amount: float, pnl: float = None):
    """Send a formatted trade embed to Discord."""
    is_buy = "BUY" in action.upper()
    is_profit = pnl is not None and pnl >= 0

    color = 0x2ECC71 if is_buy else (0x27AE60 if is_profit else 0xE74C3C)

    fields = [
        {"name": "Pair",   "value": pair,             "inline": True},
        {"name": "Price",  "value": f"${price:,.4f}", "inline": True},
        {"name": "Amount", "value": f"{amount:.6f}",  "inline": True},
    ]
    if pnl is not None:
        emoji = "✅" if pnl >= 0 else "🔴"
        fields.append({"name": "P&L", "value": f"{emoji} ${pnl:.2f}", "inline": True})

    embed = {
        "title" : f"🤖 {action}",
        "color" : color,
        "fields": fields,
    }
    _send(embeds=[embed])


def notify_daily_summary(bot_name: str, total_trades: int, pnl: float, open_orders: int):
    """Send an end-of-day P&L summary embed to Discord."""
    emoji = "📈" if pnl >= 0 else "📉"
    color = 0x2ECC71 if pnl >= 0 else 0xE74C3C

    embed = {
        "title" : f"{emoji} Daily Summary — {bot_name}",
        "color" : color,
        "fields": [
            {"name": "Trades today", "value": str(total_trades), "inline": True},
            {"name": "P&L",          "value": f"${pnl:.2f}",     "inline": True},
            {"name": "Open orders",  "value": str(open_orders),  "inline": True},
        ],
    }
    _send(embeds=[embed])
