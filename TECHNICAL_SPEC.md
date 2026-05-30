# 01 — Trading Bot: Technical Specification

**Last Updated:** 2026-05-30  
**Status:** Feature-complete on testnet. Railway deploy pending.

---

## Overview

Adaptive multi-pair grid/DCA trading bot. Detects market regime every 4 hours, selects the historically best strategy per pair, and runs independent worker threads. Three layered safety systems stop trading when risk thresholds are crossed. Full HTTP interface for health checks, daily confirmation, live trader dashboard, and emergency controls.

---

## Tech Stack

| Component | Tool | Why |
|-----------|------|-----|
| Exchange API | `ccxt` | Unified API for 100+ exchanges |
| Data / backtesting | `pandas`, `numpy`, `yfinance` | OHLCV fetch, regime analysis |
| Notifications | `requests` (Discord webhooks) | No library needed, rich embeds |
| Config | `python-dotenv` | Secure API key loading from `.env` |
| Logging | `loguru` | Structured log files with rotation |
| Storage | `sqlite3` (stdlib) | Trade log, P&L history |
| HTTP server | `http.server` (stdlib) | Health check + dashboard (no Flask) |
| Testing | `pytest` | Unit tests for core logic |

---

## Project Structure

```
01-trading-bot/
├── Architecture.md              ← Progress tracker (phases + issues log)
├── TECHNICAL_SPEC.md            ← This file
├── DEPLOY.md                    ← Step-by-step Railway deployment guide
├── requirements.txt
├── .env.example
├── .env                         ← NEVER commit this
├── .gitignore
├── railway.toml                 ← Railway build + start command
├── .python-version              ← Pins Python 3.10 for Nixpacks
│
├── main.py                      ← Entry point (adaptive / grid / dca / backtest / status)
│
├── src/
│   ├── __init__.py
│   ├── config.py                ← Load + validate all env vars; fail fast if missing
│   ├── exchange.py              ← CCXT factory (clock drift fix, rate limit, recvWindow)
│   ├── notifier.py              ← Discord webhooks; rate limiter (1.2s + retry-after); retry_after in seconds
│   ├── database.py              ← SQLite: log_trade(), get_daily_summary_by_pair()
│   ├── adaptive_bot.py          ← Orchestrator: CB pre-check before workers, 1 thread/pair,
│   │                               regime checks, daily summary, SIGTERM, shutdown, balance snapshot
│   ├── grid_bot.py              ← Grid strategy: reconcile, buy-only mode, recovery sells (both paths),
│   │                               cancel queue (thread-safe lock), P&L, active order snapshot
│   ├── dca_bot.py               ← DCA strategy: base order, safety orders, take profit
│   ├── market_classifier.py     ← 5-regime detector (ADX/ATR/SMA200/BB, 5-day filter)
│   ├── strategy_selector.py     ← Reads profitability matrix, returns grid/dca/sit_out
│   ├── circuit_breaker.py       ← 3 trip wires → lock system → Discord alert
│   ├── dead_mans_switch.py      ← 30h heartbeat; auto-halt on silence
│   ├── confirm.py               ← `python -m src.confirm` resets DMS timer
│   ├── resume.py                ← `python -m src.resume --confirm` unlocks CB
│   ├── health_server.py         ← HTTP daemon: all endpoints + full dashboard HTML/JS/CSS
│   └── test_connection.py       ← Quick sanity check (price + balance)
│
├── backtests/
│   ├── __init__.py
│   ├── fetch_history.py         ← 15 months OHLCV via yfinance for 9 pairs
│   ├── backtest_grid.py         ← Month-by-month grid simulation
│   ├── backtest_dca.py          ← Month-by-month DCA simulation
│   └── backtest_regime.py       ← Full regime analysis → profitability_matrix.json
│
├── deploy/
│   └── adaptive_bot.service     ← systemd unit for VPS live deployment
│
└── logs/                        ← Auto-created at runtime (gitignored except .gitkeep)
    ├── trades.db                ← SQLite trade history (pnl per sell, used for dashboard)
    ├── profitability_matrix.json ← Strategy approval matrix (16 approved across 9 pairs)
    ├── reconciled_orders.json   ← Orphaned orders panel — replaced per-pair on each reconcile, cleared on shutdown
    ├── cancel_queue.json        ← Dashboard cancel/market-sell requests; thread-safe lock in grid_bot
    ├── balance.json             ← USDT free/used/total snapshot; written every ~30s by adaptive_bot
    ├── active_SOLUSDT.json      ← Per-pair active open orders snapshot (one file per pair)
    ├── active_BNBUSDT.json      ← (etc. for each active pair)
    ├── system_state.json        ← Circuit breaker state (locked/reason/tripped_at/source)
    ├── heartbeat.json           ← DMS last-confirmed timestamp
    ├── adaptive_bot.log
    └── grid_bot.log
```

