import threading
import time
import requests
from src.config import Config

# ── Rate limiter ────────────────────────────────────────────────────────────
# Discord webhooks allow ~30 messages/minute globally, but bursts of >5 in
# rapid succession trigger 429s. During bot startup all 5 grid bots fire
# several notifications simultaneously. The lock + minimum interval serialise
# all sends so no burst ever hits the rate limit.
#
# Effect on latency: each notification waits at most _MIN_INTERVAL seconds for
# the previous one to clear. Startup burst of 10 messages takes ~12 seconds
# total instead of 2 seconds — acceptable because these are status messages,
# not trading signals.
_send_lock    = threading.Lock()
_last_sent_at = 0.0          # monotonic timestamp of the last successful send
_MIN_INTERVAL = 1.2          # seconds — safely under Discord's 1 msg/s limit


def _send(content: str = None, embeds: list = None):
    """
    POST a message to the Discord webhook.

    Thread-safe. Enforces _MIN_INTERVAL between sends. Retries once on 429
    using Discord's retry_after value before giving up.
    """
    global _last_sent_at

    if not Config.DISCORD_WEBHOOK_URL:
        print(f"[Notifier] No webhook configured — message: {content or '(embed)'}")
        return

    payload = {}
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds

    with _send_lock:
        # Enforce minimum spacing between messages
        gap = _last_sent_at + _MIN_INTERVAL - time.monotonic()
        if gap > 0:
            time.sleep(gap)

        for attempt in range(3):
            try:
                resp = requests.post(
                    Config.DISCORD_WEBHOOK_URL, json=payload, timeout=10
                )
                if resp.status_code == 429:
                    # Discord returns retry_after in seconds (float), not milliseconds.
                    try:
                        retry_after = float(resp.json().get("retry_after", 2.0))
                    except Exception:
                        retry_after = 2.0
                    print(f"[Notifier] Rate limited — waiting {retry_after:.1f}s before retry")
                    time.sleep(retry_after + 0.1)
                    continue  # retry after waiting
                resp.raise_for_status()
                _last_sent_at = time.monotonic()
                return
            except requests.exceptions.HTTPError:
                # Already handled 429 above; other HTTP errors are final
                _last_sent_at = time.monotonic()
                return
            except Exception as e:
                print(f"[Notifier] Send error (attempt {attempt + 1}/3): {e}")
                if attempt < 2:
                    time.sleep(2)

        _last_sent_at = time.monotonic()


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
