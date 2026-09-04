# KRX Toss MFT/LFT Trading Platform

Long-only Korean spot swing system on the [Toss Securities Open API](https://developers.tossinvest.com/). It holds names for hours to a few days. It is **not** high-frequency trading: Toss is REST-only, candles are `1m`/`1d`, and 2026 KRX sell tax (~0.20%) makes sub-1% round-trips negative EV.

This is software, not investment advice. Past academic patterns in foreign flow / overnight reversal may not persist.

## What it trades

Foreign- and institution-flow confirmed momentum on liquid KOSPI/KOSDAQ common shares:

1. Seed a watchlist from market turnover rankings (avoids scanning the whole book).
2. Require 2–3 session net buying by foreigners **or** institutions, price above the 20-day MA, and no 3-day extension above ~13%.
3. Skip new entries when KOSPI fell more than ~8% (crash halt). On a milder down day (~1.2%+), buy liquid names that sold off for a next-session bounce.
4. Place **LIMIT buys after 09:15 KST** (after open-auction noise and the Toss `ORDER_INFO` 09:00–09:10 throttle).
5. Attach an OCO take-profit / stop. Overlay polls **open positions only** on 1-minute bars for VI / warning / near-limit-up exits.

Round-trip cost model: live `GET /api/v1/commissions` plus 2026 securities transaction tax on sells (KOSPI/KOSDAQ 0.20%). Fallback commission is 0.015%.

## Setup

Python 3.12+.

```powershell
cd C:\Users\nasan\krx-toss-trading
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

1. Copy `config/creds.csv.example` to `config/creds.csv` and put your `client_id` / `client_secret` there. The file is gitignored.
2. In Toss WTS → Settings → Open API, allowlist the public IP this machine uses. Unlisted IPs get HTTP 403.
3. Keep `dry_run: true` in `config/settings.yaml` until you have scanned and backtested.

## Commands

```powershell
krx-toss scan          # after-close universe + signals
krx-toss paper         # dry-run LIMIT entries from last scan
krx-toss overlay       # 1m overlay on holdings
krx-toss eod           # time-stop flatten
krx-toss fetch-cache   # parquet cache of candles/flow
krx-toss backtest --nav 100000000
krx-toss status
krx-toss run --once    # one scheduler pass
krx-toss live --i-understand-the-risk   # real orders; also set dry_run: false
krx-toss agents --once --no-llm   # post-close PNL brief + agent handoffs (no Cursor SDK)
krx-toss agents --once            # supervisor pass (needs CURSOR_API_KEY + pip install -e ".[agents]")
```

Live trading requires **both** `dry_run: false` and `--i-understand-the-risk`.

Step-by-step paper/live runbook, scheduler clock, and every `config/settings.yaml` / `config/strategy.yaml` knob: [docs/TRADING.md](docs/TRADING.md).

## Rate limits (client × group)

See [Toss overview](https://openapi.tossinvest.com/openapi-docs/overview.md). The client token-buckets each group and honors `Retry-After`. It never retries `POST /orders` (double-fill risk); every create carries `clientOrderId`.

## Tests

```powershell
pytest
```



  Four agents are in place, with a safer pipeline than “researcher edits live code 24/7.”

  What was created

  ┌─────────────────────┬─────────────────────────────────────┬──────────────────────────────────────────────────────────────────────┐
  │ Agent               │ Skill                               │ Role                                                                 │
  ├─────────────────────┼─────────────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
  │ PNL analysis        │ .cursor/skills/pnl-analysis/        │ After 15:30 KST, writes data/agents/pnl_brief.json + queues research │
  ├─────────────────────┼─────────────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
  │ Strategy researcher │ .cursor/skills/strategy-researcher/ │ Searches alpha, prefers strategy.yaml, queues QA + Telegram          │
  ├─────────────────────┼─────────────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
  │ QA                  │ .cursor/skills/qa-agent/            │ pytest/backtest gate, bugfix in-scope, approve/reject                │
  ├─────────────────────┼─────────────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
  │ Trading agent       │ .cursor/skills/trading-agent/       │ Supervisor; never auto-flips dry_run                                 │
  └─────────────────────┴─────────────────────────────────────┴──────────────────────────────────────────────────────────────────────┘

  Orchestrator: krx-toss agents (src/krx_toss/agents/).

  Better design (baked in)

  1. Config before code — most “alpha” should be strategy.yaml sweeps, not random Python rewrites.
  2. No continuous 24h LLM burn — queue-driven after PNL + overnight cycles (not empty loops).
  3. Backtest gate + cost awareness — edge must clear ~0.20% sell tax + fees.
  4. No auto-live — QA approved still needs human ack; kill switch / creds / dry_run are forbidden.
  5. Separate from krx-toss run — trading loop stays fast; agents won’t block overlay/fills.

  How to run

  # Deterministic brief + handoffs (no Cursor API needed)
  krx-toss agents --once --no-llm --role pnl
  # Full supervisor (Cursor SDK)
  pip install -e ".[agents]"
  $env:CURSOR_API_KEY="cursor_..."
  krx-toss agents --once