---

## Key Design Decisions

**Capital-agnostic:** Every dollar amount in `.env`. Bots refuse to start if capital vars missing.

**CB pre-check on startup:** `adaptive_bot.run()` checks `cb.is_tripped()` BEFORE calling `_classify_all()`. If locked from a previous session, no workers start. This prevents grid bots running unmonitored (no drawdown/DMS/velocity checks) when the system was locked.

**Startup reconcile:** `_reconcile_or_init()` fetches existing open orders from the exchange before placing a new grid. Prevents 2× deployed capital on Railway redeployments where SIGTERM doesn't complete before SIGKILL.

**Buy-only grid on fresh start:** `place_initial_orders()` checks free base-asset balance. If less than one standard order quantity, initial sell levels are skipped entirely — they would fail anyway. Buys are placed normally. Counter-sells are created by `check_fills_and_reorder` as each buy fills.

**`_place_uncovered_sells` on both paths:** Called after both fresh grid AND reconcile startup. Catches base asset left free by fills during downtime. Creates a single correctly-sized recovery sell rather than failing multiple standard-size sells.

**MAKER_FEE_RATE deduction on counter-sells:** Binance deducts 0.1% fee from the received base asset on a BUY fill. Counter-sell quantity = `amount × (1 - 0.001)` to match actual received balance.

**Cancel queue — thread-safe:** `_CANCEL_QUEUE_LOCK = threading.Lock()` at module level, shared across all GridBot instances. The full read→process→write cycle is inside the lock. Prevents race condition where two pair threads interleave writes and unprocessed items reappear.

**Reconciled orders — replace not append:** `_write_reconciled()` removes all existing entries for this pair before writing fresh ones. `_clear_reconciled_for_pair()` on fresh grid start. File wiped entirely on any shutdown. Eliminates stale entries from previous sessions appearing as phantom orphans.

**Balance snapshot:** `_fetch_and_snapshot_balance()` writes `logs/balance.json` (USDT free/used/total + timestamp). Called from both the normal CB-check path AND the CB-tripped sleep branch, so the dashboard Free USDT card updates even when the system is locked.

**Active orders snapshot:** Each grid bot writes `logs/active_{PAIR}.json` every 30s (after fill check). Health server aggregates all per-pair files. Dashboard shows live table with total row + match indicator vs `usdt_used`.

**CB badge distinction:** Health server reads `reason` field in `system_state.json`. "manual"/"dashboard" = intentional user action → yellow `⏸ System Locked` badge. Other reasons = real CB trip → red `⚠ CB Tripped` badge.

**Orphan SELL handling:** Dashboard shows "Market Sell" (orange) for orphaned SELL orders, "Cancel" (red) for BUY orders. Cancel queue item includes `market_sell: true` and `amount`. Bot checks actual free balance before market sell — if tokens not present, cleans up silently. If sell fails, order stays on dashboard for retry.

**Shutdown race condition fix:** `_shutdown = True` set as FIRST line of `_initiate_shutdown()`. Main loop watchdog cannot restart workers mid-shutdown because flag is set before any `worker.stop()` calls.

**Discord rate limiter:** `threading.Lock` + 1.2s minimum interval + `retry_after` from 429 response (Discord returns seconds, not milliseconds — no `/1000` division).

**Centralized daily summary:** Single consolidated Discord message per UTC day from `adaptive_bot.py`, not 5 separate per-pair messages.

**Testnet first:** `ENV=testnet` always. Switch to `ENV=live` only after 48h+ stable testnet run.

---

## HTTP API Reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Railway health check — 200 if OK, 503 if CB tripped |
| GET | `/status` | Full JSON: CB state, DMS state, strategy matrix |
| GET | `/dashboard` | Trader dashboard HTML (dark, mobile-responsive, auto-refresh 60s) |
| GET | `/api/trades` | JSON: summary + per-pair fills + active orders + recent 30 fills + orphaned orders + balance |
| POST | `/confirm` | Reset dead man's switch timer |
| POST | `/cancel_orphan` | Queue an order for safe cancellation (`side`, `amount`, `market_sell` fields) |
| POST | `/shutdown` | Emergency stop (`{"confirm":"SHUTDOWN","sell_positions":bool}`) |

