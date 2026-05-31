# 01 — Trading Bot: Technical Specification

**Last Updated:** 2026-05-31  
**Status:** Phase 6d live on Railway. Phases 7–9 (full market coverage) planned.  
**Target:** Production-ready bot covering 100% of market conditions.

---

## Development Workflow — READ FIRST

> **This section is for Claude and the developer at the start of every session.**  
> Before touching any code, check the branch, check what's deployed, and follow the rules below.

### Branch Structure

```
main     ← PRODUCTION. Railway auto-deploys from this on every push.
             Only stable, tested code lives here.
             Current live URL: https://crypto-trading-bot-production-512b.up.railway.app
             
  └── develop  ← ALL NEW STRATEGY DEVELOPMENT happens here.
                  Wide Grid (Phase 7), Trailing DCA (Phase 8), Breakout (Phase 9),
                  new dashboard sections, backtests, tests/.
                  Never push directly to main from develop without a reviewed merge.
```

### How to Start Every Session

```bash
# 1. Check which branch you're on
git branch

# 2. If doing live bot work (bug fix, dashboard tweak, hotfix):
git checkout main
git pull origin main          # get latest

# 3. If doing strategy development (new bots, backtests, tests):
git checkout develop
git pull origin develop       # get latest
git merge main                # sync any live fixes into develop first
```

### Decision Tree — What Branch to Use

```
Is this a bug in the LIVE bot that needs fixing NOW?
  YES → checkout main → fix → commit → push → Railway auto-deploys
        then: checkout develop → git merge main (sync the fix)
  
Is this new code for Phase 7/8/9 (new strategies, backtests, tests)?
  YES → checkout develop → develop → commit → push origin develop
        DO NOT push to main until strategy is tested and ready

Is this a docs update (Architecture.md, TECHNICAL_SPEC.md)?
  YES → either branch is fine, but prefer main for clarity
        (docs describe both current state and future plans)

Is this a dashboard UX change?
  If it fixes a live bug → main
  If it adds new panels for Phase 7/8/9 → develop
```

### Hotfix Workflow (Live Bug)

```bash
git checkout main
git pull origin main
# ... make the fix ...
git add <files>
git commit -m "Fix: <description>"
git push origin main          # Railway deploys immediately

# Sync the fix into develop so you don't lose it
git checkout develop
git merge main
git push origin develop
```

### New Strategy Development Workflow

```bash
git checkout develop
git pull origin develop
git merge main                # always sync from main before starting

# ... build wide_grid_bot.py, tests, backtest, dashboard ...

git add src/wide_grid_bot.py backtests/backtest_wide_grid.py ...
git commit -m "Phase 7: Wide-spacing grid for HIGH_VOL regime"
git push origin develop       # DOES NOT touch Railway/main
```

### Merging develop → main (When Strategy is Ready)

A strategy is ready to merge to main when:
1. ✅ `backtest_wide_grid.py` approves it (≥60% win rate, ≥3 occurrences)
2. ✅ Unit tests pass (`pytest tests/`)
3. ✅ 48h testnet dry run with no errors
4. ✅ Dashboard correctly shows new strategy state
5. ✅ Restart recovery tested (kill and restart mid-strategy)
6. ✅ CB and DMS verified to halt new strategy correctly

```bash
# On GitHub: open PR from develop → main
# Review the diff — focus on files that both branches touch:
#   src/health_server.py     (both add UI — resolve carefully)
#   src/adaptive_bot.py      (develop adds new strategy cases)
#   src/strategy_selector.py (develop adds new strategy types)
# After review and approval: merge PR → Railway auto-deploys new strategy
```

### Files That Will Have Merge Conflicts (Plan Ahead)

