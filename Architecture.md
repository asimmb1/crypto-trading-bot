# 01 — Trading Bot Progress Tracker

**System:** Grid Bot + DCA Bot + Adaptive Orchestrator (CCXT + Python)  
**Priority:** 1st — Deploy first, generates income while you build everything else  
**Estimated Time:** 2 weeks  
**Actual Time:** ~3 sessions  
**Last Updated:** 2026-05-30  

---

## Phases

### Phase 1 — Environment & Exchange Connection ✅
- [x] Python 3.10+ installed, venv created
- [x] `pip install -r requirements.txt` successful
- [x] `.env` file created from `.env.example`
- [x] Binance Testnet API key created and verified
- [x] `python -m src.test_connection` runs without error — prints BTC/USDT price + balance
- [x] Discord webhook configured (replaced Telegram — simpler, no library needed)

### Phase 2 — Grid Bot ✅
- [x] `grid_bot.py` — GridBot class with pair/capital/exchange injection
- [x] Grid level calculation working (% spacing from center price)
- [x] Limit buy/sell orders placed on testnet
- [x] Order fill detection working (polling every 30s)
- [x] Counter-order logic: buy fills → place sell above; sell fills → place buy below
- [x] Stop loss: price drops 8% from entry → cancel all, alert Discord
- [x] Daily P&L summary via Discord
- [x] `stop()` method for clean shutdown by adaptive bot
- [x] Tested on Binance Testnet — orders confirmed live

### Phase 3 — DCA Bot ✅
- [x] `dca_bot.py` — DCABot class with pair/capital/exchange injection
- [x] DCA trigger logic: buy on % drop from last purchase
- [x] Max safety orders configurable (default 5)
- [x] Take profit logic: sell when price > avg_entry × (1 + take_profit_pct)
- [x] `stop()` method for clean shutdown by adaptive bot
- [x] Tested on Binance Testnet

### Phase 4 — Discord Alerts ✅
- [x] Discord webhook URL configured in `.env`
- [x] `notifier.py` — Discord embeds (colour-coded, structured fields)
- [x] Alerts fire on: order fill, stop loss, DCA trigger, daily P&L summary
- [x] Bot start / stop / switch events sent to Discord

### Phase 5 — Backtesting ✅
- [x] `fetch_history.py` — 15 months OHLCV via yfinance for 9 pairs
- [x] `backtest_grid.py` — month-by-month grid simulation with accumulated totals
- [x] `backtest_dca.py` — month-by-month DCA simulation with accumulated totals
- [x] `backtest_regime.py` — full regime analysis: grid + DCA tested per regime per pair
- [x] Profitability matrix saved to `logs/profitability_matrix.json`
- [x] Results: Grid approved in 16 scenarios across 9 pairs. DCA approved for LINK + ADA in RANGING only.

### Phase 5b — Adaptive Intelligence ✅ (beyond original spec)
- [x] `market_classifier.py` — ADX(14), ATR(14), SMA(200), BB. Detects 5 regimes.
- [x] `strategy_selector.py` — loads profitability matrix, returns grid/dca/sit_out per pair+regime
- [x] `adaptive_bot.py` — multi-pair orchestrator, regime checks every 4h, auto-switches strategy
- [x] 5-day regime confirmation filter (no reacting to noise)
- [x] Worker threads per pair — each bot runs independently, restarts if crashed

### Phase 5c — Capital Protection ✅ (beyond original spec)
- [x] `circuit_breaker.py` — 3 trip wires: portfolio drawdown (15%), crash velocity (8%/candle), exchange API errors
- [x] Emergency stop: cancels all orders → locks system → Discord alert
- [x] `resume.py` — requires `--confirm` flag to resume after trip (human gate)
- [x] `dead_mans_switch.py` — 24h heartbeat, auto-halt if no human confirmation in 30h
- [x] `confirm.py` — `python -m src.confirm` resets the daily timer

### Phase 6a — Railway Cloud Testnet Deployment ✅ (prepared, pending push)
- [x] `railway.toml` — build + start command (`mkdir -p logs && python3 main.py`)
- [x] `.python-version` — pins Python 3.10 for Nixpacks
- [x] `.gitignore` — updated: excludes `.env`, log contents, keeps `logs/.gitkeep`
- [x] `logs/.gitkeep` — preserves logs/ directory in git
- [x] `src/health_server.py` — HTTP daemon thread: `/health`, `/confirm`, `/dashboard`, `/api/trades`, `/cancel_orphan`, `/shutdown`
- [x] `main.py` — creates `logs/` on startup, starts health server before adaptive bot
- [x] `DEPLOY.md` — step-by-step Railway deploy guide (env vars, volume, DMS automation)
- [ ] Push to private GitHub repo
- [ ] Create Railway project, connect repo
- [ ] Set all env vars in Railway dashboard
- [ ] Add 1GB persistent volume at `/app/logs`
- [ ] Upload `logs/profitability_matrix.json` via Railway shell
- [ ] Verify healthy startup in Railway logs
- [ ] Set up daily DMS confirm via cron-job.org → `POST /confirm`

