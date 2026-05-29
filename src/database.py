import sqlite3
import os
from datetime import datetime

DB_PATH = "logs/trades.db"


def init_db():
    """Create the logs directory and trades table if they don't exist."""
    os.makedirs("logs", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp  TEXT,
            bot_type   TEXT,
            pair       TEXT,
            side       TEXT,
            price      REAL,
            amount     REAL,
            pnl        REAL,
            notes      TEXT
        )
    """)
    conn.commit()
    conn.close()


def log_trade(
    bot_type: str,
    pair: str,
    side: str,
    price: float,
    amount: float,
    pnl: float = None,
    notes: str = "",
):
    """Insert a trade record into the database."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO trades (timestamp, bot_type, pair, side, price, amount, pnl, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (datetime.utcnow().isoformat(), bot_type, pair, side, price, amount, pnl, notes),
    )
    conn.commit()
    conn.close()


def get_daily_summary(bot_type: str) -> dict:
    """Return trade count and total P&L for today."""
    today = datetime.utcnow().date().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(pnl), 0)
        FROM trades
        WHERE bot_type = ? AND timestamp LIKE ?
        """,
        (bot_type, f"{today}%"),
    )
    row = cursor.fetchone()
    conn.close()
    return {"count": row[0], "pnl": row[1]}


def get_all_trades(bot_type: str = None) -> list[dict]:
    """Fetch all trades, optionally filtered by bot type."""
    conn = sqlite3.connect(DB_PATH)
    if bot_type:
        cursor = conn.execute(
            "SELECT * FROM trades WHERE bot_type = ? ORDER BY timestamp DESC", (bot_type,)
        )
    else:
        cursor = conn.execute("SELECT * FROM trades ORDER BY timestamp DESC")
    cols = [desc[0] for desc in cursor.description]
    rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
    conn.close()
    return rows
