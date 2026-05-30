"""
health_server.py — Lightweight HTTP server for Railway deployment.

Endpoints:
  GET  /health      → 200 JSON status (circuit breaker, DMS, active pairs)
  POST /confirm     → Reset dead man's switch (replaces `python -m src.confirm`)
  GET  /status      → Full system status JSON
  GET  /dashboard   → HTML trader dashboard (capital, P&L, fills, CB/DMS)
  GET  /api/trades  → JSON trade data consumed by the dashboard

Run in a daemon thread so it doesn't block the bot:
    from src.health_server import start_health_server
    start_health_server()

Railway uses GET /health for its health check.
Use POST /confirm daily to keep the dead man's switch alive.
Open /dashboard in any browser for a live trader view.
"""

import json
import os
import glob
import sqlite3
import threading
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone

from loguru import logger

PORT = int(os.environ.get("PORT", 8080))
DB_PATH = "logs/trades.db"
CAPITAL_PER_PAIR = float(os.environ.get("GRID_TOTAL_CAPITAL", 100))

# Shutdown callback — registered by AdaptiveBot after it's constructed.
# Signature: (sell_positions: bool) -> None
_shutdown_callback = None


def register_shutdown_callback(callback):
    """Called by AdaptiveBot.__init__() to wire up the dashboard shutdown button."""
    global _shutdown_callback
    _shutdown_callback = callback


def _get_trade_stats() -> dict:
    """
    Read trades.db and compute per-pair stats for the dashboard.
    Returns a dict with summary + per-pair breakdown + recent fills.
    """
    # Read balance + active orders FIRST — included in every return path so
    # the Free USDT card and Active Orders table work even with zero fills.
    balance = {}
    try:
        if os.path.exists("logs/balance.json"):
            with open("logs/balance.json") as f:
                balance = json.load(f)
    except Exception:
        pass

    active_orders = []
    for path in sorted(glob.glob("logs/active_*.json")):
        try:
            with open(path) as f:
                active_orders.extend(json.load(f))
        except Exception:
            pass

    if not os.path.exists(DB_PATH):
        return {"available": False, "pairs": {}, "summary": {}, "recent": [],
                "balance": balance, "active_orders": active_orders}

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        # All trades, newest first
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY timestamp DESC"
        ).fetchall()
        conn.close()
    except Exception as e:
        logger.warning(f"Dashboard DB read error: {e}")
        return {"available": False, "pairs": {}, "summary": {}, "recent": [], "balance": balance}

    if not rows:
        return {
            "available": True,
            "pairs": {},
            "summary": {
                "total_fills":          0,
                "today_fills":          0,
                "total_round_trips":    0,
                "capital_deployed_usd": 0.0,
                "approx_gross_pnl_usd": 0.0,
                "today_pnl_usd":        0.0,
                "active_pairs":         0,
                "win_rate":             None,
                "tracked_sells":        0,
                "profitable_sells":     0,
            },
            "recent":        [],
            "reconciled":    [],
            "active_orders": active_orders,
            "balance":       balance,
            "generated_at":  datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        }

    # --- per-pair aggregation ---
    pairs = defaultdict(lambda: {
        "buys": 0, "sells": 0,
        "buy_volume_usd": 0.0, "sell_volume_usd": 0.0,
        "approx_pnl": 0.0,
    })

    GRID_SPACING = float(os.environ.get("GRID_SPACING_PCT", 1.0)) / 100
    GRID_LEVELS  = int(os.environ.get("GRID_LEVELS", 10))
    order_usd    = CAPITAL_PER_PAIR / GRID_LEVELS  # flat order size for fallback estimate

    for r in rows:
        p = pairs[r["pair"]]
        value_usd = (r["price"] or 0) * (r["amount"] or 0)
        if r["side"] == "buy":
            p["buys"] += 1
            p["buy_volume_usd"] += value_usd
        else:
            p["sells"] += 1
            p["sell_volume_usd"] += value_usd
            # Use actual recorded P&L when available (entry_price was known).
            # Fall back to the flat spacing-% estimate only for untracked sells
            # (reconciled orphans whose buy price was inferred, not exact).
            if r["pnl"] is not None:
                p["approx_pnl"] += r["pnl"]
            else:
                p["approx_pnl"] += order_usd * GRID_SPACING

    # Round trips = completed buy→sell cycles (min of buys/sells per pair)
    for pair, p in pairs.items():
        p["round_trips"] = min(p["buys"], p["sells"])
        p["approx_pnl"] = round(p["approx_pnl"], 4)
        p["buy_volume_usd"] = round(p["buy_volume_usd"], 2)
        p["sell_volume_usd"] = round(p["sell_volume_usd"], 2)

    # --- today vs all-time ---
    today = datetime.utcnow().date().isoformat()
    today_fills = sum(1 for r in rows if r["timestamp"].startswith(today))
    total_fills = len(rows)
    total_round_trips = sum(p["round_trips"] for p in pairs.values())
    total_pnl = round(sum(p["approx_pnl"] for p in pairs.values()), 4)
    active_pairs = [pair for pair, p in pairs.items() if p["buys"] + p["sells"] > 0]

    # Today's P&L — tracked sells only (pnl IS NOT NULL in DB)
    today_pnl = round(sum(
        r["pnl"] for r in rows
        if r["side"] == "sell" and r["pnl"] is not None and r["timestamp"].startswith(today)
    ), 4)

    # Win rate — tracked sells only (pnl IS NOT NULL means entry price was known)
    tracked_sells    = [r for r in rows if r["side"] == "sell" and r["pnl"] is not None]
    profitable_sells = [r for r in tracked_sells if r["pnl"] > 0]
    win_rate = round(len(profitable_sells) / len(tracked_sells) * 100, 1) if tracked_sells else None

    # Untracked sell count (for the P&L card sub-label transparency)
    untracked_sells = sum(1 for r in rows if r["side"] == "sell" and r["pnl"] is None)

    summary = {
        "total_fills":          total_fills,
        "today_fills":          today_fills,
        "total_round_trips":    total_round_trips,
        "approx_gross_pnl_usd": total_pnl,
        "today_pnl_usd":        today_pnl,
        "active_pairs":         len(active_pairs),
        "win_rate":             win_rate,
        "tracked_sells":        len(tracked_sells),
        "profitable_sells":     len(profitable_sells),
        "untracked_sells":      untracked_sells,
    }

    # Recent 30 fills for the table
    recent = [
        {
            "timestamp": r["timestamp"][:19].replace("T", " "),
            "pair":      r["pair"],
            "side":      r["side"].upper(),
            "price":     r["price"],
            "amount":    round(r["amount"], 6) if r["amount"] else None,
            "pnl":       r["pnl"],  # None for buys and untracked sells (orphaned)
        }
        for r in rows[:30]
    ]

    # Reconciled (orphaned) open orders — written by grid_bot on restart
    reconciled = []
    reconciled_file = "logs/reconciled_orders.json"
    if os.path.exists(reconciled_file):
        try:
            with open(reconciled_file) as f:
                reconciled = json.load(f)
        except Exception:
            pass

    return {
        "available":        True,
        "summary":          summary,
        "pairs":            dict(pairs),
        "recent":           recent,
        "reconciled":       reconciled,
        "active_orders":    active_orders,
        "balance":          balance,
        "grid_spacing_pct": float(os.environ.get("GRID_SPACING_PCT", 1.0)),
        "generated_at":     datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    }