### Phase 6a-ext — Trader Dashboard & Operational Safety ✅
- [x] **Startup reconcile** — `_reconcile_or_init()`: fetches existing exchange orders on restart → prevents 2× deployed capital
- [x] **SIGTERM / SIGINT handler** — graceful cancel within 10s Railway grace window
- [x] **Uncovered-position recovery** — `_place_uncovered_sells()`: detects BUY fills during downtime, places recovery SELL
- [x] **Entry-price inference** — `sell_price / (1 + spacing_pct)` recovers original buy price via grid geometry
- [x] **Cancel queue pattern** — dashboard writes `cancel_queue.json`; bot processes BEFORE fill detection → no fake fills
- [x] **Trader dashboard** — `GET /dashboard`: dark-theme HTML, auto-refresh 60s
- [x] **Dead Man's Switch confirm button** — colour-coded DMS badge (green/yellow/red)
- [x] **Orphaned orders panel** — BUY = Cancel button, SELL = Market Sell button (tokens never left unattended)
- [x] **Emergency shutdown button** — typed SHUTDOWN confirmation; two modes
- [x] **Hover tooltips** — all action buttons explain consequences before click
- [x] **Centralized daily summary** — one Discord message per UTC day, all pairs aggregated
- [x] **Discord rate limiter** — `threading.Lock` + 1.2s minimum + retry-after handling

### Phase 6b — Bug Fixes & Dashboard Hardening ✅ (2026-05-30)
- [x] **Shutdown race condition** — `_shutdown = True` moved to FIRST line of `_initiate_shutdown()` so main loop watchdog doesn't restart workers mid-shutdown
- [x] **Counter-sell fee deduction** — `MAKER_FEE_RATE = 0.001` applied to counter-sell quantity; Binance deducts 0.1% from received base asset on BUY fills
- [x] **Discord `retry_after` bug** — was dividing by 1000 (seconds→microseconds); Discord returns seconds. Was waiting 0ms on rate limit.
- [x] **Stop signal during error sleep** — replaced `time.sleep(60)` with 5s interruptible chunks; `stop()` now responded to within 5s
- [x] **Cancel queue race condition** — `_CANCEL_QUEUE_LOCK = threading.Lock()` at module level; all 5 pair threads share it; prevented double-processing of cancelled orders
- [x] **Workers start before CB check** — `run()` now checks `self.cb.is_tripped()` BEFORE calling `_classify_all()`; if locked from previous session, no workers start, Discord alert fires
- [x] **Initial sell orders failing** — `has_inventory` now checks `base_free >= amount_per_order × (1 - fee_rate)` (must have enough for one full order, not just dust)
- [x] **Fresh grid missing `_place_uncovered_sells`** — now called from fresh grid path too; partial inventory (e.g. 1.01 LINK < 1 order size) creates a single recovery sell instead of failing 5 standard sells
- [x] **`reconciled_orders.json` stale entries** — `_write_reconciled()` now REPLACES pair's entries (was appending); `_clear_reconciled_for_pair()` on fresh grid start; file wiped on any shutdown
- [x] **Free USDT card not showing** — moved balance read to top of `_get_trade_stats()` before early returns; moved JS balance update before `available` guard; seeded `balance.json` immediately
- [x] **Market sell on orphan cancel** — checks actual free balance first; if tokens not in account (prior emergency sell) cleans up silently; if sell fails, order stays on dashboard for retry

