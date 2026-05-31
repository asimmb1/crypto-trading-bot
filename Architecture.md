# 01 — Trading Bot Progress Tracker

**System:** Grid Bot + DCA Bot + Adaptive Orchestrator + (Planned: Wide Grid, Trailing DCA, Breakout)  
**Priority:** 1st — Core income engine. Extend to full market coverage.  
**Estimated Time:** 2 weeks initial + 2–3 months for full strategy suite  
**Actual Time:** ~4 sessions to current state  
**Last Updated:** 2026-05-31  

---

## Phases

### Phase 1 — Environment & Exchange Connection ✅
- [x] Python 3.10+ installed, venv created
- [x] `pip install -r requirements.txt` successful
- [x] `.env` file created from `.env.example`
- [x] Binance Testnet API key created and verified
- [x] `python -m src.test_connection` runs without error
- [x] Discord webhook configured

### Phase 2 — Grid Bot ✅
- [x] GridBot class with pair/capital/exchange injection
- [x] Grid level calculation with dynamic price precision (ADA/XRP use 4dp, not 2dp)
- [x] Limit buy/sell orders placed on testnet
- [x] Order fill detection polling every 30s
- [x] Counter-order logic with MAKER_FEE_RATE = 0.001 applied to counter-sell qty
- [x] Stop loss: price drops 8% from entry → cancel all
- [x] `stop()` method for clean shutdown

### Phase 3 — DCA Bot ✅
- [x] DCA trigger logic: buy on % drop from last purchase
- [x] Max safety orders configurable (default 5)
- [x] Take profit logic
- [x] Tested on Binance Testnet

### Phase 4 — Discord Alerts ✅
- [x] Discord embeds, colour-coded, structured fields
- [x] Rate limiter: threading.Lock + 1.2s min interval + retry-after (in seconds, not ms)
- [x] Centralized daily summary from adaptive_bot (not per-bot)

### Phase 5 — Backtesting ✅
- [x] 15 months OHLCV via yfinance for 9 pairs
- [x] Month-by-month grid + DCA simulation
- [x] Full regime analysis → profitability_matrix.json
- [x] 16 approved strategies: all 9 in BEAR_TREND, 5 in RANGING, DCA for LINK+ADA in RANGING

### Phase 5b — Adaptive Intelligence ✅
- [x] market_classifier.py — 5 regimes (BULL_TREND, BEAR_TREND, RANGING, HIGH_VOL, LOW_VOL)
- [x] strategy_selector.py — reads profitability matrix
- [x] adaptive_bot.py — multi-pair orchestrator, regime checks every 4h
- [x] 5-day regime confirmation filter

### Phase 5c — Capital Protection ✅
- [x] circuit_breaker.py — 3 triggers: 15% drawdown, 8%/candle velocity, 5 API errors
- [x] dead_mans_switch.py — 30h heartbeat
- [x] resume.py — human gate to unlock
- [x] confirm.py — daily DMS reset

### Phase 6a — Railway Cloud Deployment ✅
- [x] railway.toml, .python-version, DEPLOY.md
- [x] health_server.py — /health /dashboard /api/trades /confirm /shutdown /cancel_orphan
- [x] Deployed and running: https://crypto-trading-bot-production-512b.up.railway.app
- [x] Daily DMS confirm → set up via cron-job.org

### Phase 6b — Bug Fixes & Hardening ✅ (2026-05-30)
- [x] Shutdown race condition — `_shutdown = True` moved to FIRST line of `_initiate_shutdown()`
- [x] Counter-sell fee deduction — `MAKER_FEE_RATE = 0.001` applied to counter-sell qty
- [x] Discord `retry_after` bug — was dividing by 1000 erroneously
- [x] Stop signal during error sleep — 5s interruptible chunks
- [x] Cancel queue race condition — `_CANCEL_QUEUE_LOCK = threading.Lock()` shared across threads
- [x] CB pre-check before workers start — prevents unmonitored workers on locked restart
- [x] Initial sell orders failing — `has_inventory` checks full order size not dust
- [x] `_place_uncovered_sells` missing from fresh grid path — added
- [x] `reconciled_orders.json` stale entries — replace not append; clear on shutdown
- [x] Free USDT card not showing — balance read before early returns; JS update before `available` guard
- [x] Market sell on orphan cancel — balance check first; stays on dashboard if sell fails
- [x] ADA/XRP grid level collisions — `_price_decimals()` dynamic precision function