def _get_round_trips() -> list:
    """
    Return every completed round trip (sell fill with tracked P&L) from trades.db,
    enriched with inferred buy price and gross return %.
    """
    if not os.path.exists(DB_PATH):
        return []
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE side='sell' AND pnl IS NOT NULL ORDER BY timestamp DESC"
        ).fetchall()
        conn.close()
    except Exception:
        return []

    trips = []
    for r in rows:
        sell_price = float(r["price"] or 0)
        amount     = float(r["amount"] or 0)
        pnl        = float(r["pnl"] or 0)
        # Infer buy price from: pnl = amount × (sell - buy)  →  buy = sell - pnl/amount
        buy_price  = round(sell_price - pnl / amount, 6) if amount > 0 else None
        gross_pct  = round(pnl / (buy_price * amount) * 100, 3) if buy_price and buy_price > 0 and amount > 0 else None
        trips.append({
            "id":         r["id"],
            "pair":       r["pair"],
            "bot_type":   r["bot_type"],
            "timestamp":  r["timestamp"][:19].replace("T", " "),
            "sell_price": sell_price,
            "buy_price":  buy_price,
            "amount":     round(amount, 6),
            "pnl":        pnl,
            "gross_pct":  gross_pct,
        })
    return trips


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Adaptive Bot Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root {
    --bg:#0f1117; --card:#1a1d27; --border:#2a2d3a;
    --text:#e2e8f0; --muted:#8892a0;
    --green:#10b981; --red:#ef4444; --yellow:#f59e0b;
    --orange:#f97316; --blue:#3b82f6; --purple:#8b5cf6;
  }
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px;padding:20px;}

  /* ── Header ── */
  .header-row{display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px;margin-bottom:6px;}
  h1{font-size:20px;font-weight:600;}
  .subtitle{color:var(--muted);font-size:12px;margin-bottom:20px;}

  /* ── DMS confirm button ── */
  #confirm-btn{
    padding:10px 20px;border-radius:8px;border:none;cursor:pointer;
    font-size:13px;font-weight:600;letter-spacing:.02em;
    background:var(--green);color:#fff;transition:background .2s,opacity .2s;
    white-space:nowrap;
  }
  #confirm-btn:disabled{opacity:.6;cursor:not-allowed;}
  #confirm-btn.loading{background:#2a6b51;}
  #confirm-btn.success{background:#059669;}
  #confirm-btn.error{background:var(--red);}
  #confirm-toast{
    font-size:11px;color:var(--muted);margin-top:4px;text-align:right;min-height:14px;
  }

  /* ── Layout ── */
  .grid{display:grid;gap:16px;}
  .grid-4{grid-template-columns:repeat(auto-fit,minmax(180px,1fr));}
  .grid-2{grid-template-columns:repeat(auto-fit,minmax(340px,1fr));}
  .card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:18px;}

  /* ── Stat cards ── */
  .stat-label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;}
  .stat-value{font-size:26px;font-weight:700;}
  .stat-sub{font-size:11px;color:var(--muted);margin-top:4px;}

  /* ── Colours ── */
  .green{color:var(--green);}.red{color:var(--red);}.yellow{color:var(--yellow);}.orange{color:var(--orange);}.blue{color:var(--blue);}

  /* ── Badges ── */
  .badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;}
  .badge-green{background:rgba(16,185,129,.15);color:var(--green);}
  .badge-red{background:rgba(239,68,68,.15);color:var(--red);}
  .badge-yellow{background:rgba(245,158,11,.15);color:var(--yellow);}
  .badge-orange{background:rgba(249,115,22,.15);color:var(--orange);}

  /* ── Tables ── */
  table{width:100%;border-collapse:collapse;}
  th{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;padding:6px 10px;text-align:left;border-bottom:1px solid var(--border);}
  td{padding:8px 10px;border-bottom:1px solid rgba(42,45,58,.5);font-size:13px;}
  tr:last-child td{border-bottom:none;}
  .buy{color:var(--green);}.sell{color:var(--red);}

  /* ── Orphaned orders panel ── */
  #orphaned-section{display:none;margin-bottom:20px;}
  .orphaned-card{
    background:var(--card);border:1px solid var(--orange);border-radius:10px;padding:18px;
  }
  .orphaned-title{
    font-size:13px;font-weight:600;color:var(--orange);text-transform:uppercase;
    letter-spacing:.05em;margin-bottom:6px;display:flex;align-items:center;justify-content:space-between;gap:8px;
  }
  .orphaned-desc{font-size:12px;color:var(--muted);margin-bottom:14px;line-height:1.6;}
  tr.orphaned-row td{background:rgba(249,115,22,.06);}
  .btn-cancel-all{
    padding:5px 12px;border-radius:6px;border:1px solid var(--orange);background:rgba(249,115,22,.15);
    color:var(--orange);font-size:11px;font-weight:600;cursor:pointer;white-space:nowrap;
    transition:background .15s;
  }
  .btn-cancel-all:hover{background:rgba(249,115,22,.3);}
  .btn-cancel-one{
    padding:3px 9px;border-radius:5px;border:1px solid rgba(239,68,68,.4);background:rgba(239,68,68,.1);
    color:var(--red);font-size:11px;font-weight:600;cursor:pointer;transition:background .15s;
  }
  .btn-cancel-one:hover{background:rgba(239,68,68,.25);}
  .btn-market-sell{
    padding:3px 9px;border-radius:5px;border:1px solid rgba(249,115,22,.6);background:rgba(249,115,22,.15);
    color:var(--orange);font-size:11px;font-weight:600;cursor:pointer;transition:background .15s;
  }
  .btn-market-sell:hover{background:rgba(249,115,22,.3);}
  .btn-market-sell:disabled{opacity:.5;cursor:not-allowed;}
  .btn-cancel-one:disabled,.btn-cancel-all:disabled{opacity:.5;cursor:not-allowed;}
  .pending-tag{font-size:10px;font-weight:600;color:var(--yellow);background:rgba(245,158,11,.15);padding:1px 5px;border-radius:3px;margin-left:4px;}

  /* ── Untracked sell highlight ── */
  tr.untracked-sell td{background:rgba(249,115,22,.05);}
  .untracked-tag{
    font-size:10px;font-weight:600;color:var(--orange);
    background:rgba(249,115,22,.15);padding:1px 5px;border-radius:3px;margin-left:4px;
  }

  /* ── Section title ── */
  .section-title{font-size:13px;font-weight:600;margin-bottom:14px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;}

  /* ── Button tooltips ── */
  .btn-wrap{position:relative;display:inline-block;}
  .btn-tip{
    display:none;position:absolute;top:calc(100% + 10px);right:0;
    width:min(290px, calc(100vw - 20px));padding:12px 14px;
    background:#12151f;border:1px solid #2a2d3a;border-radius:9px;
    font-size:12px;color:#c8d0db;line-height:1.65;
    z-index:300;box-shadow:0 6px 24px rgba(0,0,0,.5);
    pointer-events:none;
  }
  .btn-tip strong{color:#e2e8f0;}
  .btn-tip .tip-step{display:flex;gap:7px;margin-top:5px;}
  .btn-tip .tip-step::before{content:"→";color:#3b82f6;flex-shrink:0;}
  .btn-wrap:hover .btn-tip{display:block;}

  /* ── Shutdown button ── */
  #shutdown-btn{
    padding:10px 18px;border-radius:8px;border:1px solid var(--red);background:rgba(239,68,68,.12);
    color:var(--red);font-size:13px;font-weight:600;cursor:pointer;transition:background .2s;
    white-space:nowrap;
  }
  #shutdown-btn:hover{background:rgba(239,68,68,.25);}

  /* ── Shutdown modal ── */
  #shutdown-modal{
    display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:200;
    align-items:center;justify-content:center;
  }
  #shutdown-modal.open{display:flex;}
  .modal-box{
    background:var(--card);border:1px solid var(--red);border-radius:14px;
    padding:32px;max-width:460px;width:90%;
  }
  .modal-title{font-size:18px;font-weight:700;color:var(--red);margin-bottom:10px;}
  .modal-desc{font-size:13px;color:var(--muted);line-height:1.7;margin-bottom:20px;}
  .modal-option{
    display:flex;align-items:flex-start;gap:10px;padding:14px;border-radius:8px;
    border:1px solid var(--border);margin-bottom:16px;cursor:pointer;transition:border-color .15s;
  }
  .modal-option:hover{border-color:var(--red);}
  .modal-option input{margin-top:2px;accent-color:var(--red);}
  .modal-option-label{font-size:13px;font-weight:600;color:var(--text);}
  .modal-option-sub{font-size:11px;color:var(--muted);margin-top:3px;line-height:1.5;}
  .modal-confirm-label{font-size:12px;color:var(--muted);margin-bottom:6px;display:block;}
  .modal-input{
    width:100%;padding:10px 12px;background:var(--bg);border:1px solid var(--border);
    border-radius:6px;color:var(--text);font-size:14px;font-family:monospace;
    letter-spacing:.08em;transition:border-color .15s;
  }
  .modal-input:focus{outline:none;border-color:var(--red);}
  .modal-actions{display:flex;gap:10px;margin-top:20px;}
  .modal-cancel{
    flex:1;padding:11px;border-radius:7px;border:1px solid var(--border);
    background:transparent;color:var(--text);font-size:13px;cursor:pointer;
  }
  .modal-submit{
    flex:1;padding:11px;border-radius:7px;border:none;background:var(--red);
    color:#fff;font-size:13px;font-weight:700;cursor:pointer;opacity:.4;transition:opacity .15s;
  }
  .modal-submit.ready{opacity:1;}
  .modal-submit:disabled{cursor:not-allowed;}

  /* ── Status row + refresh ── */
  .status-row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px;align-items:center;}
  #refresh-bar{font-size:11px;color:var(--muted);margin-top:20px;}
  progress{accent-color:var(--blue);width:60px;height:6px;vertical-align:middle;margin-left:6px;}
  canvas{max-height:200px;}

  /* ── Stat card tooltips ── */
  .stat-label{display:flex;align-items:center;gap:4px;}
  .cinfo{
    display:inline-flex;align-items:center;justify-content:center;
    width:14px;height:14px;border-radius:50%;border:1px solid rgba(136,146,160,.5);
    color:var(--muted);font-size:8px;font-weight:700;cursor:help;
    position:relative;flex-shrink:0;line-height:1;
  }
  .ctip{
    display:none;position:absolute;left:50%;transform:translateX(-50%);top:calc(100% + 8px);
    background:#1a1f35;border:1px solid var(--border);border-radius:9px;
    padding:11px 13px;font-size:11px;line-height:1.65;color:var(--text);
    width:240px;z-index:200;box-shadow:0 6px 24px rgba(0,0,0,.7);
    font-weight:400;pointer-events:none;white-space:normal;
  }
  .ctip b{color:#c9d1db;}
  .cinfo:hover .ctip{display:block;}

  /* ── Table total footer row ── */
  tfoot tr td{
    padding:8px 10px;font-size:12px;font-weight:700;
    border-top:2px solid var(--border);background:rgba(255,255,255,.03);
  }
  .match-badge{
    display:inline-block;padding:1px 7px;border-radius:4px;font-size:10px;font-weight:700;margin-left:6px;
  }
  .match-ok{background:rgba(16,185,129,.15);color:var(--green);}
  .match-warn{background:rgba(245,158,11,.15);color:var(--yellow);}

  /* ── Round Trips History Modal ── */
  #trips-modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.88);z-index:500;overflow-y:auto;}
  #trips-modal.open{display:block;}
  .trips-inner{max-width:1100px;margin:0 auto;padding:28px 20px 60px;}
  .trips-topbar{display:flex;align-items:center;gap:14px;margin-bottom:22px;flex-wrap:wrap;}
  .trips-topbar h2{font-size:18px;font-weight:700;flex:1;}
  .trips-close-btn{background:none;border:1px solid var(--border);color:var(--text);border-radius:7px;padding:7px 16px;cursor:pointer;font-size:13px;}
  .trips-close-btn:hover{background:var(--border);}
  .trips-stats-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:22px;}
  .trips-kpi{background:var(--card);border:1px solid var(--border);border-radius:9px;padding:12px 18px;flex:1;min-width:120px;}
  .trips-kpi-label{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;}
  .trips-kpi-value{font-size:20px;font-weight:700;}
  .trips-chart-wrap{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:22px;}
  .trips-chart-title{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:10px;}
  .trips-filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px;}
  .trips-filter-btn{padding:4px 12px;border-radius:20px;border:1px solid var(--border);background:transparent;color:var(--muted);font-size:12px;cursor:pointer;transition:all .15s;}
  .trips-filter-btn.active,.trips-filter-btn:hover{border-color:var(--blue);color:var(--blue);background:rgba(59,130,246,.1);}
  .trips-empty{text-align:center;color:var(--muted);padding:60px 0;font-size:14px;}
  .trips-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;}
  .trip-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;transition:border-color .15s;}
  .trip-card:hover{border-color:var(--blue);}
  .trip-card-top{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px;}
  .trip-pair-badge{font-size:11px;font-weight:700;color:var(--muted);background:rgba(255,255,255,.05);padding:2px 8px;border-radius:4px;}
  .trip-bot-badge{font-size:10px;color:var(--blue);background:rgba(59,130,246,.1);padding:1px 6px;border-radius:3px;}
  .trip-pnl{font-size:20px;font-weight:700;}
  .trip-bar-section{margin:10px 0 6px;}
  .trip-bar-track{height:6px;border-radius:3px;background:rgba(255,255,255,.06);position:relative;overflow:hidden;}
  .trip-bar-fill{height:100%;border-radius:3px;background:linear-gradient(90deg,rgba(16,185,129,.4),var(--green));}
  .trip-prices{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin-top:5px;}
  .trip-prices .tp-buy{color:var(--green);}
  .trip-prices .tp-sell{color:var(--red);}
  .trip-meta{font-size:11px;color:var(--muted);display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;padding-top:8px;border-top:1px solid var(--border);}

  /* ── Grid Context Panel (slide-in from right) ── */
  #grid-panel-backdrop{
    display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:699;
    transition:opacity .28s;opacity:0;
  }
  #grid-panel-backdrop.open{display:block;opacity:1;}
  #grid-panel{
    position:fixed;top:0;right:0;height:100vh;
    width:min(440px,100vw);
    background:#1a1e30;border-left:2px solid rgba(59,130,246,.3);
    box-shadow:-8px 0 40px rgba(0,0,0,.6);
    transform:translateX(100%);
    transition:transform .28s cubic-bezier(.4,0,.2,1);
    overflow-y:auto;z-index:700;display:flex;flex-direction:column;
  }
  #grid-panel.open{transform:translateX(0);}

  /* Panel header */
  .gp-header{
    position:sticky;top:0;background:#13172a;z-index:10;
    display:flex;justify-content:space-between;align-items:flex-start;
    padding:18px 20px 14px;border-bottom:1px solid var(--border);gap:12px;
  }
  .gp-header-left{flex:1;min-width:0;}
  .gp-pair-name{font-size:16px;font-weight:700;margin-bottom:2px;}
  .gp-subtitle{font-size:11px;color:var(--muted);}
  .gp-header-btns{display:flex;gap:8px;align-items:center;flex-shrink:0;}
  .gp-demo-btn{
    font-size:11px;padding:5px 10px;border-radius:5px;cursor:pointer;
    border:1px solid var(--blue);color:var(--blue);background:rgba(59,130,246,.1);white-space:nowrap;
  }
  .gp-demo-btn:hover{background:rgba(59,130,246,.25);}
  .gp-close{
    background:none;border:1px solid var(--border);color:var(--muted);
    cursor:pointer;font-size:16px;line-height:1;padding:5px 8px;border-radius:5px;
  }
  .gp-close:hover{color:var(--text);border-color:var(--text);}

  /* Demo banner */
  .gp-demo-banner{
    background:rgba(59,130,246,.1);border-bottom:1px solid rgba(59,130,246,.3);
    padding:6px 20px;font-size:11px;color:var(--blue);text-align:center;
  }

  /* Panel body */
  .gp-body{padding:16px 20px;flex:1;}
  .gp-section-hdr{
    font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;
    color:var(--muted);margin:18px 0 8px;display:flex;align-items:center;gap:8px;
  }
  .gp-section-hdr:first-child{margin-top:0;}
  .gp-empty{font-size:12px;color:var(--muted);padding:6px 0;font-style:italic;}

  /* Order rows — same colour scheme as before */
  .gp-row{
    display:flex;align-items:center;gap:10px;
    padding:8px 10px;border-radius:7px;margin:3px 0;
    border-left:3px solid transparent;font-size:12px;
  }
  .gp-row-sell  {border-left-color:var(--red);   background:rgba(239,68,68,.07);}
  .gp-row-buy   {border-left-color:var(--green);  background:rgba(16,185,129,.07);}
  .gp-row-focus {border-left-color:var(--blue);   background:rgba(59,130,246,.14);border:1px solid rgba(59,130,246,.35);}
  .gp-row-fill-buy  {border-left-color:var(--purple); background:rgba(139,92,246,.07);}
  .gp-row-fill-sell {border-left-color:var(--yellow);  background:rgba(245,158,11,.07);}
  .gp-zone-div{
    display:flex;align-items:center;gap:6px;margin:6px 0;
    font-size:10px;color:var(--muted);
  }
  .gp-zone-div::before,.gp-zone-div::after{content:'';flex:1;border-top:1px dashed rgba(255,255,255,.12);}
  .gp-price{font-family:monospace;font-size:12px;width:82px;flex-shrink:0;color:var(--text);}
  .gp-icon{font-size:14px;width:16px;text-align:center;flex-shrink:0;}
  .gp-icon-sell  {color:var(--red);}
  .gp-icon-buy   {color:var(--green);}
  .gp-icon-focus {color:var(--blue);}
  .gp-icon-fbuy  {color:var(--purple);}
  .gp-icon-fsell {color:var(--yellow);}
  .gp-label{font-size:12px;flex:1;}
  .gp-label-sell  {color:var(--red);}
  .gp-label-buy   {color:var(--green);}
  .gp-label-focus {color:var(--blue);font-weight:700;}
  .gp-label-fbuy  {color:var(--purple);}
  .gp-label-fsell {color:var(--yellow);}
  .gp-time{font-size:11px;color:var(--muted);flex-shrink:0;}

  /* Chain section */
  .gp-streak-badge{
    padding:2px 8px;border-radius:10px;font-size:10px;font-weight:700;
    background:rgba(245,158,11,.15);color:var(--yellow);white-space:nowrap;
    text-transform:none;letter-spacing:0;
  }
  .gp-chain-pair{
    background:rgba(255,255,255,.02);border:1px solid var(--border);
    border-radius:8px;padding:10px 12px;margin-bottom:8px;
  }
  .gp-chain-buy{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--green);padding:3px 0;}
  .gp-chain-sell{display:flex;align-items:center;gap:8px;font-size:12px;padding:3px 0;}
  .gp-chain-sell.tracked{color:var(--yellow);}
  .gp-chain-sell.open{color:var(--red);}
  .gp-chain-connector{
    display:flex;align-items:center;gap:10px;
    margin:2px 0 2px 6px;padding:3px 0 3px 14px;
    border-left:2px solid rgba(255,255,255,.1);
    font-size:11px;color:var(--muted);
  }
  .gp-chain-pnl-positive{color:var(--green);font-weight:700;}
  .gp-chain-pending{
    font-size:11px;color:var(--muted);font-style:italic;
    padding:6px 0;border-top:1px dashed var(--border);margin-top:6px;
  }
  .gp-chain-break{
    display:flex;align-items:center;gap:6px;margin:12px 0 8px;
    font-size:10px;color:var(--muted);
  }
  .gp-chain-break::before,.gp-chain-break::after{content:'';flex:1;border-top:1px dashed rgba(255,255,255,.1);}

  /* Legend */
  .gp-legend{
    padding:14px 20px 20px;border-top:1px solid var(--border);
    display:flex;flex-wrap:wrap;gap:10px;font-size:11px;
    position:sticky;bottom:0;background:#13172a;
  }
  .gp-legend-item{display:flex;align-items:center;gap:5px;white-space:nowrap;}

  /* ── Mobile responsive ── */
  @media (max-width: 640px) {
    body { padding: 12px; }

    /* Header — stack logo above buttons */
    .header-row { flex-direction: column; gap: 10px; }
    .header-row > div:last-child {
      display: flex; flex-direction: column; gap: 8px; width: 100%;
    }
    .header-row > div:last-child > div,
    .header-row > div:last-child > .btn-wrap { width: 100%; }
    #confirm-btn { width: 100%; text-align: center; }
    /* Shutdown less prominent on mobile — outline style, half width, right-aligned */
    #shutdown-btn { width: auto; font-size:12px; padding:7px 14px; float:right; }
    #confirm-toast { text-align: left; }

    /* Stat cards — 2 columns */
    .grid-4 { grid-template-columns: repeat(2, 1fr); gap: 10px; }
    .stat-value { font-size: 20px; }
    .card { padding: 12px; }

    /* Chart + pair table — single column */
    .grid-2 { grid-template-columns: 1fr; }

    /* Tables — scroll horizontally instead of overflowing */
    .card { overflow-x: auto; }
    table { min-width: 480px; }

    /* Orphaned panel */
    .orphaned-title { flex-direction: column; align-items: flex-start; gap: 8px; }
    .orphaned-card { overflow-x: auto; }

    /* Card tooltips — clamp to viewport width */
    .ctip {
      width: min(240px, calc(100vw - 48px));
      left: 0; transform: none;
    }

    /* Button tooltips — already viewport-aware via min() */
  }

  /* Shutdown modal — scrollable, fits any screen height */
  .modal-box {
    max-height: 90dvh;
    overflow-y: auto;
  }
  @media (max-width: 520px) {
    #shutdown-modal { align-items: flex-end; padding: 0; }
    .modal-box {
      width: 100%; max-width: 100%; max-height: 92dvh;
      border-radius: 16px 16px 0 0; padding: 24px 16px;
      border-left: none; border-right: none; border-bottom: none;
    }
    .modal-option-sub { font-size: 10px; line-height: 1.4; }
    .modal-actions { gap: 8px; }
  }