### Phase 6c — Dashboard UX & Observability ✅ (2026-05-30)
- [x] **Free USDT card** — live exchange balance (`usdt_free`), updates every ~30s, shows `of $X total` sub-label
- [x] **"In Buy Orders" card** — real `usdt_used` from exchange balance (replaces config-estimate "Capital Deployed")
- [x] **Win Rate card** — % of tracked sells with positive P&L; excludes untracked orphan sells
- [x] **Today's P&L sub-label** — shows today's tracked P&L alongside all-time on the P&L card
- [x] **Untracked sells note** — P&L sub shows `N estimated` when orphan sells fall back to spacing-% approximation
- [x] **Tooltips on all 6 stat cards** — detailed hover explanation of what each card measures, what moves it, caveats
- [x] **Active Open Orders panel** — live table of every open limit order; pair, side, price, amount, USD value; sourced from `logs/active_PAIR.json` written every 30s by each grid bot
- [x] **Total row with match indicator** — buy total in active orders table shows `✓ matches In Buy Orders` (green) or `⚠ differs by $X` (yellow) vs `usdt_used`
- [x] **CB badge distinction** — manual shutdown shows yellow `⏸ System Locked` badge; real CB trip shows red `⚠ CB Tripped` badge; previously both showed red alarm
- [x] **Mobile responsive dashboard** — `@media (max-width: 640px)`: 2-column cards, stacked header, scrollable tables, viewport-clamped tooltips; shutdown modal slides up as bottom sheet on mobile
- [x] **P&L uses real DB values** — actual `pnl` column from DB used when entry price was known; spacing-% estimate only for untracked orphan sells
- [x] **Per-pair table clarified** — columns renamed "Buy Fills / Sell Fills", subtitle explains cumulative-since-start not current orders
- [x] **`balance.json` always current** — `_fetch_and_snapshot_balance()` called from both normal CB path AND CB-tripped sleep branch; dashboard stays live even when system is locked

### Phase 6d — VPS Live Deployment ⬜ (real money)
- [ ] Run `python3 main.py --backtest` to refresh strategy matrix (current one is 24h+ old)
- [ ] Push to private GitHub repo
- [ ] VPS provisioned (Hetzner CX11 or DigitalOcean $4/mo)
- [ ] Code pushed to VPS via git
- [ ] `.env` set on VPS — `ENV=live`, real Binance API keys (IP-whitelisted to VPS)
- [ ] `adaptive_bot.service` installed and running via systemd
- [ ] `python -m src.confirm` added to crontab (daily confirmation)
- [ ] `python main.py --status` clean on VPS
- [ ] First 24 hours monitored manually
- [ ] Capital: $200 USDT allocated (widen spacing to 1.5–2% for fees, enable BNB fee payment)

---

## Current Status

**Phase:** 6a–6c complete. Next: refresh backtest matrix → push to GitHub → Railway testnet deploy.  
**Last Updated:** 2026-05-30  
**Environment:** Testnet (ENV=testnet)  
**Active Bots:** 5 grid bots — SOL/USDT, BNB/USDT, XRP/USDT, LINK/USDT, ADA/USDT (RANGING regime)  
**Sitting Out:** BTC/USDT, ETH/USDT, AVAX/USDT, DOGE/USDT (no approved strategy in RANGING)

**Notes:**
```
- All bots start cleanly — CB pre-check prevents unmonitored workers on locked restarts
- Buy-only grids on fresh starts (BNB, XRP) when no base asset held — correct behaviour
- LINK partial inventory (< 1 order size) now handled by _place_uncovered_sells, not failed sells
- Cancel queue race condition fixed — no more duplicate -2011 errors on orphan cancels
- Dashboard shows 6 live stat cards, active orders, fill history, orphaned orders panel
- Free USDT + In Buy Orders cards update every ~30s from exchange balance
- Active orders table has total row with match indicator vs In Buy Orders card
- Mobile responsive — tested on 640px and 520px breakpoints
- CB badge correctly distinguishes manual lock (yellow) from real trip (red)
- Strategy matrix: 16 approved strategies across 9 pairs — refresh weekly

Before going live:
  1. Widen grid spacing to 1.5–2% minimum (covers 0.1%/side Binance fee)
  2. Enable BNB fee payment on Binance (reduces fee to 0.075%/side)
  3. Replace approx P&L with exchange.fetch_my_trades() for real net P&L
  4. Capital: $200 USDT deployed across 5 pairs ($40/pair or $100/pair with 2 pairs only)
```

---

## Daily Operator Commands

```bash
# ── Must run daily (keep dead man's switch alive) ──────────────────────
python3 -m src.confirm
# OR via HTTP (local or Railway):
curl -X POST http://localhost:8080/confirm
# OR: click "❤️ Confirm I'm Alive" on the dashboard

# ── Dashboard (open in browser) ────────────────────────────────────────
http://localhost:8080/dashboard          # local
https://YOUR-APP.railway.app/dashboard  # Railway

# ── System health (JSON) ───────────────────────────────────────────────
python3 main.py --status
curl http://localhost:8080/health

# ── Refresh strategy matrix (weekly or after market shift) ─────────────
python3 main.py --backtest

# ── Resume after circuit breaker trip or manual shutdown ───────────────
python3 -m src.resume --confirm

# ── Emergency shutdown (also available via dashboard button) ───────────
curl -X POST http://localhost:8080/shutdown \
  -H "Content-Type: application/json" \
  -d '{"confirm":"SHUTDOWN","sell_positions":false}'

# ── View logs (local) ─────────────────────────────────────────────────
tail -f logs/adaptive_bot.log
tail -f logs/grid_bot.log
```