### Phase 6c — Dashboard UX & Observability ✅ (2026-05-30)
- [x] 6 stat cards with hover tooltips: Free USDT, In Buy Orders (real usdt_used), Gross P&L, Win Rate, Round Trips, Total Fills
- [x] Active Open Orders panel — live from logs/active_PAIR.json every 30s
- [x] Total row with match indicator vs In Buy Orders card
- [x] CB badge: yellow for manual lock, red for real trip
- [x] Mobile responsive — 640px breakpoints, shutdown modal as bottom sheet
- [x] P&L uses real DB values, estimate only for untracked orphan sells
- [x] balance.json written even when CB tripped
- [x] Round Trip History modal — cumulative P&L chart, trip cards, pair filter
- [x] Grid Context Panel — full-height slide-in from right, all 5 fill states colour-coded
- [x] Grid chain visualization — buy→sell pairs linked, streak badge
- [x] 3 fill states: BUY filled (purple ✓), SELL tracked (yellow ★), SELL untracked (muted ~)
- [x] Demo mode in panel — shows all states with mock data

### Phase 6d — Live Deployment & Strategy Validation ✅ (2026-05-31)
- [x] Railway live: crypto-trading-bot-production-512b.up.railway.app
- [x] Strategy matrix refreshed via Railway CLI (`railway run python3 main.py --backtest`)
- [x] 4 completed SOL round trips confirmed on Railway (+$1.40 total P&L)
- [ ] VPS live money deployment (ENV=live, real Binance keys, $300–500 capital)
- [ ] Enable BNB fee payment (reduces fee to 0.075%/side)
- [ ] Widen spacing to 1.5–2% on live (covers 0.2% round-trip fee)
- [ ] Start with 2 pairs: LINK + SOL ($250/pair = $500 total)

### Phase 7 — Wide-Spacing Grid for HIGH_VOL ⬜
*Covers the first blind spot: HIGH_VOL currently has zero strategy (10–20% of market time)*

- [ ] `src/wide_grid_bot.py` — GridBot subclass with HIGH_VOL-specific parameters
  - Spacing: 4–6% (vs 1.5% standard)
  - Capital: 50% of normal per-pair capital (reduced risk)
  - Stop-loss: 15% (wider — HIGH_VOL means large swings)
  - Levels: 6 (fewer, larger moves per fill)
- [ ] `backtests/backtest_wide_grid.py` — validate wide grid profitability in HIGH_VOL periods
  - Must achieve ≥60% win rate over ≥3 occurrences to be approved
  - Compare vs sitting out: wide grid must outperform 0% return
- [ ] Add `wide_grid` strategy to strategy_selector.py
- [ ] Update adaptive_bot.py to launch WideGridBot when HIGH_VOL detected
- [ ] Dashboard: "WIDE GRID" badge in Active Orders (distinct from "GRID")
- [ ] Dashboard: separate backtest row in Per-Pair Fill History for wide grid cycles
- [ ] Discord: different notification template for wide grid fills

**Edge cases to handle:**
- Flash crash blows through all 6 levels in one candle → stop-loss must be exchange stop-limit, not just bot polling
- HIGH_VOL → RANGING transition: wide grid has sell orders far above → close all, deploy standard grid
- Wide grid with 6 levels at 5% spacing requires price to move 30% total — may not fill in mild HIGH_VOL
- Fee calculation: 5% gross − 0.2% fees = 4.8% net (excellent, worth the risk)