</style>
</head>
<body>

<!-- ── Header ───────────────────────────────────────────────────────── -->
<div class="header-row">
  <div>
    <h1>&#x1F916; Adaptive Bot</h1>
  </div>
  <div style="display:flex;gap:10px;align-items:flex-start;flex-wrap:wrap;justify-content:flex-end">

    <!-- Confirm I'm Alive -->
    <div style="text-align:right">
      <div class="btn-wrap">
        <button id="confirm-btn" onclick="confirmAlive()">&#x2764;&#xFE0F; Confirm I'm Alive</button>
        <div class="btn-tip">
          <strong>Dead Man's Switch (DMS)</strong><br>
          A safety mechanism that halts all trading if you haven't checked in within 30 hours — preventing runaway bots if you lose access.<br><br>
          <div class="tip-step">Clicking this resets the 30-hour timer.</div>
          <div class="tip-step">Must be pressed at least once per day.</div>
          <div class="tip-step">The DMS badge below shows hours remaining.</div>
        </div>
      </div>
      <div id="confirm-toast"></div>
    </div>

    <!-- Shutdown -->
    <div class="btn-wrap">
      <button id="shutdown-btn" onclick="openShutdownModal()">&#x26D4; Shutdown</button>
      <div class="btn-tip" style="border-color:rgba(239,68,68,.4);">
        <strong>Emergency Stop</strong><br>
        Cancels all open orders across every trading pair and stops all bots.<br><br>
        <div class="tip-step">Choose to hold positions or market-sell to USDT.</div>
        <div class="tip-step">System is locked — bots won't auto-restart.</div>
        <div class="tip-step">Portfolio monitor is notified and stays running.</div>
        <div class="tip-step">Requires typing SHUTDOWN to confirm.</div>
        <br><span style="color:var(--muted)">To restart: <code style="background:#0f1117;padding:1px 5px;border-radius:3px">python -m src.resume --confirm</code></span>
      </div>
    </div>

  </div>
</div>
<p class="subtitle" id="generated-at">Loading...</p>

<!-- ── Shutdown modal ────────────────────────────────────────────────── -->
<div id="shutdown-modal">
  <div class="modal-box">
    <div class="modal-title">&#x26D4; Emergency Shutdown</div>
    <p class="modal-desc">
      Stops all trading bots and cancels every open order immediately.
      The portfolio monitor keeps running as your safety net.
      Choose what happens to your existing crypto holdings:
    </p>

    <label class="modal-option">
      <input type="radio" name="sell-mode" value="hold" checked onchange="updateShutdownMode(this)"/>
      <div>
        <div class="modal-option-label">&#x1F6D1; Cancel orders &mdash; keep positions</div>
        <div class="modal-option-sub">
          <strong>What happens:</strong><br>
          &bull; All open limit orders cancelled across every pair<br>
          &bull; Your crypto holdings stay in your account untouched<br>
          &bull; All bots stop placing new orders immediately<br>
          &bull; System locked — bots won't auto-restart<br>
          &bull; Portfolio monitor notified via halt signal<br>
          &bull; You review and exit positions manually on the exchange<br><br>
          <strong>Use when:</strong> market is falling and you don't want to sell at the bottom. Limit your losses by reviewing before you exit.
        </div>
      </div>
    </label>

    <label class="modal-option">
      <input type="radio" name="sell-mode" value="sell" onchange="updateShutdownMode(this)"/>
      <div>
        <div class="modal-option-label">&#x1F4B8; Cancel orders + sell everything to USDT</div>
        <div class="modal-option-sub">
          <strong>What happens (everything above, plus):</strong><br>
          &bull; Market sells all held crypto to USDT at current exchange price<br>
          &bull; Each sell result is confirmed individually on Discord<br>
          &bull; Any sell failures are flagged on Discord for manual action<br>
          &bull; You end up 100% in USDT — no open positions<br><br>
          <strong>&#x26A0; Slippage warning:</strong> Market orders accept the current bid. In a fast-moving or thin market this can be significantly below the last traded price.<br><br>
          <strong>Use when:</strong> you need to go completely flat right now and accept market price.
        </div>
      </div>
    </label>

    <label class="modal-confirm-label">Type <strong>SHUTDOWN</strong> in capitals to confirm:</label>
    <input
      class="modal-input"
      id="shutdown-input"
      type="text"
      placeholder="SHUTDOWN"
      autocomplete="off"
      oninput="onShutdownInput(this)"
    />

    <div class="modal-actions">
      <button class="modal-cancel" onclick="closeShutdownModal()">Cancel</button>
      <button class="modal-submit" id="shutdown-submit" onclick="executeShutdown()" disabled>
        Shutdown Bot
      </button>
    </div>
  </div>
</div>

<!-- ── Status badges ─────────────────────────────────────────────────── -->
<div class="status-row" id="status-badges">
  <span class="badge badge-yellow">Loading...</span>
</div>