| File | Why Conflict | Resolution |
|------|-------------|-----------|
| `src/health_server.py` | main gets dashboard hotfixes; develop adds Phase 7/8/9 panels | Take all from develop for new sections; take hotfixes from main |
| `src/adaptive_bot.py` | main may get CB/DMS fixes; develop adds new strategy worker cases | Keep both sets of changes |
| `src/strategy_selector.py` | develop adds wide_grid/trailing_dca/breakout strategy types | Take develop version entirely (main shouldn't touch this) |
| `main.py` | develop adds new --wide-grid / --trailing-dca run modes | Take develop version, verify existing modes still work |

**New files in develop (no conflicts — they don't exist in main):**
- `src/wide_grid_bot.py`
- `src/trailing_dca_bot.py`
- `src/breakout_bot.py`
- `backtests/backtest_wide_grid.py`
- `backtests/backtest_trailing_dca.py`
- `backtests/backtest_breakout.py`
- `tests/` (entire directory)

### Weekly Sync Rule

Every time you start a development session on `develop`, merge `main` in first:
```bash
git checkout develop && git merge main
```
This keeps the gap small. If you let it drift for weeks, the merge gets painful.

### Railway Deployment Notes

- Railway watches `main` branch only. Pushing to `develop` does nothing on Railway.
- After merging to `main`, Railway builds and deploys automatically (~2 min).
- To verify the new code is live: `curl https://crypto-trading-bot-production-512b.up.railway.app/health`
- To run one-off commands on Railway: `railway run --service crypto-trading-bot <command>`

---

## Overview

Adaptive multi-pair trading bot operating across all crypto market regimes. Detects regime every 4 hours, selects the historically proven strategy for each pair, and runs independent worker threads. Three layered safety systems. Full HTTP interface with live dashboard, emergency controls, and trade visualisation.

**Current coverage:** RANGING + BEAR_TREND (40% of market conditions)  
**Target coverage:** All 5 regimes + transitions (100%)

---

## Tech Stack

| Component | Tool | Why |
|-----------|------|-----|
| Exchange API | `ccxt` | Unified API for 100+ exchanges |
| Data / backtesting | `pandas`, `numpy`, `yfinance` | OHLCV fetch, regime analysis |
| Notifications | `requests` (Discord webhooks) | No library, rich embeds, rate-limiter built in |
| Config | `python-dotenv` | Secure API key loading, fail-fast on missing vars |
| Logging | `loguru` | Structured log files with rotation |
| Storage | `sqlite3` (stdlib) | Trade log, P&L history, all fills |
| HTTP server | `http.server` (stdlib) | Self-contained dashboard, no Flask |
| Testing | `pytest` | Unit + integration tests |
| Deployment | Railway.app (testnet) → VPS/systemd (live) | Persistent volume, auto-deploy from git |

---

## Project Structure

```
01-trading-bot/
├── Architecture.md              ← Progress tracker (phases + issues log)
├── TECHNICAL_SPEC.md            ← This file
├── DEPLOY.md                    ← Railway + VPS deployment guide
├── requirements.txt
├── .env.example
├── railway.toml
├── .python-version              ← Pins Python 3.10 for Nixpacks
│
├── main.py                      ← Entry point: --adaptive / --backtest / --status
│
├── src/
│   ├── config.py                ← All env vars; fail fast if missing
│   ├── exchange.py              ← CCXT factory; clock drift fix; rate limit; recvWindow=60000
│   ├── notifier.py              ← Discord; threading.Lock; 1.2s min; retry-after in SECONDS
│   ├── database.py              ← SQLite: log_trade(), get_daily_summary_by_pair()
│   ├── adaptive_bot.py          ← Orchestrator: CB pre-check, 1 thread/pair, balance snapshot, SIGTERM
│   ├── grid_bot.py              ← Grid: reconcile, buy-only mode, MAKER_FEE_RATE, cancel queue (locked), active snapshot
│   ├── dca_bot.py               ← DCA: base order, safety orders, take profit
│   ├── market_classifier.py     ← 5-regime detector: ADX/ATR/SMA200/BB; 5-day filter
│   ├── strategy_selector.py     ← Reads profitability matrix; returns grid/dca/sit_out
│   ├── circuit_breaker.py       ← 3 triggers → lock system → Discord
│   ├── dead_mans_switch.py      ← 30h heartbeat; auto-halt
│   ├── confirm.py               ← python -m src.confirm (daily DMS reset)
│   ├── resume.py                ← python -m src.resume --confirm (unlock CB)
│   ├── health_server.py         ← All HTTP endpoints + self-contained dashboard
│   └── test_connection.py       ← Sanity check: price + balance
│
│   ── PLANNED NEW MODULES ──────────────────────────────────────────
│   ├── wide_grid_bot.py         ← Phase 7: HIGH_VOL strategy (4–6% spacing, 50% capital)
│   ├── trailing_dca_bot.py      ← Phase 8: BULL_TREND strategy (trailing stop, state file)
│   └── breakout_bot.py          ← Phase 9: LOW_VOL strategy (squeeze detect + breakout)
│
├── backtests/
│   ├── fetch_history.py         ← 15 months OHLCV, 9 pairs
│   ├── backtest_grid.py         ← Month-by-month grid simulation
│   ├── backtest_dca.py          ← Month-by-month DCA simulation
│   ├── backtest_regime.py       ← Full regime analysis → profitability_matrix.json
│   │
│   ── PLANNED NEW BACKTESTS ───────────────────────────────────────
│   ├── backtest_wide_grid.py    ← Phase 7: 4–6% spacing in HIGH_VOL periods
│   ├── backtest_trailing_dca.py ← Phase 8: trailing stop in BULL_TREND periods
│   └── backtest_breakout.py     ← Phase 9: Bollinger squeeze breakout simulation
│
├── tests/                       ← PLANNED: pytest suite
│   ├── __init__.py
│   ├── test_grid_bot.py
│   ├── test_market_classifier.py
│   ├── test_strategy_selector.py
│   ├── test_circuit_breaker.py
│   ├── test_trailing_dca_bot.py
│   └── test_breakout_bot.py
│
├── deploy/
│   └── adaptive_bot.service     ← systemd unit for VPS
│
└── logs/                        ← Auto-created; gitignored except .gitkeep
    ├── trades.db                ← All fills with side/price/amount/pnl
    ├── profitability_matrix.json
    ├── balance.json             ← USDT free/used/total snapshot (written every ~30s)
    ├── active_SOLUSDT.json      ← Per-pair active orders snapshot (every 30s per bot)
    ├── reconciled_orders.json   ← Orphaned orders (replaced per-pair; cleared on shutdown)
    ├── cancel_queue.json        ← Dashboard cancel requests (thread-safe locked read/write)
    ├── system_state.json        ← CB lock state + reason + source
    ├── heartbeat.json           ← DMS last-confirmed timestamp
    ├── trailing_dca_state.json  ← PLANNED Phase 8: peak price + trailing stop per pair
    ├── adaptive_bot.log
    └── grid_bot.log
```

---

## Key Design Decisions (Non-Negotiable)

**MAKER_FEE_RATE = 0.001 on counter-sells**  
Binance deducts 0.1% fee from the received base asset on a BUY fill. Counter-sell must use `amount × 0.999` or it fails with "insufficient balance". This is verified in live testing — all 9 LINK counter-sells failed before this fix.

**Cancel queue is thread-safe (`_CANCEL_QUEUE_LOCK`)**  
Five pair threads all share `cancel_queue.json`. Without a module-level `threading.Lock()`, threads interleave their reads and writes, causing processed items to reappear. Race condition confirmed in live testnet logs (all -2011 errors were from this).

**CB pre-check before starting workers**  
`adaptive_bot.run()` checks `cb.is_tripped()` BEFORE calling `_classify_all()`. If locked from a previous session, NO workers start. Without this, workers run without portfolio monitoring, DMS, or velocity checks — dangerous for live trading.

**`_write_reconciled()` replaces, never appends**  
Previous sessions' orders accumulate in the file without this rule. On next restart the dashboard shows phantom orders that no longer exist on the exchange. Clear on shutdown, replace per-pair on reconcile.

**`_place_uncovered_sells()` called from both paths**  
Must run after fresh grid placement AND after reconcile. A partial base asset balance (<1 full order qty) would go unattended if only called from the reconcile path.

**Dynamic price precision via `_price_decimals()`**  
At 1% spacing, ADA ($0.237) steps are $0.00237 — below 1 cent. `round(level, 2)` collapses 5 levels to the same price. `_price_decimals()` computes the required dp so each level is distinct.

**`_shutdown = True` is FIRST line of `_initiate_shutdown()`**  
The main loop watchdog runs every 30s. If shutdown flag is set at the END of the method, the watchdog restarts workers mid-shutdown. Confirmed in live logs: SOL was restarted 9 seconds after shutdown was triggered.

**`retry_after` from Discord is in SECONDS, not milliseconds**  
Previous code divided by 1000, making the wait ~0ms. Discord then 429'd every retry immediately. Do not restore the `/1000` division.

**balance.json written when CB is tripped**  
The CB-tripped main loop branch must call `_fetch_and_snapshot_balance()`. Without this, the dashboard Free USDT card shows dashes whenever the system is locked — because the balance write only happened inside `_check_circuit_breaker()` which is skipped when tripped.

**Grid Context Panel uses actual orders, not calculated levels**  
Calculated levels (`fp × (1 + n × spacing%)`) don't string-match actual order prices (e.g., `$82.42 × 1.01 = $83.2442` ≠ `$83.2400`). Always use the actual orders from `_activeOrders` filtered by pair. Use `toFixed(4)` exact matching for the focused row.

---

## Strategy Extension Plan

### Phase 7: Wide-Spacing Grid (HIGH_VOL)

**Problem it solves:** HIGH_VOL has zero approved strategies. 10–20% of crypto market time is left entirely on the table. High volatility = large swings = opportunity for wider-spaced grids.

**Design:**

```python
# src/wide_grid_bot.py — GridBot subclass
class WideGridBot(GridBot):
    """
    Identical to GridBot but with HIGH_VOL-specific parameters:
      - Spacing: 4–6% (captures the large swings)
      - Capital: 50% of normal (reduced risk for violent markets)
      - Stop-loss: 15% (wider — 8% would trigger on noise in HIGH_VOL)
      - Levels: 6 (fewer levels, each earns more per fill)
      
    All existing safety mechanisms preserved:
      - Startup reconcile
      - _place_uncovered_sells
      - Cancel queue (same lock)
      - MAKER_FEE_RATE applied
      - Dynamic price precision
    """
    def __init__(self, pair, capital, exchange):
        super().__init__(pair, capital * 0.5, exchange)  # 50% capital
        self.spacing_pct    = Config.HIGH_VOL_SPACING_PCT / 100  # 0.04–0.06
        self.stop_loss_pct  = Config.HIGH_VOL_STOP_LOSS_PCT / 100  # 0.15
        self.num_levels     = 6
        self.order_size     = self.total_capital / self.num_levels
```

**New env vars:**
```bash
HIGH_VOL_SPACING_PCT=5.0      # 5% spacing
HIGH_VOL_STOP_LOSS_PCT=15.0   # 15% stop-loss
```

**Backtest validation (`backtest_wide_grid.py`):**
- Extract all HIGH_VOL periods from historical data
- Simulate WideGridBot with 5% spacing, 6 levels, 15% stop
- Approval criteria: ≥60% win rate, ≥3 occurrences, avg return > 0%
- Compare: wide grid return vs 0% (sitting out) — must beat 0%
- Expected: ~4.8% net per round trip (5% gross − 0.2% fees)

**UI changes:**
- Active Open Orders: "WIDE GRID" badge (purple, distinct from green "GRID")
- Grid Context Panel: shows wider spacing levels, "HIGH_VOL Mode" header
- Per-Pair Fill History: includes wide grid cycles in history

**Edge cases:**
| Case | Handling |
|------|----------|
| Flash crash blows all 6 levels in one candle | Stop-loss exchange order (stop-limit), not just bot polling |
| HIGH_VOL → RANGING transition | Cancel all wide grid orders, deploy standard grid |
| HIGH_VOL lasts < 2 days | Wide grid may never fill — minimum 2-day confirmation before switching |
| Pair not approved for standard grid | May still approve for wide grid (different backtest criteria) |

---

### Phase 8: Trailing DCA (BULL_TREND)

**Problem it solves:** BULL_TREND sits out entirely. The most profitable crypto market phase (100–400% typical gains) generates zero income for the current bot. Standard DCA sells too early (at 1.5% take-profit) — misses the full trend.

**Design:**

```python
# src/trailing_dca_bot.py
class TrailingDCABot:
    """
    State machine: IDLE → ENTERING → POSITION_HELD → TRAILING → EXITED
    
    Lifecycle:
      1. BULL_TREND confirmed → enter base position
      2. Price dips → add safety orders (max 3), lower avg entry
      3. Price rises above avg entry → begin trailing (stop = peak × 0.85)
      4. Peak rises → stop rises (never lowers)
      5. Price < trailing_stop OR regime != BULL_TREND → market sell all
    
    Persistence: logs/trailing_dca_state.json
      {
        "pair": "LINK/USDT",
        "state": "TRAILING",
        "base_price": 12.50,
        "avg_entry": 12.10,
        "peak_price": 15.80,
        "trailing_stop": 13.43,   # peak × 0.85
        "total_qty": 10.82,
        "safety_orders_placed": 2,
        "entered_at": "2026-06-01T10:00:00"
      }
    """
    
    TRAILING_STOP_PCT = 0.15   # 15% below peak
    MAX_SAFETY_ORDERS = 3
    SAFETY_DROP_PCT   = 0.03   # add safety order on 3% dip
    CHECK_INTERVAL    = 300    # check trailing stop every 5 min
```

**Restart recovery (critical):**
```python
def _recover_from_state_file(self):
    """On restart: verify position exists, restore peak/stop, continue trailing."""
    state = json.load(open("logs/trailing_dca_state.json"))
    if state["state"] == "EXITED":
        return  # nothing to do
    
    # Verify position still on exchange
    balance = self.exchange.fetch_balance()
    qty = balance[base]["total"]
    if qty < state["total_qty"] * 0.95:
        # Position was externally closed — update state
        self._mark_exited("Position closed externally")
        return
    
    # Restore from state
    self.avg_entry    = state["avg_entry"]
    self.peak_price   = state["peak_price"]
    self.trailing_stop = state["trailing_stop"]
    self.state        = state["state"]
    logger.info(f"Trailing DCA recovered: peak=${self.peak_price}, stop=${self.trailing_stop}")
```

**Regime-change exit (highest priority):**
```python
# In adaptive_bot.py _switch_pair():
if new_strategy != "trailing_dca" and current_strategy == "trailing_dca":
    # Regime changed away from BULL_TREND → close position immediately
    worker.bot.exit_at_market(reason="regime_change")
```

**New env vars:**
```bash
TRAILING_DCA_CAPITAL=125      # Growth bucket capital (separate from grid capital)
TRAILING_DCA_TRAILING_PCT=15  # Stop trails 15% below peak
TRAILING_DCA_SAFETY_PCT=3     # Add safety order on 3% dip
TRAILING_DCA_MAX_SAFETY=3     # Max 3 safety orders
```

**UI — Dashboard additions:**
- New "Trailing Position" section (shows when active):
  - Pair, current price, avg entry, unrealised P&L %
  - Peak price with date
  - Trailing stop level with coloured distance indicator
    - Green: >10% above stop (safe)
    - Yellow: 5–10% above stop (approaching)
    - Red: <5% above stop (near exit)
- Round Trip History: "TRAILING DCA" filter pill, shows full lifecycle card (entry → peak → exit)

**Edge cases:**
| Case | Handling |
|------|----------|
| Regime flips BULL → BEAR mid-position | Immediate market sell regardless of P&L |
| Safety orders fire 3× in 10 min (flash dip) | Rate-limit: max 1 safety order per 15 min |
| State file corrupted/missing on restart | Close all positions safely, start clean |
| Exchange API down during trailing stop trigger | Retry every 30s, loud Discord alert |
| Peak price from yesterday — was it real? | State file is source of truth; verify against exchange candle data on restart |
| Two pairs both in BULL_TREND | Max 2 trailing DCA positions simultaneously |
| Position value < $50 after fees | Don't add more safety orders — too diluted |

---

### Phase 9: Breakout Positioning (LOW_VOL)

**Problem it solves:** LOW_VOL (Bollinger Band squeeze) precedes explosive moves. Currently sits out entirely. A small pre-position can be placed with defined risk, then sized up when direction confirms.

**Design:**

```python
# src/breakout_bot.py
class BreakoutBot:
    """
    Bollinger Squeeze → pre-position → wait for direction → size up or exit.
    
    Squeeze detection:
      ATR(14) < 50% of its 6-month (126-day) rolling average
      AND Bollinger Band width < historical 10th percentile
    
    State machine:
      WATCHING      → squeeze detected, no position
      PRE_POSITIONED → 5% capital bought, stop at -8%
      BULL_CONFIRMED → closed above upper BB, added to 20% capital, trail stop
      BEAR_CONFIRMED → closed below lower BB, exit pre-position (small loss)
      EXITED         → position closed, watching again
    """
    
    PRE_POSITION_PCT  = 0.05   # 5% of Growth capital as pre-position
    FULL_POSITION_PCT = 0.20   # 20% of Growth capital on confirmation
    STOP_LOSS_PCT     = 0.08   # 8% stop on pre-position
    TRAIL_STOP_PCT    = 0.12   # 12% trail on confirmed bull
    MAX_WATCH_DAYS    = 21     # Close pre-position if no confirmation in 21 days
```

**Squeeze detection:**
```python
def _is_squeeze(self, pair: str) -> bool:
    """ATR(14) < 50% of its 6-month rolling average."""
    ohlcv = self.exchange.fetch_ohlcv(pair, '1d', limit=140)
    df = pd.DataFrame(ohlcv, columns=['ts','o','h','l','c','v'])
    df['atr'] = df['h'].rolling(14).max() - df['l'].rolling(14).min()
    current_atr = df['atr'].iloc[-1]
    avg_atr_6m  = df['atr'].iloc[-126:].mean()
    return current_atr < avg_atr_6m * 0.50
```

**New env vars:**
```bash
BREAKOUT_CAPITAL=50           # From Growth bucket (small, defined risk)
BREAKOUT_STOP_PCT=8.0
BREAKOUT_TRAIL_PCT=12.0
BREAKOUT_MAX_WATCH_DAYS=21
```

**Edge cases:**
| Case | Handling |
|------|----------|
| False breakout: poke above band then reversal | Stop-loss at 8% catches it; max loss = 5% × 8% = 0.4% of Growth |
| Multiple pairs squeezing simultaneously | Max 2 pre-positions at once |
| Squeeze resolves with no volume | No confirmation → no additional capital |
| Regime transitions to HIGH_VOL during squeeze | Close pre-position, defer to wide grid |
| 21-day timeout with no breakout | Exit pre-position at market, reset to WATCHING |
| Breakout direction = bear (price drops) | In spot: exit pre-position (no shorting), switch to grid-ready |

---

## Testing Plan

### Level 1 — Unit Tests (`tests/`)

**`test_grid_bot.py`:**
```python
def test_calculate_grid_standard():
    """10 levels at 1% spacing — verify all levels are distinct."""

def test_calculate_grid_low_price():
    """ADA @ $0.237 — verify 4dp used, no collapsed levels."""

def test_price_decimals_all_ranges():
    """$0.20, $1.34, $9.15, $82, $672 — each gets correct dp."""

def test_counter_sell_fee_deduction():
    """Buy $10 of LINK @ $9.15 → counter-sell amount = 1.0934 × 0.999."""

def test_has_inventory_true():
    """base_free = 1.09 LINK, amount_per_order = 1.08 → True."""

def test_has_inventory_false_partial():
    """base_free = 1.01 LINK, amount_per_order = 1.09 → False (not enough for full order)."""

def test_reconcile_clears_pair():
    """_write_reconciled() with new orders replaces old entries for same pair."""

def test_cancel_queue_thread_safe():
    """5 threads write/read cancel queue simultaneously — no items lost or duplicated."""
```

**`test_market_classifier.py`:**
```python
def test_bull_trend_detection():
    """Synthetic OHLCV: price rising 5%/week, ADX=35, above SMA200 → BULL_TREND."""

def test_ranging_detection():
    """Synthetic: price flat ±3%, ADX=18 → RANGING."""

def test_5_day_confirmation():
    """Signal fires on day 1, confirm = False until day 5."""

def test_high_vol_detection():
    """ATR 3× normal average → HIGH_VOL."""

def test_low_vol_squeeze():
    """ATR < 50% of 6m average → LOW_VOL."""
```

**`test_trailing_dca_bot.py`:**
```python
def test_state_machine_transitions():
    """IDLE → ENTERING → POSITION_HELD → TRAILING → EXITED."""

def test_trailing_stop_raises():
    """Peak rises from $100 → $110 → stop raises from $85 → $93.50."""

def test_trailing_stop_never_lowers():
    """Peak at $110, price dips to $105 → peak stays $110, stop stays $93.50."""

def test_regime_change_exits():
    """Regime flips from BULL_TREND → RANGING → position closes at market."""

def test_restart_recovery():
    """State file exists with TRAILING state → bot picks up from peak_price."""

def test_corrupted_state_file():
    """State file is invalid JSON → close all positions safely, reset to IDLE."""
```

**`test_breakout_bot.py`:**
```python
def test_squeeze_detection():
    """ATR = 0.45× 6m average → squeeze True."""

def test_bull_confirmation():
    """Price closes above upper BB → state = BULL_CONFIRMED, add capital."""

def test_bear_confirmation():
    """Price closes below lower BB → state = BEAR_CONFIRMED, exit pre-position."""

def test_timeout_exit():
    """21 days in PRE_POSITIONED with no confirmation → exit at market."""

def test_max_concurrent_positions():
    """3 pairs squeezing simultaneously → only 2 pre-positions opened."""
```

### Level 2 — Backtest Validation

Before any new strategy is approved for live, it must pass:

| Criterion | Threshold | Why |
|-----------|-----------|-----|
| Win rate | ≥60% of periods profitable | Edge must be proven statistically |
| Occurrences | ≥3 qualifying periods in 15 months | Not a 1-time lucky result |
| Average return | >0% per period | Better than sitting out |
| Sharpe ratio | >0.5 | Reward per unit of risk is positive |
| Max drawdown | <25% | Position sizing must survive worst case |

Run validation:
```bash
python3 -m backtests.backtest_wide_grid      # Phase 7
python3 -m backtests.backtest_trailing_dca   # Phase 8
python3 -m backtests.backtest_breakout       # Phase 9
```

### Level 3 — Testnet Dry Run (per strategy)

For each new strategy before live:
1. Run on Binance testnet for minimum **48 hours**
2. Verify: reconcile works on restart mid-strategy
3. Verify: Discord notifications correct for all events
4. Verify: Dashboard shows correct data (new panel/cards)
5. Verify: Trailing stop fires correctly (simulate price movement)
6. Verify: Regime change triggers clean exit
7. Verify: CB and DMS still arm and halt correctly with new strategy running

### Level 4 — Live Integration Tests (production)

First week on live (real money, minimum capital):
1. Deploy LINK + SOL grid only at $100/pair
2. Verify: fills logging correctly with real fees
3. Verify: P&L tracking accurate (compare to Binance trade history)
4. Verify: Balance snapshot matches exchange balance within 1%
5. Verify: DMS automation firing daily (cron-job.org)
6. Verify: CB would trigger correctly (test with --status before each session)

---

## UI/UX Extension Plan

### Phase 7 Dashboard (Wide Grid)

**Active Open Orders panel:**
- Add `strategy_type` field to `logs/active_*.json` snapshot
- "WIDE GRID" badge: purple background (vs "GRID" green badge)
- Values per order 4–6× larger — tooltip explains HIGH_VOL sizing

**Grid Context Panel:**
- "HIGH_VOL Mode" header with orange accent
- Shows wider level spacing in the price ladder
- "Sells pending" total changes colour: orange in HIGH_VOL (not default muted)

**Per-Pair Fill History:**
- Add "WIDE" indicator on rows from wide_grid cycles
- Round Trip History modal: "WIDE GRID" filter pill

### Phase 8 Dashboard (Trailing DCA)

**New "Active Trailing Position" section** (appears when state = TRAILING):
```
TRAILING DCA POSITION
────────────────────────────────────────────────────
LINK/USDT       Entered: $11.20 avg    Qty: 10.82
Current: $14.50               Unrealised: +$35.83 (+29.3%)
Peak: $15.80 (2 days ago)     Trailing Stop: $13.43
Distance to stop: 7.4% ▓▓▓▓▓▓▓░░░░░░░░                [GREEN]
────────────────────────────────────────────────────
```

**Distance bar colour coding:**
- Green (>10%): safe, trailing comfortably
- Yellow (5–10%): approaching — watch closely
- Red (<5%): near exit — price may trigger soon

**Round Trip History — Trailing DCA card:**
```
LINK/USDT — Trailing DCA              TRAILING
Entered: $11.20 avg (2 safety orders)
Peak: $15.80 · Exit: $13.50 (trailing stop triggered)
+$24.84 (+22.2%)    · 23 days held
```

### Phase 9 Dashboard (Breakout)

**Status indicator in header** (when squeeze active on any pair):
- Small yellow "🔍 Squeeze detected: LINK/USDT" badge in status row

**New "Breakout Watch" panel:**
```
BOLLINGER SQUEEZE DETECTED
LINK/USDT — Band width at 6-month low
Pre-position: $62 @ $12.40 · Stop: $11.41
Direction confirmation: price must close above $12.95 (bull) or below $11.85 (bear)
Watching: 3 of 21 days
```

### Mobile Responsiveness (all new features)

All new panels and sections follow the same `@media (max-width: 640px)` rules:
- Trailing position: stack distance bar below values (not side-by-side)
- Breakout watch: compact single-column layout
- Wide grid active orders: same table scroll as current
- All new modals: bottom-sheet pattern on mobile (same as shutdown modal)

---

## HTTP API Reference (Current + Planned)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | System health: CB, DMS, strategy matrix |
| GET | `/status` | Full JSON status |
| GET | `/dashboard` | Trader dashboard HTML |
| GET | `/api/trades` | Trade stats + active orders + balance |
| GET | `/api/round_trips` | All completed cycles with inferred buy price |
| POST | `/confirm` | Reset DMS timer |
| POST | `/cancel_orphan` | Queue order for cancellation/market-sell |
| POST | `/shutdown` | Emergency stop |
| GET | `/api/trailing_position` | **PLANNED Phase 8**: current trailing DCA state |
| GET | `/api/breakout_status` | **PLANNED Phase 9**: squeeze status per pair |

---

## Safety Layers (Full System)

| Layer | Trigger | Action | Covers |
|-------|---------|--------|--------|
| Per-bot stop loss | 8% drop from grid entry | Cancel all for that pair | Grid, Wide Grid |
| Wide grid stop loss | 15% drop (wider) | Cancel all for that pair | Wide Grid only |
| Trailing stop | Price < peak × 0.85 | Market sell all trailing DCA position | Trailing DCA |
| Breakout stop | 8% below pre-position entry | Market sell pre-position | Breakout |
| Circuit breaker | 15% portfolio drawdown / 8%/candle velocity / 5 API errors | Lock system, cancel ALL | All strategies |
| Dead man's switch | 30h without POST /confirm | Halt all bots | All strategies |
| Regime-change exit | Strategy no longer approved for detected regime | Stop current bot, start approved strategy | All strategies |

**New safety considerations for Phase 8–9:**
- Trailing DCA position must close on ANY regime change from BULL_TREND (not just CB)
- Breakout pre-position must close if regime changes to HIGH_VOL (different strategy takes over)
- Both new bots must register with the CB (their capital counts toward drawdown calculation)
- DMS halt must market-sell trailing DCA positions (not just cancel orders)

---

## Backtesting Results (Current)

Period: 15 months (Nov 2024 – May 2026)

| Regime | Grid | DCA | Wide Grid | Trailing DCA | Breakout |
|--------|------|-----|-----------|--------------|----------|
| BULL_TREND | ❌ | ⚠️ | ❌ | 🔬 TBD | ❌ |
| BEAR_TREND | ✅ avg 8.8% | ❌ | 🔬 TBD | ❌ | ❌ |
| RANGING | ✅ avg 12.4% | ✅ LINK+ADA | ❌ | ❌ | ❌ |
| HIGH_VOL | ❌ | ❌ | 🔬 TBD | ❌ | ❌ |
| LOW_VOL | ❌ | ❌ | ❌ | ❌ | 🔬 TBD |

🔬 = backtest planned but not yet run

---

## Capital Allocation Framework

**Three buckets (total portfolio = $X):**

| Bucket | % | Current ($500 example) | Purpose |
|--------|---|----------------------|---------|
| Core (Grid/DCA) | 60% | $300 | Steady income from oscillations |
| Growth (Trailing/Breakout) | 25% | $125 | Capture large directional moves |
| Reserve | 15% | $75 | Emergency fund, never deployed |

**Strategy capital limits:**
```bash
GRID_TOTAL_CAPITAL=250          # Per pair (Core bucket)
TRAILING_DCA_CAPITAL=125        # Total Growth bucket for trailing DCA
BREAKOUT_CAPITAL=50             # Pre-position from Growth bucket
HIGH_VOL_CAPITAL_PCT=0.50       # 50% of normal grid capital for wide grid
TOTAL_CAPITAL_LIMIT=500         # Hard cap — CB never allows > this deployed
```

---

## Before Going Live Checklist

- [ ] Widen `GRID_SPACING_PCT` to 1.5–2.0% (covers 0.2% round-trip fee)
- [ ] Enable BNB fee payment on Binance (reduces to 0.15% round-trip)
- [ ] Replace approx P&L with `exchange.fetch_my_trades()` for real net P&L
- [ ] IP-whitelist VPS IP on Binance API key (no withdrawal permissions)
- [ ] DMS automation confirmed running (cron-job.org POST /confirm daily)
- [ ] Start with LINK + SOL only (highest RANGING returns, different sectors)
- [ ] $250/pair → $25/order (comfortably above $10 Binance minimum)
- [ ] Deploy VPS: Hetzner CX21 (2vCPU, 4GB — handles all strategies)
- [ ] Monitor first 48h manually via dashboard
- [ ] All three safety layers verified (stop-loss, CB, DMS) before any capital increase

---

## Environment Variables (Full Reference)

```bash
# Exchange
EXCHANGE=binance
ENV=testnet            # testnet | live
BINANCE_API_KEY=...
BINANCE_API_SECRET=...

# Grid strategy (Core bucket)
GRID_PAIR=BTC/USDT     # default (unused in adaptive mode)
GRID_TOTAL_CAPITAL=250 # per pair — use 1.5–2% spacing on live
GRID_LEVELS=10
GRID_SPACING_PCT=1.5   # MUST be ≥1.5 on live (covers 0.2% fees)
GRID_STOP_LOSS_PCT=8.0

# DCA strategy (Core bucket)
DCA_PAIR=ETH/USDT
DCA_TOTAL_CAPITAL=100
DCA_BASE_ORDER_PCT=20
DCA_SAFETY_ORDER_PCT=10
DCA_MAX_SAFETY_ORDERS=5
DCA_TAKE_PROFIT_PCT=2.0
DCA_PRICE_DROP_PCT=2.5

# Wide Grid strategy — Phase 7 (Core bucket, HIGH_VOL)
HIGH_VOL_SPACING_PCT=5.0
HIGH_VOL_STOP_LOSS_PCT=15.0

# Trailing DCA strategy — Phase 8 (Growth bucket, BULL_TREND)
TRAILING_DCA_CAPITAL=125
TRAILING_DCA_TRAILING_PCT=15
TRAILING_DCA_SAFETY_PCT=3
TRAILING_DCA_MAX_SAFETY=3

# Breakout strategy — Phase 9 (Growth bucket, LOW_VOL)
BREAKOUT_CAPITAL=50
BREAKOUT_STOP_PCT=8.0
BREAKOUT_TRAIL_PCT=12.0
BREAKOUT_MAX_WATCH_DAYS=21

# Safety
CB_DRAWDOWN_PCT=15.0
CB_VELOCITY_PCT=8.0
CB_API_ERROR_COUNT=5
DMS_HALT_HOURS=30
TOTAL_CAPITAL_LIMIT=500  # Hard cap across all strategies

# Notifications
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# Integration
MONITOR_URL=https://your-monitor.railway.app

# Railway
PORT=8080
```