### Phase 8 — Trailing DCA for BULL_TREND ⬜
*Covers the biggest blind spot: BULL_TREND currently sits out entirely (20–35% of market time, highest-return phase)*

- [ ] `src/trailing_dca_bot.py` — new bot class
  - State machine: IDLE → ENTERING → POSITION_HELD → TRAILING → EXITED
  - Persistent state file: `logs/trailing_dca_state.json`
  - Tracks: entry price, average cost, peak price, current trailing stop
- [ ] Entry logic:
  - BULL_TREND confirmed 5 days
  - Buy base position (20% of Growth capital)
  - Safety orders: additional buys on each 3–5% dip (max 3 safety orders)
  - Average entry tracked across all safety orders
- [ ] Trailing stop logic:
  - Trailing stop initialized at 15% below first entry
  - Raises to 15% below running peak (never lowers)
  - On each price check (every 5 min): if current_price < trailing_stop → exit all at market
  - Stop level stored in state file and displayed on dashboard
- [ ] Exit logic:
  - Trailing stop triggered → market sell all, log as single trade
  - Regime change from BULL_TREND → immediate market close regardless of P&L
  - Manual shutdown → market sell in sell_positions=True mode
- [ ] Restart recovery:
  - On startup: read trailing_dca_state.json
  - Verify position exists on exchange (fetch_balance)
  - Restore peak_price and trailing_stop_level from state file
  - Continue trailing from where it left off — no double-entry
- [ ] Capital: separate env var `TRAILING_DCA_CAPITAL` from Growth bucket (not grid capital)
- [ ] Limit to top 2 pairs by BULL_TREND backtest return to avoid thin spread
- [ ] `backtests/backtest_trailing_dca.py` — historical BULL_TREND simulation
- [ ] Dashboard: new "Trailing Position" section
  - Shows: pair, entry price, current price, peak price, trailing stop level, unrealised P&L, % from stop
  - Color: green when > 10% above stop, yellow when 5–10%, red when < 5% (near exit)

**Edge cases to handle:**
- Regime flips BULL → BEAR mid-position: exit immediately at market, don't wait for trailing stop
- Safety orders fire in rapid succession (flash dip): rate-limit order placement, max 1 safety order per 5 min
- Multiple pairs both in BULL_TREND: cap at 2 pairs max (avoid over-concentration)
- Position size too small after safety orders dilute average: minimum position value $50
- Peak price from previous session lost on restart: state file is the single source of truth
- If state file missing/corrupted: close all positions safely, restart clean
- Exchange API down during trailing stop trigger: keep retrying every 30s, alert Discord
- Stop triggered between poll intervals (price briefly dips): accept slight slippage (normal for trailing stops)

### Phase 9 — Breakout Positioning for LOW_VOL ⬜
*Covers the third blind spot: LOW_VOL precedes explosive moves — currently sits out entirely*

- [ ] `src/breakout_bot.py` — new bot class
  - Squeeze detection: ATR(14) < 50% of its 6-month rolling average
  - State machine: WATCHING → PRE_POSITIONED → BULL_CONFIRMED → BEAR_CONFIRMED → EXITED
- [ ] Pre-position phase:
  - When squeeze detected: buy 5% of Growth capital at current price
  - Place stop-loss order at 8% below entry
  - Mark as "watching" — direction not yet confirmed
- [ ] Confirmation phase:
  - BULL: price closes above upper Bollinger Band on volume → add to 20% of Growth capital, raise stop to 8% below previous day's close
  - BEAR: price closes below lower Bollinger Band → close pre-position at market (small loss accepted), switch to grid-ready mode