<!-- ── Stat cards ─────────────────────────────────────────────────────── -->
<div class="grid grid-4" style="margin-bottom:20px">

  <div class="card">
    <div class="stat-label">Free USDT
      <span class="cinfo">?<span class="ctip"><b>Available Capital</b><br>USDT in your exchange account not locked in any open order.<br><br>Goes <b style="color:var(--green)">up</b> when: a buy order is cancelled (USDT returned) or a sell order fills (USDT received).<br>Goes <b style="color:var(--red)">down</b> when: new buy orders are placed.<br><br>Updates every ~30s. Watch this live as you cancel orders.</span></span>
    </div>
    <div class="stat-value green" id="s-usdt-free">—</div>
    <div class="stat-sub" id="s-usdt-free-sub"></div>
  </div>

  <div class="card">
    <div class="stat-label">In Buy Orders
      <span class="cinfo">?<span class="ctip"><b>USDT Locked in Open Buy Orders</b><br>The exact amount of USDT currently reserved on the exchange in pending limit buy orders. Updates every ~30s.<br><br>Goes <b style="color:var(--red)">up</b> when new buy orders are placed.<br>Goes <b style="color:var(--green)">down</b> when buy orders are cancelled (USDT freed) or fill (USDT becomes base asset).<br><br>Add this to <b>Free USDT</b> to see your total USDT balance.</span></span>
    </div>
    <div class="stat-value" id="s-capital">—</div>
    <div class="stat-sub" id="s-capital-sub">USDT reserved in open buys</div>
  </div>

  <div class="card">
    <div class="stat-label">Gross P&amp;L
      <span class="cinfo">?<span class="ctip"><b>All-Time Profit (Gross)</b><br>Uses <b>actual recorded P&amp;L</b> from the database for tracked sells (where the matching buy price was known).<br><br>For untracked sells (reconciled orphans with inferred entry), falls back to: grid spacing% &times; order size as an estimate.<br><br>Does <b>not</b> deduct Binance fees (0.1%/side, 0.075% with BNB). Before going live, switch to fetch_my_trades() for net P&amp;L.</span></span>
    </div>
    <div class="stat-value" id="s-pnl">—</div>
    <div class="stat-sub" id="s-pnl-sub"></div>
  </div>

  <div class="card">
    <div class="stat-label">Win Rate
      <span class="cinfo">?<span class="ctip"><b>% of Profitable Sells</b><br>How many tracked sell orders closed with positive P&amp;L.<br><br>Only counts sells where the entry price was known (bot tracked the matching buy). Reconciled orphan sells use an inferred entry and are excluded.</span></span>
    </div>
    <div class="stat-value" id="s-winrate">—</div>
    <div class="stat-sub" id="s-winrate-sub"></div>
  </div>

  <div class="card" id="rt-card" onclick="openTripsModal()" style="cursor:pointer;transition:border-color .2s;" onmouseenter="this.style.borderColor='var(--blue)'" onmouseleave="this.style.borderColor=''">
    <div class="stat-label">Round Trips
      <span class="cinfo">?<span class="ctip"><b>Completed Buy&rarr;Sell Cycles</b><br>Each round trip = one buy order filled + its counter-sell order filled.<br><br>Click this card to see the full history — entry/exit prices, P&L chart, and per-trip breakdown.</span></span>
    </div>
    <div class="stat-value" id="s-rt">—</div>
    <div class="stat-sub" id="s-rt-sub" style="color:var(--blue);font-size:10px">click to view history →</div>
  </div>

  <div class="card">
    <div class="stat-label">Total Fills
      <span class="cinfo">?<span class="ctip"><b>All Orders Executed</b><br>Every buy and sell fill logged since the bot started (buys + sells combined).<br><br>Each round trip needs 2 fills. A high fill count in a choppy market means the grid is actively capturing oscillations — exactly what it&apos;s designed for.</span></span>
    </div>
    <div class="stat-value" id="s-fills">—</div>
    <div class="stat-sub" id="s-fills-sub"></div>
  </div>

</div>

<!-- ── Charts + pair table ───────────────────────────────────────────── -->
<div class="grid grid-2" style="margin-bottom:20px">
  <div class="card">
    <div class="section-title">Fills by Pair</div>
    <canvas id="chart-fills"></canvas>
  </div>
  <div class="card">
    <div class="section-title">Per-Pair Fill History <span style="font-size:10px;font-weight:400;color:var(--muted);text-transform:none;letter-spacing:0">(cumulative since bot started — not current open orders)</span></div>
    <table>
      <thead><tr><th>Pair</th><th>Buy Fills</th><th>Sell Fills</th><th>Trips</th><th>Est. P&amp;L</th></tr></thead>
      <tbody id="pair-tbody"></tbody>
    </table>
  </div>
</div>

<!-- ── Active open orders ────────────────────────────────────────────── -->
<div class="card" id="active-orders-card" style="margin-bottom:20px;display:none;">
  <div class="section-title" style="display:flex;justify-content:space-between;align-items:center;">
    <span>Active Open Orders</span>
    <span id="active-orders-meta" style="font-size:11px;color:var(--muted);font-weight:400;text-transform:none;letter-spacing:0"></span>
  </div>
  <table>
    <thead>
      <tr>
        <th>Pair</th><th>Side</th><th>Price</th><th>Amount</th><th>Value USD</th>
      </tr>
    </thead>
    <tbody id="active-tbody"></tbody>
    <tfoot id="active-tfoot"></tfoot>
  </table>
</div>

<!-- ── Orphaned / unmanaged orders ───────────────────────────────────── -->
<div id="orphaned-section">
  <div class="orphaned-card">
    <div class="orphaned-title">
      <span>&#x26A0;&#xFE0F; Unmanaged Open Orders &mdash; Action Required</span>
      <div class="btn-wrap">
        <button class="btn-cancel-all" id="cancel-all-btn" onclick="cancelAllOrphans()">Cancel All</button>
        <div class="btn-tip" style="border-color:rgba(249,115,22,.4);">
          <strong>Clear All Unmanaged Orders</strong><br>
          BUY orders are cancelled — USDT is returned by the exchange.<br>
          SELL orders are cancelled and tokens are <strong>immediately market-sold to USDT</strong> so nothing is left unattended.<br><br>
          <div class="tip-step">Processed within 30 seconds on the bot's next cycle.</div>
          <div class="tip-step">Bot removes orders from memory first — no fake fills logged.</div>
          <div class="tip-step">Market sells fire at current bid price (slight slippage possible).</div>
        </div>
      </div>
    </div>
    <p class="orphaned-desc">
      These orders existed on the exchange before the last bot restart — P&amp;L is not tracked for them.
      They also appear in <strong>Active Open Orders</strong> above (same orders, two views).
      <strong>BUY:</strong> Cancel safely — USDT returned by exchange.
      <strong>SELL:</strong> Cancelling will immediately market-sell the tokens to USDT so nothing is left unattended.
    </p>
    <table>
      <thead><tr><th>Pair</th><th>Side</th><th>Price</th><th>Amount</th><th>Age</th><th>Action</th></tr></thead>
      <tbody id="orphaned-tbody"></tbody>
    </table>
  </div>
</div>

<!-- ── Recent fills ───────────────────────────────────────────────────── -->
<div class="card">
  <div class="section-title">Recent Fills (last 30)</div>
  <table>
    <thead><tr><th>Time (UTC)</th><th>Pair</th><th>Side</th><th>Price</th><th>Amount</th><th>Value USD</th><th>P&amp;L</th></tr></thead>
    <tbody id="fills-tbody"></tbody>
  </table>
</div>

<div id="refresh-bar">Auto-refreshing every 60s <progress id="prog" max="60" value="60"></progress></div>

<!-- ── Round Trip History Modal ──────────────────────────────────────── -->
<div id="trips-modal">
  <div class="trips-inner">
    <div class="trips-topbar">
      <button class="trips-close-btn" onclick="closeTripsModal()">← Back to Dashboard</button>
      <h2>&#x1F504; Round Trip History</h2>
    </div>
    <div class="trips-stats-row" id="trips-kpis"></div>
    <div class="trips-chart-wrap">
      <div class="trips-chart-title">Cumulative P&amp;L over time</div>
      <canvas id="trips-chart" style="max-height:180px"></canvas>
    </div>
    <div class="trips-filters" id="trips-filters"></div>
    <div id="trips-list"></div>
  </div>
</div>

<!-- ── Grid Context Panel (slides in from right) ─────────────────────── -->
<div id="grid-panel-backdrop" onclick="closeGridPanel()"></div>
<div id="grid-panel">
  <div class="gp-header">
    <div class="gp-header-left">
      <div class="gp-pair-name" id="gp-pair-name">SOL/USDT</div>
      <div class="gp-subtitle" id="gp-subtitle">Grid Context &mdash; Current Session</div>
    </div>
    <div class="gp-header-btns">
      <button class="gp-demo-btn" onclick="showGridDemo()">&#x1F4CB;&nbsp;Demo</button>
      <button class="gp-close" onclick="closeGridPanel()">&#x2715;</button>
    </div>
  </div>
  <div id="gp-demo-banner" class="gp-demo-banner" style="display:none">
    Demo mode &mdash; showing all possible states with mock data
  </div>
  <div class="gp-body">
    <div class="gp-section-hdr">Open Orders</div>
    <div id="gp-orders"></div>
    <div class="gp-section-hdr" id="gp-fills-hdr" style="display:none">Recent Fills</div>
    <div id="gp-fills"></div>
    <div class="gp-section-hdr" id="gp-chain-hdr" style="display:none">
      Grid Chain
      <span class="gp-streak-badge" id="gp-streak-badge"></span>
    </div>
    <div id="gp-chain"></div>
  </div>
  <div class="gp-legend">
    <span class="gp-legend-item"><span class="gp-icon gp-icon-sell">&#x25CF;</span><span style="color:var(--red)">Open SELL</span></span>
    <span class="gp-legend-item"><span class="gp-icon gp-icon-buy">&#x25CF;</span><span style="color:var(--green)">Open BUY</span></span>
    <span class="gp-legend-item"><span class="gp-icon gp-icon-focus">&#x25B6;</span><span style="color:var(--blue)">This order</span></span>
    <span class="gp-legend-item"><span class="gp-icon gp-icon-fsell">&#x2605;</span><span style="color:var(--yellow)">Sell filled</span></span>
    <span class="gp-legend-item"><span class="gp-icon gp-icon-fbuy">&#x2713;</span><span style="color:var(--purple)">Buy filled</span></span>
  </div>
</div>

<script>
let fillsChart  = null;
let tripsChart  = null;
let countdown   = 60;
let _spacingPct = 1.0;   // updated from API response
let _activeOrders = [];   // kept fresh for grid popup
let _recentFills  = [];   // kept fresh for grid popup

// ══════════════════════════════════════════════════════════════════════════════
// ROUND TRIP HISTORY MODAL
// ══════════════════════════════════════════════════════════════════════════════

let _tripsData    = [];
let _tripsFilter  = 'ALL';

async function openTripsModal() {
  document.getElementById('trips-modal').classList.add('open');
  document.body.style.overflow = 'hidden';
  _tripsData = await fetch('/api/round_trips').then(r => r.json()).catch(() => []);
  renderTripsModal();
}

function closeTripsModal() {
  document.getElementById('trips-modal').classList.remove('open');
  document.body.style.overflow = '';
}

// Close on background click
document.addEventListener('click', e => {
  const modal = document.getElementById('trips-modal');
  if (modal.classList.contains('open') && e.target === modal) closeTripsModal();
});

