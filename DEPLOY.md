# Railway Deployment Guide

## Prerequisites
- GitHub account
- Railway account (railway.app — free tier is enough for testnet)
- All `.env` values ready

---

## Step 1 — Push code to GitHub

```bash
cd /Users/apple/Documents/Claude/Projects/Crypto/01-trading-bot

# Initialise git (if not already done)
git init
git add .
git commit -m "Initial trading bot"

# Create a NEW PRIVATE repo on github.com, then:
git remote add origin https://github.com/YOUR_USERNAME/crypto-trading-bot.git
git branch -M main
git push -u origin main
```

> ⚠️ The `.gitignore` already excludes `.env`, `logs/*`, and `backtests/data/`.
> Never push your `.env` file.

---

## Step 2 — Create Railway project

1. Go to [railway.app](https://railway.app) → **New Project**
2. Select **Deploy from GitHub repo**
3. Connect your GitHub account → select `crypto-trading-bot`
4. Railway auto-detects Python via `.python-version` and `requirements.txt`

---

## Step 3 — Set environment variables

In Railway dashboard → your service → **Variables** tab, add all of these:

| Variable | Value |
|---|---|
| `ENV` | `testnet` |
| `BINANCE_TESTNET_API_KEY` | your testnet API key |
| `BINANCE_TESTNET_SECRET` | your testnet secret |
| `DISCORD_WEBHOOK_URL` | your Discord webhook URL |
| `GRID_TOTAL_CAPITAL` | `100` |
| `GRID_PAIR` | `BTC/USDT` |
| `GRID_LEVELS` | `10` |
| `GRID_SPACING_PCT` | `1.0` |
| `GRID_STOP_LOSS_PCT` | `10.0` |
| `DCA_PAIR` | `ETH/USDT` |
| `DCA_BASE_ORDER` | `50` |
| `DCA_SAFETY_ORDER` | `30` |
| `DCA_MAX_SAFETY_ORDERS` | `5` |
| `DCA_PRICE_DROP_PCT` | `2.5` |
| `DCA_TAKE_PROFIT_PCT` | `1.5` |

---

## Step 4 — Add persistent volume for logs

The bot stores SQLite trades, circuit breaker state, and heartbeat files in `logs/`.
Without a persistent volume these reset on every redeploy.

1. Railway dashboard → your service → **Volumes** tab
2. **Add Volume**
   - Mount path: `/app/logs`
   - Size: 1 GB (more than enough)
3. Save → Railway will redeploy automatically

---

## Step 5 — Upload the profitability matrix

The `logs/profitability_matrix.json` was built locally by the backtest.
You need to upload it to the Railway volume before the bot can trade.

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Link to your project
railway link

# Upload the matrix
railway run --service crypto-trading-bot \
  cp /dev/stdin /app/logs/profitability_matrix.json \
  < logs/profitability_matrix.json
```

Or use the Railway shell (dashboard → service → **Shell** tab):
```bash
# Paste the matrix JSON directly into the shell
cat > /app/logs/profitability_matrix.json << 'EOF'
{ ... paste content of logs/profitability_matrix.json here ... }
EOF
```

---

## Step 6 — Deploy

Railway will auto-deploy on every `git push`. To trigger manually:

Railway dashboard → **Deploy** button

Watch logs in **Logs** tab. You should see:

```
Health server listening on port XXXX — GET /health | POST /confirm
Adaptive Bot starting...
Strategy matrix loaded — 9 pairs
Running regime classification for all pairs...
Started GRID on SOL/USDT
...
```

---

## Daily operation — Keep dead man's switch alive

The bot auto-halts if it doesn't receive a daily confirmation within 30 hours.

**Option A — HTTP (recommended for Railway):**
```bash
curl -X POST https://YOUR-RAILWAY-URL.railway.app/confirm
```

**Option B — Railway CLI:**
```bash
railway run python3 -m src.confirm
```

You can automate Option A with a free cron service like [cron-job.org](https://cron-job.org):
- URL: `POST https://YOUR-RAILWAY-URL.railway.app/confirm`
- Schedule: once per day

---

## Health check

```bash
curl https://YOUR-RAILWAY-URL.railway.app/health
```

Returns:
```json
{
  "ok": true,
  "env": "testnet",
  "circuit_breaker": { "tripped": false },
  "dead_mans_switch": { "alive": true, "hours_until_halt": 28.5 },
  "strategy_matrix": { "loaded": true, "pairs": 9 }
}
```

`"ok": false` means circuit breaker tripped or DMS expired.

---

## Circuit breaker recovery

If the circuit breaker trips:
```bash
railway run python3 -m src.resume --confirm
```

---

## Going live (real money)

When ready to switch from testnet to live:
1. Generate live Binance API keys (HMAC, IP-whitelist Railway's egress IP)
2. In Railway Variables: change `ENV` to `live`
3. Add `BINANCE_API_KEY` and `BINANCE_SECRET`
4. Redeploy

> 💡 Railway's egress IP is fixed per project — whitelist it in Binance API settings for security.