---

## Dashboard Panels

| Panel | Source | Updates |
|-------|--------|---------|
| Free USDT card | `logs/balance.json → usdt_free` | ~30s |
| In Buy Orders card | `logs/balance.json → usdt_used` | ~30s |
| Gross P&L card | `trades.db pnl` column (estimate fallback for untracked) | 60s |
| Win Rate card | `trades.db` tracked sells with pnl > 0 | 60s |
| Round Trips card | `trades.db` min(buys, sells) per pair | 60s |
| Total Fills card | `trades.db` row count | 60s |
| Active Open Orders | `logs/active_PAIR.json` per pair | 30s |
| Per-Pair Fill History | `trades.db` cumulative since bot started | 60s |
| Recent Fills (30) | `trades.db` newest rows | 60s |
| Orphaned Orders | `logs/reconciled_orders.json` | 60s |
| Status badges | `/health` endpoint → `system_state.json`, `heartbeat.json` | 30s |

---

## Safety Layers (3 independent)

| Layer | Trigger | Action |
|-------|---------|--------|
| Per-bot stop loss | Price drops 8% from grid entry | Cancel all orders for that pair |
| Circuit breaker | 15% portfolio drawdown OR 8%/candle velocity OR 5 API errors | Lock system, cancel all, Discord alert |
| Dead man's switch | 30h without POST /confirm | Halt all bots, Discord alert |

---

## Backtesting Results (as of 2026-05-29)

- **Period:** 15 months (Nov 2024 – May 2026)
- **Pairs:** BTC, ETH, SOL, BNB, XRP, LINK, ADA, AVAX, DOGE
- **Regimes tested:** BULL_TREND, BEAR_TREND, RANGING, HIGH_VOL, LOW_VOL
- **Grid approved:** 16 strategies (ALL 9 pairs in BEAR_TREND at avg 8.0% return; 5 pairs in RANGING)
- **DCA approved:** LINK/USDT (5.42%) and ADA/USDT (4.75%) in RANGING only
- **Current active regime:** RANGING — 5 grid bots running (SOL, BNB, XRP, LINK, ADA)
- **Matrix age:** Refresh weekly or after significant market regime shift (`python3 main.py --backtest`)

---

## Before Going Live (ENV=live checklist)

- [ ] Widen grid spacing to 1.5–2% (covers 0.1%/side Binance fee — current 1% loses money on fees)
- [ ] Enable BNB fee payment on Binance (reduces to 0.075%/side)
- [ ] Deploy with $200 USDT — $40/pair across 5 pairs (or $100/pair with 2 pairs only)
- [ ] Refresh profitability matrix on live data (`python3 main.py --backtest`)
- [ ] Replace spacing-% P&L estimate with `exchange.fetch_my_trades()` for actual net P&L
- [ ] IP-whitelist VPS IP on Binance API key
- [ ] Confirm DMS automation running (cron-job.org POST /confirm daily)

---

## Environment Variables (.env)

```bash
# Exchange
EXCHANGE=binance
ENV=testnet                    # testnet | live
BINANCE_API_KEY=...
BINANCE_API_SECRET=...

# Grid bot
GRID_PAIR=BTC/USDT             # default single-pair (unused in adaptive mode)
GRID_TOTAL_CAPITAL=100         # USD per pair
GRID_LEVELS=10
GRID_SPACING_PCT=1.0           # % between levels (use 1.5–2.0 on live for fees)
GRID_STOP_LOSS_PCT=8.0

# DCA bot
DCA_PAIR=ETH/USDT
DCA_TOTAL_CAPITAL=100
DCA_BASE_ORDER_PCT=20
DCA_SAFETY_ORDER_PCT=10
DCA_MAX_SAFETY_ORDERS=5
DCA_TAKE_PROFIT_PCT=2.0
DCA_PRICE_DROP_PCT=2.5

# Notifications
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# Circuit breaker
CB_DRAWDOWN_PCT=15.0
CB_VELOCITY_PCT=8.0
CB_API_ERROR_COUNT=5

# Dead man's switch
DMS_HALT_HOURS=30

# Portfolio monitor integration (optional)
MONITOR_URL=https://your-monitor.railway.app   # POST /halt on emergency shutdown

# Railway
PORT=8080
```