function renderTripsModal() {
  const trips = _tripsData;

  // ── KPI summary ──────────────────────────────────────────────────────────
  const totalPnl  = trips.reduce((s,t) => s + (t.pnl || 0), 0);
  const best      = trips.reduce((b,t) => t.pnl > (b?.pnl||0) ? t : b, null);
  const pairs     = [...new Set(trips.map(t => t.pair))];
  document.getElementById('trips-kpis').innerHTML = [
    ['Total Trips',   trips.length,                                              ''],
    ['Total P&L',     (totalPnl >= 0 ? '+' : '') + '$' + totalPnl.toFixed(4),  totalPnl >= 0 ? 'green' : 'red'],
    ['Best Trade',    best ? ('+$' + best.pnl.toFixed(4) + ' ' + best.pair.split('/')[0]) : '—', 'green'],
    ['Active Pairs',  pairs.length + ' pair' + (pairs.length !== 1 ? 's' : ''), ''],
  ].map(([label,val,cls]) => `
    <div class="trips-kpi">
      <div class="trips-kpi-label">${label}</div>
      <div class="trips-kpi-value ${cls}">${val}</div>
    </div>`).join('');

  // ── Cumulative P&L chart ─────────────────────────────────────────────────
  const sorted    = [...trips].sort((a,b) => a.timestamp.localeCompare(b.timestamp));
  let cum = 0;
  const chartData = sorted.map(t => { cum += (t.pnl||0); return { x: t.timestamp, y: +cum.toFixed(6) }; });
  if (tripsChart) { tripsChart.destroy(); tripsChart = null; }
  const ctx = document.getElementById('trips-chart');
  if (typeof Chart !== 'undefined' && chartData.length > 0) {
    tripsChart = new Chart(ctx, {
      type: 'line',
      data: {
        datasets: [{
          label: 'Cumulative P&L',
          data: chartData,
          borderColor: '#10b981',
          backgroundColor: 'rgba(16,185,129,.08)',
          borderWidth: 2,
          pointRadius: chartData.length < 30 ? 4 : 2,
          pointBackgroundColor: '#10b981',
          fill: true,
          tension: 0.3,
        }]
      },
      options: {
        responsive: true,
        parsing: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { type: 'category', ticks: { color: '#8892a0', maxTicksLimit: 8 }, grid: { color: '#2a2d3a' } },
          y: { ticks: { color: '#8892a0', callback: v => '$' + v.toFixed(3) }, grid: { color: '#2a2d3a' } },
        }
      }
    });
  } else if (chartData.length === 0) {
    ctx.insertAdjacentHTML('beforebegin', '<p style="color:var(--muted);font-size:12px;text-align:center;padding:20px 0">No tracked round trips yet — P&L chart appears as sell orders complete</p>');
  }

  // ── Pair filter pills ────────────────────────────────────────────────────
  const allPairs = ['ALL', ...new Set(trips.map(t => t.pair.split('/')[0]))];
  document.getElementById('trips-filters').innerHTML = allPairs.map(p =>
    `<button class="trips-filter-btn ${_tripsFilter === p ? 'active' : ''}"
      onclick="_tripsFilter='${p}';renderTripsModal()">${p}</button>`
  ).join('');

  // ── Trip cards ───────────────────────────────────────────────────────────
  const filtered = _tripsFilter === 'ALL' ? trips : trips.filter(t => t.pair.startsWith(_tripsFilter + '/'));
  const listEl   = document.getElementById('trips-list');

  if (filtered.length === 0) {
    listEl.innerHTML = `<div class="trips-empty">
      ${trips.length === 0
        ? '&#x23F3; No round trips recorded yet. A round trip completes when a sell order fills with a tracked entry price. Keep the bot running &mdash; they will appear here as grid cycles complete.'
        : 'No trips for this pair with current filter.'}
    </div>`;
    return;
  }

  listEl.innerHTML = '<div class="trips-grid">' + filtered.map(t => {
    const pnlCls   = (t.pnl||0) >= 0 ? 'green' : 'red';
    const pnlSign  = (t.pnl||0) >= 0 ? '+' : '';
    const pct      = t.gross_pct != null ? ` (${t.gross_pct > 0 ? '+' : ''}${t.gross_pct.toFixed(2)}%)` : '';
    // Bar fill: represent the spread as % of buy price range shown
    const spread   = t.buy_price && t.sell_price ? ((t.sell_price - t.buy_price) / t.buy_price * 100) : 1;
    const barW     = Math.min(100, Math.max(5, spread * 40));  // visual scale
    const base     = t.pair.split('/')[0];
    const botBadge = t.bot_type === 'dca' ? 'DCA' : 'GRID';
    return `<div class="trip-card">
      <div class="trip-card-top">
        <span class="trip-pair-badge">${t.pair}</span>
        <span class="trip-bot-badge">${botBadge}</span>
      </div>
      <div class="trip-pnl ${pnlCls}">${pnlSign}$${(t.pnl||0).toFixed(6)}${pct}</div>
      <div class="trip-bar-section">
        <div class="trip-bar-track">
          <div class="trip-bar-fill" style="width:${barW}%"></div>
        </div>
        <div class="trip-prices">
          <span class="tp-buy">&#x25BC; BUY $${(t.buy_price||0).toFixed(4)}</span>
          <span class="tp-sell">&#x25B2; SELL $${(t.sell_price||0).toFixed(4)}</span>
        </div>
      </div>
      <div class="trip-meta">
        <span>${t.amount} ${base}</span>
        <span>&middot;</span>
        <span>${t.timestamp.split(' ')[0]}</span>
        <span>${t.timestamp.split(' ')[1]}</span>
      </div>
    </div>`;
  }).join('') + '</div>';
}

// ══════════════════════════════════════════════════════════════════════════════
// GRID CONTEXT PANEL — slides in from the right, full viewport height.
// Uses ACTUAL orders (no calculated levels), same colour scheme.
// Adds a chain section showing the buy→sell sequence + streak for this session.
// ══════════════════════════════════════════════════════════════════════════════

// ── Row builders (unchanged colour scheme) ────────────────────────────────
function _gpOrderRow(price, side, isFocus) {
  const rowCls = isFocus ? 'gp-row gp-row-focus'
               : side === 'sell' ? 'gp-row gp-row-sell' : 'gp-row gp-row-buy';
  const iconCls = isFocus ? 'gp-icon gp-icon-focus'
                : side === 'sell' ? 'gp-icon gp-icon-sell' : 'gp-icon gp-icon-buy';
  const icon  = isFocus ? '&#x25B6;' : '&#x25CF;';
  const lblCls = isFocus ? 'gp-label gp-label-focus'
               : side === 'sell' ? 'gp-label gp-label-sell' : 'gp-label gp-label-buy';
  const label = isFocus
    ? `&#x2190; ${side.toUpperCase()} &mdash; you clicked this`
    : side === 'sell'
      ? 'SELL &mdash; open, waiting to fill at profit'
      : 'BUY &mdash; open, waiting to fill';
  const dp = price < 1 ? 4 : price < 100 ? 4 : 2;
  return `<div class="${rowCls}">
    <span class="gp-price">$${parseFloat(price).toFixed(dp)}</span>
    <span class="${iconCls}">${icon}</span>
    <span class="${lblCls}">${label}</span>
  </div>`;
}

function _gpFillRow(price, side, timestamp, pnl) {
  const isBuy        = side === 'BUY';
  const isTrackedTrip = !isBuy && pnl !== null && pnl !== undefined;
  const isUntrackedSell = !isBuy && !isTrackedTrip;

  // Colour:  BUY→purple ✓ | SELL tracked→yellow ★ | SELL untracked→muted ~
  const rowCls  = isTrackedTrip   ? 'gp-row gp-row-fill-sell'
                : isBuy           ? 'gp-row gp-row-fill-buy'
                : 'gp-row' ; // untracked sell — plain row
  const iconCls = isTrackedTrip   ? 'gp-icon gp-icon-fsell'
                : isBuy           ? 'gp-icon gp-icon-fbuy'
                : 'gp-icon';
  const icon    = isTrackedTrip   ? '&#x2605;'   // ★
                : isBuy           ? '&#x2713;'   // ✓
                : '&#x7E;';                       // ~
  const lblCls  = isTrackedTrip   ? 'gp-label gp-label-fsell'
                : isBuy           ? 'gp-label gp-label-fbuy'
                : 'gp-label';
  const label   = isTrackedTrip
    ? `SELL filled &mdash; round trip! &nbsp;<strong>+$${parseFloat(pnl).toFixed(4)}</strong>`
    : isBuy
      ? `BUY filled &mdash; counter-sell placed above`
      : `<span style="color:var(--muted)">SELL filled &mdash; P&amp;L not tracked (recovery sell)</span>`;

  const time = (timestamp || '').split(' ')[1]?.slice(0,8) || '';
  const dp   = parseFloat(price) < 1 ? 4 : parseFloat(price) < 100 ? 4 : 2;
  return `<div class="${rowCls}" style="${isUntrackedSell ? 'border-left:3px solid rgba(136,146,160,.3);background:rgba(136,146,160,.04)' : ''}">
    <span class="gp-price">$${parseFloat(price).toFixed(dp)}</span>
    <span class="${iconCls}" style="${isUntrackedSell ? 'color:var(--muted)' : ''}">${icon}</span>
    <span class="${lblCls}">${label}</span>
    <span class="gp-time">${time}</span>
  </div>`;
}

// ── Chain builder ─────────────────────────────────────────────────────────
function _buildChain(pairFills) {
  // Sort fills chronologically (oldest first)
  const sorted = [...pairFills].sort((a, b) => a.timestamp.localeCompare(b.timestamp));

  // Match buys to sells in FIFO order — each buy pairs with the next sell
  const pairs = [];
  const pendingBuys = [];

  for (const f of sorted) {
    if (f.side === 'BUY') {
      pendingBuys.push(f);
    } else {
      // SELL — pair with the oldest pending buy
      const buy = pendingBuys.shift() || null;
      pairs.push({ buy, sell: f });
    }
  }
  // Remaining pending buys have no sell yet
  for (const buy of pendingBuys) pairs.push({ buy, sell: null });

  // Longest consecutive streak (pairs where both buy and sell have tracked pnl)
  let maxStreak = 0, cur = 0;
  for (const p of pairs) {
    if (p.buy && p.sell && p.sell.pnl !== null) { cur++; maxStreak = Math.max(maxStreak, cur); }
    else cur = 0;
  }

  const totalPnl = pairs.reduce((s, p) => s + (p.sell?.pnl || 0), 0);
  return { pairs, streak: maxStreak, totalPnl };
}

// ── Chain HTML builder ────────────────────────────────────────────────────
function _chainHtml(pairs) {
  if (pairs.length === 0) return '<div class="gp-empty">No fills yet for this pair in the current session.</div>';

  return [...pairs].reverse().map((p, i) => {  // newest first
    const buyPriceFmt  = p.buy  ? '$' + parseFloat(p.buy.price).toFixed(4)  : '—';
    const sellPriceFmt = p.sell ? '$' + parseFloat(p.sell.price).toFixed(4) : '—';
    const buyTime  = p.buy?.timestamp?.split(' ')[1]?.slice(0,5)  || '';
    const sellTime = p.sell?.timestamp?.split(' ')[1]?.slice(0,5) || '';
    const hasPnl   = p.sell && p.sell.pnl !== null;
    const pct      = hasPnl && p.buy
      ? ((p.sell.pnl / (parseFloat(p.buy.price) * parseFloat(p.buy.amount || 1))) * 100).toFixed(2)
      : null;

    const connText = hasPnl
      ? `<span class="gp-chain-pnl-positive">+$${p.sell.pnl.toFixed(4)}</span>${pct ? `&nbsp;(${pct}%)` : ''}`
      : p.sell
        ? `<span style="color:var(--red)">untracked P&amp;L</span>`
        : `<span style="color:var(--muted);font-style:italic">sell not yet filled</span>`;

    return `<div class="gp-chain-pair">
      <div class="gp-chain-buy">
        <span class="gp-icon gp-icon-buy">&#x25BC;</span>
        <span class="gp-price">${buyPriceFmt}</span>
        <span style="color:var(--green);font-size:11px">BUY</span>
        <span class="gp-time" style="margin-left:auto">${buyTime}</span>
      </div>
      <div class="gp-chain-connector">${connText}</div>
      <div class="gp-chain-sell ${hasPnl ? 'tracked' : p.sell ? 'open' : ''}">
        <span class="gp-icon gp-icon-${hasPnl ? 'fsell' : 'sell'}">&#x25B2;</span>
        <span class="gp-price">${sellPriceFmt}</span>
        <span style="font-size:11px;color:${hasPnl ? 'var(--yellow)' : 'var(--muted)'}">SELL</span>
        <span class="gp-time" style="margin-left:auto">${sellTime}</span>
      </div>
    </div>`;
  }).join('');
}

