# 01 — Trading Bot Progress Tracker

**System:** Grid Bot + DCA Bot + Adaptive Orchestrator (CCXT + Python)  
**Priority:** 1st — Deploy first, generates income while you build everything else  
**Estimated Time:** 2 weeks  
**Actual Time:** ~1 session  

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

### Phase 6 — Live Deployment ⬜
- [ ] VPS provisioned (Hetzner CX11 or DigitalOcean $4/mo)
- [ ] Code pushed to VPS via git (private repo)
- [ ] `.env` set on VPS — `ENV=live`, real Binance API keys
- [ ] `adaptive_bot.service` installed and running via systemd
- [ ] `python main.py --backtest` run on VPS to refresh profitability matrix
- [ ] `python -m src.confirm` added to crontab (daily confirmation)
- [ ] `python main.py --status` clean on VPS
- [ ] First 24 hours monitored manually
- [ ] Capital: $200 USDT allocated to adaptive bot on Binance

---

## Current Status

**Phase:** 1–5c Complete. Ready for Phase 6 (live deployment).  
**Last Updated:** May 2026  
**Environment:** Testnet (ENV=testnet)  
**Notes:**
```
- Both bots confirmed running on Binance testnet
- Discord notifications working with rich embeds
- Profitability matrix: 16 approved strategies across 9 pairs
- Regime backtest period: 15 months (Nov 2024 – May 2026)
- Grid bot approved for ALL 9 pairs in BEAR_TREND
- DCA bot approved for LINK/USDT and ADA/USDT in RANGING only
- Circuit breaker armed, dead man's switch armed
- To go live: change ENV=live, add real API keys, run --backtest, monitor first 24h
```

---

## Daily Operator Commands

```bash
# Must run daily to keep bots alive (dead man's switch)
python -m src.confirm

# Check system health
python main.py --status

# Refresh strategy matrix (run weekly or after major market shift)
python main.py --backtest

# Resume after circuit breaker trip
python -m src.resume --confirm

# View live logs
tail -f logs/adaptive_bot.log
tail -f logs/grid_bot.log
tail -f logs/dca_bot.log
```

---

## Issues Log

| Date | Issue | Resolution |
|------|-------|-----------|
| 2026-05 | python3.11 not installed on Mac | Used python3 (3.10) — fully compatible |
| 2026-05 | Discord messages showing HTML tags | Fixed: replaced `<b>` with `**` markdown |
| 2026-05 | yfinance MultiIndex columns | Fixed: flatten with `c[0].lower()` |
| 2026-05 | JSON serialisation of numpy bool | Fixed: recursive `to_native()` converter |
| 2026-05 | `ModuleNotFoundError: src` | Run as `python -m backtests.backtest_regime` not `python backtests/...` |