- [ ] Trailing in confirmed direction:
  - Once confirmed bull: trail stop at 12% below running peak
  - Once confirmed bear: no position held (spot only — can't short)
- [ ] `backtests/backtest_breakout.py` — historical LOW_VOL breakout simulation
  - Test on all LOW_VOL periods in 15-month dataset
  - Measure win rate on direction confirmation
- [ ] Dashboard: "Breakout Watch" section when squeeze active
  - Shows: pair, Bollinger Band width vs historical, pre-position status, distance to confirm level

**Edge cases to handle:**
- False breakout (poke above band then reversal): tight stop 8% catches it, max loss = 5% Growth capital
- Multiple pairs squeezing simultaneously: limit to 1 breakout position per pair, max 2 pairs total
- Squeeze resolves with no volume: wait — no confirmation = no additional capital committed
- Regime transitions to HIGH_VOL during squeeze: close pre-position, switch to wide grid
- Very slow squeezes lasting weeks: cap maximum watching period at 21 days

### Phase 10 — Production Hardening & Full Test Suite ⬜
- [ ] Unit test coverage: `tests/test_grid_bot.py`, `tests/test_trailing_dca.py`, `tests/test_breakout_bot.py`, `tests/test_market_classifier.py`
- [ ] Integration tests: regime switch → strategy switch end-to-end
- [ ] All new strategies run 48h testnet before live
- [ ] Replace P&L estimate with `exchange.fetch_my_trades()` for live trading
- [ ] Net P&L (after real fees) displayed separately from gross
- [ ] Portfolio-level position sizing: total deployed never exceeds TOTAL_CAPITAL_LIMIT env var
- [ ] VPS live deployment: Hetzner CX21 (2 vCPU, 4GB RAM — handles all strategies)

---

## Current Status

**Phase:** 6a–6d in progress. Railway cloud running with real market data.  
**Last Updated:** 2026-05-31  
**Environment:** Testnet (Railway) — 4 confirmed round trips on SOL, +$1.40 P&L  
**Strategy coverage:** 40% of market conditions (RANGING + BEAR_TREND only)  
**Target coverage:** 100% (Phases 7–9 add HIGH_VOL, BULL_TREND, LOW_VOL)

**Active Pairs:** SOL, BNB, XRP, LINK, ADA (RANGING regime)  
**Sitting Out:** BTC, ETH, AVAX, DOGE

**Key milestones reached this session:**
- Railway live deployment confirmed working with real fills
- Grid Context Panel (slide-in, full-height, 5 colour states)
- Round Trip History with cumulative P&L chart
- Grid chain visualization + streak badge
- Financial strategy analysis: 3 blind spots identified (BULL, HIGH_VOL, LOW_VOL)
- Comprehensive strategy document: `crypto_strategy_financial_guide.docx`

**Before live real money:**
1. Widen spacing to 1.5–2% (covers 0.2% round-trip fee)
2. Enable BNB fee payment on Binance
3. Start with LINK + SOL, $250/pair
4. Confirm DMS automation on cron-job.org
5. Monitor first 48h manually

---

## Daily Operator Commands

```bash
# ── Must run daily (keep DMS alive) ────────────────────────────────────
python3 -m src.confirm
# OR: click "❤️ Confirm I'm Alive" on the dashboard
# OR automated: cron-job.org POST https://crypto-trading-bot-production-512b.up.railway.app/confirm

# ── Dashboard ───────────────────────────────────────────────────────────
https://crypto-trading-bot-production-512b.up.railway.app/dashboard

# ── Refresh strategy matrix (weekly) ───────────────────────────────────
railway run --service crypto-trading-bot python3 main.py --backtest
# OR locally:
python3 main.py --backtest

# ── Resume after any lock ───────────────────────────────────────────────
python3 -m src.resume --confirm

# ── Emergency shutdown ─────────────────────────────────────────────────
# Via dashboard Shutdown button (preferred)
# OR: curl -X POST https://YOUR-APP.railway.app/shutdown \
#   -H "Content-Type: application/json" \
#   -d '{"confirm":"SHUTDOWN","sell_positions":false}'
```

---

## Issues Log

| Date | Issue | Resolution |
|------|-------|-----------|
| 2026-05 | python3.11 not installed | Used python3 (3.10) — fully compatible |
| 2026-05 | Discord HTML tags showing | Fixed: replaced `<b>` with `**` markdown |
| 2026-05 | yfinance MultiIndex columns | Fixed: flatten with `c[0].lower()` |
| 2026-05 | JSON serialisation numpy bool | Fixed: recursive `to_native()` converter |
| 2026-05 | `ModuleNotFoundError: src` | Run as `python3 -m backtests.backtest_regime` |
| 2026-05-29 | `GridBot has no attribute open_orders` | Instance vars accidentally in stop(). Moved to __init__(). |
| 2026-05-29 | `fetch_open_orders` failing all pairs | Clock drift. Fixed: `adjustForTimeDifference=True`, `recvWindow=60000` |
| 2026-05-29 | Railway policy concern | Confirmed safe — targets Telegram userbots, not exchange bots |
| 2026-05-30 | Dashboard blank after startup | 3 causes: empty-DB keys, Chart.js CDN, Python \\' escape. All fixed. |
| 2026-05-30 | 5 Discord daily summaries | Centralized in adaptive_bot.py, fires once at 23:00 UTC |
| 2026-05-30 | Double orders on redeploy | Fixed: reconcile_or_init(); SIGTERM handler |
| 2026-05-30 | Discord 429 burst on startup | Fixed: threading.Lock + 1.2s + retry-after |
| 2026-05-30 | Shutdown restarts workers | Fixed: _shutdown=True moved to FIRST line |
| 2026-05-30 | Counter-sell "insufficient balance" | Fixed: MAKER_FEE_RATE=0.001 applied |
| 2026-05-30 | Discord retry_after waiting 0ms | Fixed: removed erroneous /1000 division |
| 2026-05-30 | Stop signal ignored 60s | Fixed: 5s interruptible chunks |
| 2026-05-30 | Free USDT card showing dashes | Fixed: balance before early returns; JS before available guard |
| 2026-05-30 | Cancel queue race condition | Fixed: _CANCEL_QUEUE_LOCK shared across threads |
| 2026-05-30 | Workers start before CB check | Fixed: cb.is_tripped() before _classify_all() |
| 2026-05-30 | Stale orphaned orders after shutdown | Fixed: replace not append; clear on shutdown |
| 2026-05-30 | Initial sell orders failing (LINK/BNB) | Fixed: has_inventory checks full order size |
| 2026-05-30 | _place_uncovered_sells missing fresh path | Fixed: called from both paths now |
| 2026-05-30 | Market sell fails silently | Fixed: balance check first; silent cleanup if no tokens |
| 2026-05-30 | Orphan SELL leaves tokens unattended | Fixed: Market Sell button; stays on dashboard if fails |
| 2026-05-30 | CB badge red for manual shutdown | Fixed: checks reason field; yellow for manual |
| 2026-05-30 | ADA grid levels collapsing to same price | Fixed: _price_decimals() dynamic precision |
| 2026-05-30 | Grid popup cuts off, small fixed size | Fixed: full-height slide-in panel from right |
| 2026-05-30 | Popup shows wrong colors (all blue) | Fixed: uses actual orders not calculated levels; toFixed(4) exact match |
| 2026-05-30 | Sell fills showing as BUY color | Fixed: 3-way classification: BUY fill / SELL tracked / SELL untracked |
| 2026-05-31 | Strategy gap: BULL/HIGH_VOL/LOW_VOL = 0 strategies | Documented. Phases 7–9 planned to close all gaps. |
| 2026-05-31 | SMA200 issue: SOL 21% and LINK 14% below SMA200 | Grid unaffected (BEAR_TREND grid approved). Phase 8 Trailing DCA needs classifier fix: add recovery-bull detection within 15% of SMA200. Documented in TECHNICAL_SPEC.md Phase 8. |