// ── Main render function ──────────────────────────────────────────────────
function _renderGridPanel(pair, allOrders, pairFills, focusPrice, focusSide, isDemo) {
  const fp = parseFloat(focusPrice);

  // Orders section
  const sells = allOrders.filter(o => o.side === 'sell').sort((a, b) => b.price - a.price);
  const buys  = allOrders.filter(o => o.side === 'buy').sort((a, b) => b.price - a.price);
  let ordersHtml = '';
  sells.forEach(o => {
    ordersHtml += _gpOrderRow(o.price, 'sell', parseFloat(o.price).toFixed(4) === fp.toFixed(4));
  });
  if (sells.length > 0 && buys.length > 0) ordersHtml += `<div class="gp-zone-div">market&nbsp;zone</div>`;
  else if (sells.length > 0)               ordersHtml += `<div class="gp-zone-div">&#x25BC;&nbsp;market&nbsp;below</div>`;
  else if (buys.length > 0)                ordersHtml += `<div class="gp-zone-div">market&nbsp;above&nbsp;&#x25B2;</div>`;
  buys.forEach(o => {
    ordersHtml += _gpOrderRow(o.price, 'buy', parseFloat(o.price).toFixed(4) === fp.toFixed(4));
  });
  // If focus price not in any order (clicked a fill row with no active order)
  if (!allOrders.some(o => parseFloat(o.price).toFixed(4) === fp.toFixed(4))) {
    ordersHtml = _gpOrderRow(fp, focusSide, true) + (ordersHtml ? '<div class="gp-zone-div"></div>' + ordersHtml : '');
  }
  document.getElementById('gp-orders').innerHTML = ordersHtml || '<div class="gp-empty">No open orders for this pair.</div>';

  // Fills section
  const recentFills = pairFills.slice(0, 6);
  const fillsHdr = document.getElementById('gp-fills-hdr');
  const fillsEl  = document.getElementById('gp-fills');
  if (recentFills.length > 0) {
    fillsHdr.style.display = 'block';
    fillsEl.innerHTML = recentFills.map(f => _gpFillRow(f.price, f.side, f.timestamp, f.pnl)).join('');
  } else {
    fillsHdr.style.display = 'none';
    fillsEl.innerHTML = '';
  }

  // Chain section
  const chainHdr = document.getElementById('gp-chain-hdr');
  const chainEl  = document.getElementById('gp-chain');
  const { pairs, streak, totalPnl } = _buildChain(pairFills);
  if (pairs.length > 0) {
    chainHdr.style.display = 'flex';
    const streakEl = document.getElementById('gp-streak-badge');
    if (streak >= 1) {
      const fire = streak >= 4 ? '&#x1F525;' : streak >= 2 ? '&#x2728;' : '&#x2713;';
      streakEl.innerHTML = `${fire} ${streak} round trip${streak > 1 ? 's' : ''} &middot; +$${totalPnl.toFixed(4)}`;
      streakEl.style.display = 'inline-block';
    } else {
      streakEl.style.display = 'none';
    }
    chainEl.innerHTML = _chainHtml(pairs);
  } else {
    chainHdr.style.display = 'none';
    chainEl.innerHTML = '';
  }

  // Header
  document.getElementById('gp-pair-name').textContent = pair + (isDemo ? '' : '');
  document.getElementById('gp-subtitle').textContent  = isDemo
    ? 'Demo — all possible states with mock data'
    : 'Grid Context · Current Session';
  document.getElementById('gp-demo-banner').style.display = isDemo ? 'block' : 'none';
}

// ── Open / close ──────────────────────────────────────────────────────────
function openGridPanel(pair, focusPrice, focusSide, focusLabel, event) {
  event && event.stopPropagation();
  const pairOrders = _activeOrders.filter(o => o.pair === pair);
  const pairFills  = _recentFills.filter(f => f.pair === pair);
  _renderGridPanel(pair, pairOrders, pairFills, focusPrice, focusSide, false);
  document.getElementById('grid-panel').classList.add('open');
  document.getElementById('grid-panel-backdrop').classList.add('open');
  document.body.style.overflow = 'hidden';
}

function showGridDemo() {
  const demoOrders = [
    { side: 'sell', price: 84.85 }, { side: 'sell', price: 83.91 },
    { side: 'sell', price: 83.00 },
    { side: 'buy',  price: 81.00 }, { side: 'buy',  price: 80.19 }, { side: 'buy', price: 79.39 },
  ];
  // Buys happen BEFORE sells — bot buys at the dip, price rises, sell fills later
  const demoFills = [
    { side: 'BUY',  price: 82.44, timestamp: '2026-05-30 10:37:12', pnl: null,   amount: 0.727 },
    { side: 'BUY',  price: 82.67, timestamp: '2026-05-30 10:38:21', pnl: null,   amount: 0.119 },
    { side: 'SELL', price: 83.26, timestamp: '2026-05-30 11:37:42', pnl: 0.5993, amount: 0.727 },
    { side: 'SELL', price: 83.50, timestamp: '2026-05-30 11:38:31', pnl: 0.1012, amount: 0.119 },
  ];
  _renderGridPanel('SOL/USDT', demoOrders, demoFills, 83.00, 'sell', true);
  document.getElementById('grid-panel').classList.add('open');
  document.getElementById('grid-panel-backdrop').classList.add('open');
  document.body.style.overflow = 'hidden';
}

function closeGridPanel() {
  document.getElementById('grid-panel').classList.remove('open');
  document.getElementById('grid-panel-backdrop').classList.remove('open');
  document.body.style.overflow = '';
}

// ── Cancel queue state (track pending cancellations locally) ───────────
const pendingCancels = new Set();

async function cancelOrphan(orderId, pair, side, amount, btn) {
  const isSell = side === 'sell';
  const msg = isSell
    ? `Market sell this ${pair} SELL order?\n\n`
      + `The limit sell will be cancelled and a market sell will fire immediately.\n`
      + `${parseFloat(amount).toFixed(6)} tokens → USDT at current market price.\n\n`
      + `Nothing will be left unattended.`
    : `Cancel this ${pair} BUY order?\n\n`
      + `The order hasn't filled yet — cancelling returns your USDT.\n`
      + `No tokens are held.`;
  if (!confirm(msg)) return;
  btn.disabled = true;
  btn.textContent = '⏳';
  try {
    const r = await fetch('/cancel_orphan', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({order_id: orderId, pair, side, amount: parseFloat(amount)})
    });
    const d = await r.json();
    if (d.queued) {
      pendingCancels.add(orderId);
      btn.textContent = isSell ? '⏳ Selling...' : '⏳ Queued';
      btn.style.color = 'var(--yellow)';
      btn.style.borderColor = 'var(--yellow)';
    } else {
      btn.textContent = '❌ Error';
      btn.disabled = false;
    }
  } catch(e) {
    btn.textContent = '❌ Error';
    btn.disabled = false;
  }
}

async function cancelAllOrphans() {
  const rows = [...document.querySelectorAll('#orphaned-tbody tr')];
  if (!rows.length) return;

  const allBtns = rows.map(r => r.querySelector('[data-orderid]')).filter(Boolean);
  const activeBtns = allBtns.filter(b => !b.disabled);
  if (!activeBtns.length) return;

  const sells = activeBtns.filter(b => b.dataset.side === 'sell');
  const buys  = activeBtns.filter(b => b.dataset.side === 'buy');
  let msg = `Clear all ${activeBtns.length} unmanaged order(s)?\n\n`;
  if (buys.length)  msg += `• ${buys.length} BUY order(s) → cancelled (USDT returned by exchange)\n`;
  if (sells.length) msg += `• ${sells.length} SELL order(s) → cancelled + market sold to USDT immediately\n`;
  if (sells.length) msg += `\nTokens from SELL orders will be converted at current market price.`;
  if (!confirm(msg)) return;

  const cancelAllBtn = document.getElementById('cancel-all-btn');
  cancelAllBtn.disabled = true;
  cancelAllBtn.textContent = '⏳ Queuing...';

  for (const btn of activeBtns) {
    const oid    = btn.dataset.orderid;
    const pair   = btn.dataset.pair;
    const side   = btn.dataset.side;
    const amount = parseFloat(btn.dataset.amount || '0');
    try {
      await fetch('/cancel_orphan', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({order_id: oid, pair, side, amount})
      });
      pendingCancels.add(oid);
      btn.disabled = true;
      btn.textContent = side === 'sell' ? '⏳ Selling...' : '⏳ Queued';
      btn.style.color = 'var(--yellow)';
      btn.style.borderColor = 'var(--yellow)';
    } catch(e) { /* continue with remaining orders */ }
  }
  cancelAllBtn.textContent = '✅ All Queued';
}

// ── Shutdown modal ──────────────────────────────────────────────────────
let _sellPositions = false;

function openShutdownModal() {
  document.getElementById('shutdown-modal').classList.add('open');
  document.getElementById('shutdown-input').value = '';
  document.getElementById('shutdown-submit').disabled = true;
  document.getElementById('shutdown-submit').classList.remove('ready');
  // Reset to default option
  document.querySelectorAll('input[name="sell-mode"]')[0].checked = true;
  _sellPositions = false;
}

function closeShutdownModal() {
  document.getElementById('shutdown-modal').classList.remove('open');
}

function updateShutdownMode(radio) {
  _sellPositions = radio.value === 'sell';
  const btn = document.getElementById('shutdown-submit');
  btn.textContent = _sellPositions ? 'Shutdown + Sell All' : 'Shutdown Bot';
  // Re-check input validity
  onShutdownInput(document.getElementById('shutdown-input'));
}

function onShutdownInput(input) {
  const ready = input.value === 'SHUTDOWN';
  const btn = document.getElementById('shutdown-submit');
  btn.disabled = !ready;
  ready ? btn.classList.add('ready') : btn.classList.remove('ready');
}

async function executeShutdown() {
  const btn = document.getElementById('shutdown-submit');
  if (document.getElementById('shutdown-input').value !== 'SHUTDOWN') return;
  btn.disabled = true;
  btn.textContent = '⏳ Shutting down...';

  try {
    const r = await fetch('/shutdown', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({confirm: 'SHUTDOWN', sell_positions: _sellPositions}),
    });
    const d = await r.json();
    if (d.initiated) {
      closeShutdownModal();
      // Replace dashboard with a shutdown banner
      document.body.innerHTML = `
        <div style="min-height:100vh;display:flex;align-items:center;justify-content:center;background:#0f1117;">
          <div style="text-align:center;padding:40px;">
            <div style="font-size:48px;margin-bottom:16px;">⛔</div>
            <h1 style="color:#ef4444;font-size:24px;margin-bottom:12px;">Bot Shutdown Initiated</h1>
            <p style="color:#8892a0;font-size:14px;max-width:400px;line-height:1.7;">
              All orders are being cancelled.
              ${_sellPositions ? 'Positions are being market-sold to USDT.' : 'Positions held — review on exchange.'}<br><br>
              Check Discord for confirmation.<br>
              To restart: run <code style="background:#1a1d27;padding:2px 6px;border-radius:4px;">python -m src.resume --confirm</code>
            </p>
          </div>
        </div>`;
    } else {
      btn.textContent = '❌ Error — Retry';
      btn.disabled = false;
      alert('Shutdown failed: ' + (d.error || 'unknown error'));
    }
  } catch(e) {
    btn.textContent = '❌ Network Error';
    btn.disabled = false;
    alert('Could not reach bot: ' + e);
  }
}

// Close modal on background click
document.getElementById('shutdown-modal').addEventListener('click', function(e) {
  if (e.target === this) closeShutdownModal();
});