---

## Issues Log

| Date | Issue | Resolution |
|------|-------|-----------|
| 2026-05 | python3.11 not installed on Mac | Used python3 (3.10) — fully compatible |
| 2026-05 | Discord messages showing HTML tags | Fixed: replaced `<b>` with `**` markdown in notifier + bot files |
| 2026-05 | yfinance MultiIndex columns | Fixed: flatten with `c[0].lower()` when MultiIndex detected |
| 2026-05 | JSON serialisation of numpy bool | Fixed: recursive `to_native()` converter before `json.dumps()` |
| 2026-05 | `ModuleNotFoundError: src` | Run as `python3 -m backtests.backtest_regime`; added `backtests/__init__.py` |
| 2026-05-29 | `GridBot has no attribute open_orders` | Instance variables accidentally moved into `stop()`. Moved back to `__init__()`. |
| 2026-05-29 | `fetch_open_orders` failing for all pairs | Clock drift / testnet blip. Fixed: `adjustForTimeDifference=True`, `recvWindow=60000`. |
| 2026-05-29 | Railway "Mirrors/Userbots" policy concern | Confirmed safe: policy targets Telegram userbots, not exchange trading bots. |
| 2026-05-30 | Dashboard blank after startup | (1) empty-DB missing required keys; (2) Chart.js CDN failure inside shared try/catch; (3) Python `\'` escape in triple-quoted JS string. All fixed. |
| 2026-05-30 | 5 Discord daily summaries (one per bot) | Centralized in `adaptive_bot.py`; fires once at 23:00 UTC, aggregates all pairs. |
| 2026-05-30 | Double orders on Railway redeploy | Fixed: `_reconcile_or_init()` checks exchange first; SIGTERM handler registered. |
| 2026-05-30 | Discord 429 rate limit burst on startup | Fixed: `threading.Lock` + 1.2s min interval + retry-after in `notifier.py`. |
| 2026-05-30 | Shutdown restarted workers mid-shutdown | Fixed: `_shutdown = True` moved to FIRST line of `_initiate_shutdown()`. |
| 2026-05-30 | Counter-sell "insufficient balance" | Fixed: `MAKER_FEE_RATE = 0.001` applied; Binance deducts fee from received base asset. |
| 2026-05-30 | Discord `retry_after` waiting 0ms | Fixed: removed erroneous `/ 1000` — Discord returns seconds, not milliseconds. |
| 2026-05-30 | Stop signal ignored for 60s after error | Fixed: `time.sleep(60)` → 12 × 5s chunks, `_running` checked each iteration. |
| 2026-05-30 | Free USDT card showing dashes | Three causes: (1) balance.json not written when CB tripped; (2) early return before balance card JS; (3) early returns missing balance key. All fixed. |
| 2026-05-30 | Cancel queue race condition (-2011 flood) | Fixed: `_CANCEL_QUEUE_LOCK = threading.Lock()` — shared across all pair threads. |
| 2026-05-30 | Workers start with pre-tripped CB | Fixed: `run()` checks `cb.is_tripped()` before `_classify_all()`. |
| 2026-05-30 | Stale orphaned orders after shutdown | Fixed: `_write_reconciled()` replaces (not appends); shutdown clears file; fresh grid clears pair entries. |
| 2026-05-30 | Initial sell orders failing (LINK/BNB) | Fixed: `has_inventory` now requires `base_free >= amount_per_order × (1 - fee_rate)`. |
| 2026-05-30 | `_place_uncovered_sells` not on fresh path | Fixed: now called from fresh grid path as well as reconcile path. |
| 2026-05-30 | Market sell fails silently when no tokens | Fixed: balance check before market sell; if tokens absent, clean up silently. |
| 2026-05-30 | Orphan SELL cancel left tokens unattended | Fixed: BUY = Cancel, SELL = Market Sell button; order stays on dashboard if sell fails. |
| 2026-05-30 | CB badge shown red for manual shutdown | Fixed: checks `reason` field — "manual"/"dashboard" → yellow `⏸ Locked` badge. |