// ── DMS confirm button ──────────────────────────────────────────────────
async function confirmAlive() {
  const btn = document.getElementById('confirm-btn');
  const toast = document.getElementById('confirm-toast');
  btn.disabled = true;
  btn.className = 'loading';
  btn.textContent = '⏳ Confirming...';
  toast.textContent = '';
  try {
    const r = await fetch('/confirm', { method: 'POST' });
    const d = await r.json();
    if (d.confirmed) {
      btn.className = 'success';
      btn.textContent = '✅ Confirmed!';
      toast.style.color = '#10b981';
      toast.textContent = "Dead man\\'s switch reset — bot will keep running.";
      setTimeout(() => {
        btn.textContent = "❤️ Confirm I\\'m Alive";
        btn.className = '';
        btn.disabled = false;
        toast.textContent = '';
        refresh();
      }, 3000);
    } else {
      throw new Error(d.error || 'Unknown error');
    }
  } catch(e) {
    btn.className = 'error';
    btn.textContent = '❌ Failed — Retry';
    toast.style.color = '#ef4444';
    toast.textContent = String(e);
    btn.disabled = false;
  }
}

// ── Status badges ───────────────────────────────────────────────────────
async function fetchStatus() {
  try {
    const r = await fetch('/health');
    const d = await r.json();
    const badges = document.getElementById('status-badges');
    badges.innerHTML = '';

    const env = d.env || 'unknown';
    badges.innerHTML += `<span class="badge badge-${env === 'live' ? 'green' : 'yellow'}">${env.toUpperCase()}</span>`;

    const cb = d.circuit_breaker;
    if (cb) {
      if (cb.tripped) {
        // Real circuit-breaker trip — drawdown / velocity / API errors
        badges.innerHTML += `<span class="badge badge-red">&#x26A0; CB Tripped: ${cb.reason || ''}</span>`;
      } else if (cb.manual_lock) {
        // User pressed Shutdown — expected state, not an alarm
        badges.innerHTML += `<span class="badge badge-yellow">&#x23F8; System Locked &mdash; run <code>resume --confirm</code> to restart</span>`;
      } else {
        badges.innerHTML += `<span class="badge badge-green">&#x2714; Circuit Breaker OK</span>`;
      }
    }

    const dms = d.dead_mans_switch;
    if (dms) {
      if (dms.alive) {
        const h = dms.hours_until_halt?.toFixed(1) ?? '?';
        const cls = parseFloat(h) < 6 ? 'badge-red' : parseFloat(h) < 12 ? 'badge-yellow' : 'badge-green';
        badges.innerHTML += `<span class="badge ${cls}">&#x2764; DMS OK &mdash; ${h}h left</span>`;
      } else {
        badges.innerHTML += `<span class="badge badge-red">&#x26A0; DMS HALTED</span>`;
      }
    }
  } catch(e) {
    document.getElementById('status-badges').innerHTML = '<span class="badge badge-red">&#x26A0; Cannot reach bot</span>';
  }
}

// ── Trade data ──────────────────────────────────────────────────────────
function ageString(isoStr) {
  // Avoid double-Z: timestamps already stored with trailing Z
  const normalized = isoStr.endsWith('Z') ? isoStr : isoStr + 'Z';
  const diff = (Date.now() - new Date(normalized).getTime()) / 1000;
  if (!isFinite(diff)) return '?';
  if (diff < 60) return Math.round(diff) + 's ago';
  if (diff < 3600) return Math.round(diff/60) + 'm ago';
  return Math.round(diff/3600) + 'h ago';
}

async function fetchTrades() {
  try {
    const r = await fetch('/api/trades');
    const d = await r.json();

    // ── Free USDT card — always update, even with zero trades ──────────────
    // balance.json is written by the bot's CB check every ~30s independently
    // of trade history, so this card works from the very first bot startup.
    const bal = d.balance || {};
    const freeUsdt = bal.usdt_free;
    const totalUsdt = bal.usdt_total;
    const freeEl = document.getElementById('s-usdt-free');
    if (freeUsdt !== undefined && freeUsdt !== null) {
      freeEl.textContent = '$' + freeUsdt.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
      freeEl.className = 'stat-value green';
      const totalStr = totalUsdt !== undefined ? ` of $${totalUsdt.toFixed(2)} total` : '';
      const age = bal.updated_at ? ' · ' + ageString(bal.updated_at) : '';
      document.getElementById('s-usdt-free-sub').textContent = totalStr + age;
    } else {
      freeEl.textContent = '—';
      freeEl.className = 'stat-value';
      document.getElementById('s-usdt-free-sub').textContent = 'waiting for first balance check (~30s)';
    }

    // Trade cards only populate once the DB has fills
    if (!d.available) {
      document.getElementById('generated-at').textContent = 'No trade data yet — fills appear once orders execute';
      return;
    }

    document.getElementById('generated-at').textContent = 'Last updated: ' + d.generated_at;

    // Store fresh data for grid popup (used when user clicks an order/fill)
    _spacingPct   = d.grid_spacing_pct ?? 1.0;
    _activeOrders = d.active_orders || [];
    _recentFills  = d.recent || [];

    // Stat cards
    const s = d.summary;

    // In Buy Orders — real locked USDT from exchange balance snapshot
    const lockedEl = document.getElementById('s-capital');
    const usedUsdt = bal.usdt_used;
    if (usedUsdt !== undefined && usedUsdt !== null) {
      lockedEl.textContent = '$' + usedUsdt.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
      lockedEl.className = usedUsdt > 0 ? 'stat-value' : 'stat-value';
    } else {
      lockedEl.textContent = '—';
    }

    // Gross P&L (all-time + today sub + untracked note)
    const pnl = s.approx_gross_pnl_usd;
    const pnlEl = document.getElementById('s-pnl');
    pnlEl.textContent = (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(4);
    pnlEl.className = 'stat-value ' + (pnl >= 0 ? 'green' : 'red');
    const todayPnl = s.today_pnl_usd;
    const todaySign = todayPnl >= 0 ? '+' : '';
    const untrackedNote = s.untracked_sells > 0 ? ` · ${s.untracked_sells} estimated` : ' · all tracked';
    document.getElementById('s-pnl-sub').textContent =
      `today: ${todaySign}$${todayPnl.toFixed(4)}${untrackedNote}`;

    // Win Rate
    const wrEl = document.getElementById('s-winrate');
    if (s.win_rate !== null && s.win_rate !== undefined) {
      wrEl.textContent = s.win_rate.toFixed(1) + '%';
      wrEl.className = 'stat-value ' + (s.win_rate >= 50 ? 'green' : 'red');
      document.getElementById('s-winrate-sub').textContent =
        s.profitable_sells + ' of ' + s.tracked_sells + ' tracked sells';
    } else {
      wrEl.textContent = '—';
      wrEl.className = 'stat-value';
      document.getElementById('s-winrate-sub').textContent = 'no tracked sells yet';
    }

    // Round Trips
    document.getElementById('s-rt').textContent = s.total_round_trips;
    document.getElementById('s-rt-sub').textContent = 'completed buy→sell cycles';

    // Total Fills
    document.getElementById('s-fills').textContent = s.total_fills;
    document.getElementById('s-fills-sub').textContent = s.today_fills + ' today';

    // Per-pair table
    const pairs = d.pairs;
    const tbody = document.getElementById('pair-tbody');
    tbody.innerHTML = '';
    const sortedPairs = Object.entries(pairs).sort((a,b) => b[1].round_trips - a[1].round_trips);
    for (const [pair, p] of sortedPairs) {
      tbody.innerHTML += `<tr>
        <td><strong>${pair}</strong></td>
        <td class="buy">${p.buys}</td>
        <td class="sell">${p.sells}</td>
        <td>${p.round_trips}</td>
        <td class="${p.approx_pnl > 0 ? 'green' : p.approx_pnl < 0 ? 'red' : ''}">${p.sells > 0 ? (p.approx_pnl >= 0 ? '+' : '') + '$' + p.approx_pnl.toFixed(4) : '—'}</td>
      </tr>`;
    }

    // ── Active open orders ────────────────────────────────────────────────
    const activeOrders = (d.active_orders || []).sort((a,b) =>
      a.pair.localeCompare(b.pair) || a.price - b.price
    );
    const activeCard = document.getElementById('active-orders-card');
    const activeTbody = document.getElementById('active-tbody');
    if (activeOrders.length > 0) {
      activeCard.style.display = 'block';
      const totalBuyValue  = activeOrders.filter(o=>o.side==='buy').reduce((s,o)=>s+o.value_usd,0);
      const totalSellValue = activeOrders.filter(o=>o.side==='sell').reduce((s,o)=>s+o.value_usd,0);
      const buyCnt  = activeOrders.filter(o=>o.side==='buy').length;
      const sellCnt = activeOrders.filter(o=>o.side==='sell').length;
      document.getElementById('active-orders-meta').textContent =
        `${activeOrders.length} orders · ${buyCnt} buys · ${sellCnt} sells`;

      // Format amount: cap to 4 decimal places for clean display
      function fmtAmt(amount) {
        const n = parseFloat(amount);
        if (n >= 100) return n.toFixed(2);
        if (n >= 1)   return n.toFixed(4);
        return n.toFixed(6);
      }

      activeTbody.innerHTML = activeOrders.map(o => `<tr style="cursor:pointer" title="Click to see grid context"
          onclick="openGridPanel('${o.pair}',${o.price},'${o.side}','open order',event)"
          onmouseenter="this.style.background='rgba(59,130,246,.06)'" onmouseleave="this.style.background=''">
        <td><strong>${o.pair}</strong></td>
        <td class="${o.side==='buy'?'buy':'sell'}">${o.side.toUpperCase()}</td>
        <td>$${parseFloat(o.price).toFixed(4)}</td>
        <td>${fmtAmt(o.amount)}</td>
        <td>$${parseFloat(o.value_usd).toFixed(2)}</td>
      </tr>`).join('');

      // Total row — buy total should match "In Buy Orders" card (usdt_used).
      // Tolerance: larger of $5 or 3% of total to absorb fee-rounding drift
      // without triggering a persistent false warning.
      const usedUsdt = (typeof bal !== 'undefined' && bal.usdt_used !== undefined) ? bal.usdt_used : null;
      const diff = usedUsdt !== null ? Math.abs(totalBuyValue - usedUsdt) : null;
      const tolerance = usedUsdt !== null ? Math.max(5, usedUsdt * 0.03) : 5;
      const matchBadge = diff === null ? ''
        : diff <= tolerance
          ? `<span class="match-badge match-ok">✓ matches In Buy Orders</span>`
          : `<span class="match-badge match-warn">⚠ differs from In Buy Orders by $${diff.toFixed(2)}</span>`;

      const tfoot = document.getElementById('active-tfoot');
      tfoot.innerHTML = `
        <tr>
          <td colspan="4" style="color:var(--muted)">
            Buys locked${matchBadge}
          </td>
          <td class="buy">$${totalBuyValue.toFixed(2)}</td>
        </tr>`
        + (totalSellValue > 0 ? `<tr>
          <td colspan="4" style="color:var(--muted)">Sells pending (waiting to fill)</td>
          <td style="color:var(--muted)">$${totalSellValue.toFixed(2)}</td>
        </tr>` : '');
    } else {
      activeCard.style.display = 'none';
      document.getElementById('active-tfoot').innerHTML = '';
    }

    // Chart — isolated so a CDN failure doesn't block the tables below
    try {
      const labels = sortedPairs.map(([p]) => p.replace('/USDT',''));
      const buyData  = sortedPairs.map(([,p]) => p.buys);
      const sellData = sortedPairs.map(([,p]) => p.sells);
      if (fillsChart) { fillsChart.destroy(); fillsChart = null; }
      if (typeof Chart !== 'undefined') {
        fillsChart = new Chart(document.getElementById('chart-fills'), {
          type: 'bar',
          data: {
            labels,
            datasets: [
              { label:'Buys',  data:buyData,  backgroundColor:'rgba(16,185,129,.7)', borderRadius:4 },
              { label:'Sells', data:sellData, backgroundColor:'rgba(239,68,68,.7)',   borderRadius:4 },
            ]
          },
          options: {
            responsive:true,
            plugins:{ legend:{ labels:{ color:'#8892a0', font:{ size:11 } } } },
            scales:{
              x:{ ticks:{ color:'#8892a0' }, grid:{ color:'#2a2d3a' } },
              y:{ ticks:{ color:'#8892a0' }, grid:{ color:'#2a2d3a' } }
            }
          }
        });
      } else {
        document.getElementById('chart-fills').insertAdjacentHTML('beforebegin',
          '<p style="color:var(--muted);font-size:12px">Chart unavailable (CDN blocked)</p>');
      }
    } catch(chartErr) {
      console.warn('Chart render failed:', chartErr);
    }

    // ── Orphaned / unmanaged orders ──────────────────────────────────────
    const orphaned = d.reconciled || [];
    const orphanedSection = document.getElementById('orphaned-section');
    const orphanedTbody = document.getElementById('orphaned-tbody');
    const cancelAllBtn = document.getElementById('cancel-all-btn');
    orphanedTbody.innerHTML = '';
    if (orphaned.length > 0) {
      orphanedSection.style.display = 'block';
      cancelAllBtn.disabled = false;
      cancelAllBtn.textContent = `Cancel All (${orphaned.length})`;
      // Build all rows in one pass, then set innerHTML once (O(n) not O(n²))
      const rowsHtml = orphaned.map(o => {
        const isPending = pendingCancels.has(o.id);
        const isSell    = o.side === 'sell';
        let actionCell;
        if (isPending) {
          actionCell = `<span class="pending-tag">&#x23F3; ${isSell ? 'Selling...' : 'Pending'}</span>`;
        } else if (isSell) {
          actionCell = `<button class="btn-market-sell" data-orderid="${o.id}" data-pair="${o.pair}" data-side="sell" data-amount="${o.amount}"
               onclick="cancelOrphan('${o.id}','${o.pair}','sell',${o.amount},this)">Market Sell</button>`;
        } else {
          actionCell = `<button class="btn-cancel-one" data-orderid="${o.id}" data-pair="${o.pair}" data-side="buy" data-amount="${o.amount}"
               onclick="cancelOrphan('${o.id}','${o.pair}','buy',${o.amount},this)">Cancel</button>`;
        }
        return `<tr class="orphaned-row">
          <td><strong>${o.pair}</strong></td>
          <td class="${isSell ? 'sell' : 'buy'}">${o.side.toUpperCase()}</td>
          <td>$${parseFloat(o.price).toFixed(4)}</td>
          <td>${parseFloat(o.amount).toFixed(6)}</td>
          <td style="color:var(--muted);font-size:12px">${ageString(o.reconciled_at)}</td>
          <td>${actionCell}</td>
        </tr>`;
      }).join('');
      orphanedTbody.innerHTML = rowsHtml;
    } else {
      orphanedSection.style.display = 'none';
    }

    // ── Recent fills (with P&L column + untracked highlight) ─────────────
    const ftbody = document.getElementById('fills-tbody');
    ftbody.innerHTML = '';
    for (const f of d.recent) {
      const val = f.price && f.amount ? '$' + (f.price * f.amount).toFixed(2) : '—';

      // Determine P&L display and whether this is an untracked sell
      const isSell = f.side === 'SELL';
      const isUntracked = isSell && f.pnl === null;
      let pnlCell = '—';
      if (f.pnl !== null && f.pnl !== undefined) {
        const sign = f.pnl >= 0 ? '+' : '';
        const cls  = f.pnl >= 0 ? 'green' : 'red';
        pnlCell = `<span class="${cls}">${sign}$${parseFloat(f.pnl).toFixed(6)}</span>`;
      } else if (isUntracked) {
        pnlCell = `<span class="orange">? <span class="untracked-tag">UNTRACKED</span></span>`;
      }

      const rowClass = isUntracked ? 'untracked-sell' : '';
      const fillSide = f.side === 'BUY' ? 'buy' : 'sell';
      ftbody.innerHTML += `<tr class="${rowClass}" style="cursor:pointer" title="Click to see grid context"
          onclick="openGridPanel('${f.pair}',${f.price},'${fillSide}','filled ${f.timestamp?.split(' ')[1]||''}',event)"
          onmouseenter="this.style.background='rgba(59,130,246,.06)'" onmouseleave="this.style.background=''">
        <td style="color:#8892a0;font-size:12px">${f.timestamp}</td>
        <td><strong>${f.pair}</strong></td>
        <td class="${fillSide}">${f.side}</td>
        <td>$${f.price?.toFixed ? f.price.toFixed(4) : f.price}</td>
        <td>${f.amount ?? '—'}</td>
        <td>${val}</td>
        <td>${pnlCell}</td>
      </tr>`;
    }
  } catch(e) {
    console.error(e);
  }
}

async function refresh() {
  await Promise.all([fetchStatus(), fetchTrades()]);
}

// Countdown
setInterval(() => {
  countdown -= 1;
  document.getElementById('prog').value = countdown;
  if (countdown <= 0) { countdown = 60; refresh(); }
}, 1000);

refresh();
</script>
</body>
</html>"""


def _get_status() -> dict:
    """Build a status dict from local state files."""
    status = {
        "ok": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "env": os.environ.get("ENV", "unknown"),
        "circuit_breaker": {"tripped": False, "reason": None},
        "dead_mans_switch": {"alive": True, "hours_until_halt": None},
        "strategy_matrix": {"loaded": False, "pairs": 0},
    }

    # Circuit breaker / system lock
    # Distinguish between an intentional manual shutdown (expected state, user
    # pressed the button) and a real CB trip (drawdown / velocity / API errors).
    # Both write system_state.json with locked=true, but the reason differs.
    state_file = "logs/system_state.json"
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                state = json.load(f)
            locked = state.get("locked", False)
            reason = state.get("reason", "")
            manual = locked and ("manual" in reason.lower() or "dashboard" in reason.lower())
            cb_tripped = locked and not manual
            status["circuit_breaker"] = {
                "tripped":      cb_tripped,   # real CB trip — show red alarm
                "manual_lock":  manual,        # user-initiated — show neutral badge
                "reason":       reason if locked else None,
                "tripped_at":   state.get("tripped_at") if locked else None,
            }
            if locked:
                status["ok"] = False
        except Exception:
            pass

    # Dead man's switch
    heartbeat_file = "logs/heartbeat.json"
    if os.path.exists(heartbeat_file):
        try:
            with open(heartbeat_file) as f:
                hb = json.load(f)
            from src.dead_mans_switch import DeadMansSwitch
            dms = DeadMansSwitch()
            ds = dms.status()
            status["dead_mans_switch"] = {
                "alive": ds["alive"],
                "hours_since_confirm": round(ds["hours_since"], 1),
                "hours_until_halt": round(ds["hours_until_halt"], 1),
                "last_confirmed": ds["last_confirmed"],
            }
            if not ds["alive"]:
                status["ok"] = False
        except Exception:
            pass

    # Strategy matrix
    matrix_file = "logs/profitability_matrix.json"
    if os.path.exists(matrix_file):
        try:
            with open(matrix_file) as f:
                matrix = json.load(f)
            status["strategy_matrix"] = {
                "loaded": True,
                "pairs": len(matrix.get("pairs", {})),
                "generated_at": matrix.get("generated_at", "?")[:10],
            }
        except Exception:
            pass

    return status


class _Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        # Suppress default HTTP request logs (noisy in prod)
        pass

    def _send_json(self, code: int, data: dict):
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, code: int, html: str):
        body = html.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/health", "/"):
            status = _get_status()
            code = 200 if status["ok"] else 503
            self._send_json(code, status)

        elif self.path == "/status":
            self._send_json(200, _get_status())

        elif self.path == "/dashboard":
            self._send_html(200, _DASHBOARD_HTML)

        elif self.path == "/api/trades":
            self._send_json(200, _get_trade_stats())

        elif self.path == "/api/round_trips":
            self._send_json(200, _get_round_trips())

        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/confirm":
            try:
                from src.dead_mans_switch import DeadMansSwitch
                dms = DeadMansSwitch()
                dms.confirm()
                logger.info("Dead man's switch confirmed via HTTP /confirm")
                self._send_json(200, {
                    "confirmed": True,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "message": "Dead man's switch reset. Bot will keep running.",
                })
            except Exception as e:
                logger.error(f"Health server /confirm error: {e}")
                self._send_json(500, {"error": str(e)})

        elif self.path == "/shutdown":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
            except Exception:
                body = {}

            if body.get("confirm") != "SHUTDOWN":
                self._send_json(400, {
                    "error": "Confirmation required — send {\"confirm\": \"SHUTDOWN\"}",
                })
                return

            if _shutdown_callback is None:
                self._send_json(503, {
                    "error": "Shutdown callback not registered — bot may still be starting up.",
                })
                return

            sell_positions = bool(body.get("sell_positions", False))
            logger.warning(
                f"Dashboard /shutdown received (sell_positions={sell_positions})"
            )
            # Run callback in a daemon thread so the HTTP response returns immediately
            threading.Thread(
                target=_shutdown_callback,
                args=(sell_positions,),
                daemon=True,
                name="dashboard-shutdown",
            ).start()

            self._send_json(200, {
                "initiated":     True,
                "sell_positions": sell_positions,
                "message": (
                    "Shutdown initiated. All orders will be cancelled"
                    + (" and positions market-sold to USDT." if sell_positions else ". Positions held.")
                ),
            })

        elif self.path == "/cancel_orphan":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
                order_id = body.get("order_id", "").strip()
                pair     = body.get("pair", "").strip()
                side     = body.get("side", "buy").strip().lower()   # "buy" or "sell"
                amount   = float(body.get("amount", 0.0))
                if not order_id or not pair:
                    self._send_json(400, {"error": "order_id and pair required"})
                    return

                # SELL orders: cancel the limit order then immediately market-sell the tokens.
                # BUY orders: cancel only — USDT is returned by the exchange automatically.
                market_sell = (side == "sell")

                queue_file = "logs/cancel_queue.json"
                queue: list = []
                if os.path.exists(queue_file):
                    try:
                        with open(queue_file) as f:
                            queue = json.load(f)
                    except Exception:
                        queue = []

                # Idempotent — don't add duplicates
                if not any(q["order_id"] == order_id for q in queue):
                    queue.append({
                        "order_id":     order_id,
                        "pair":         pair,
                        "side":         side,
                        "amount":       amount,
                        "market_sell":  market_sell,
                        "requested_at": datetime.now(timezone.utc).isoformat(),
                    })
                    os.makedirs("logs", exist_ok=True)
                    with open(queue_file, "w") as f:
                        json.dump(queue, f, indent=2)

                action = "market-sell queued" if market_sell else "cancel queued"
                logger.info(f"{pair} {side} order {order_id} — {action}")
                self._send_json(200, {
                    "queued":      True,
                    "order_id":    order_id,
                    "pair":        pair,
                    "side":        side,
                    "market_sell": market_sell,
                    "note": (
                        "Limit SELL will be cancelled and tokens market-sold within 30s."
                        if market_sell else
                        "BUY order will be cancelled within 30s. USDT returned by exchange."
                    ),
                })
            except Exception as e:
                logger.error(f"/cancel_orphan error: {e}")
                self._send_json(500, {"error": str(e)})

        else:
            self._send_json(404, {"error": "not found"})


def start_health_server():
    """Start the health HTTP server in a background daemon thread."""
    server = HTTPServer(("0.0.0.0", PORT), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Health server listening on port {PORT} — GET /health | POST /confirm | GET /dashboard")
