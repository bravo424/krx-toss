# Trading operations

How to set up this platform, change what it trades, and run it in paper or live mode.

This is software, not investment advice. Keep `dry_run: true` until you have scanned, backtested, and watched a paper session.

## What it does

Long-only KOSPI/KOSDAQ swing trades on the Toss Open API. It does **not** scan every listed name and there is **no static ticker list**.

Each after-close scan:

1. Pulls turnover rankings (amount + volume, 1-day / 1-week / 1-month, top 100 each).
2. Merges unique names and keeps a watchlist of about 180 liquid common shares.
3. Drops 정리매매, 투자경고, 단기과열, 투자위험, warrants, and non-ACTIVE names.
4. Accepts names with foreign **and** institution net buying, price above the 20-day MA, and no 3-day extension above ~13%.
5. Skips **all** new entries if KOSPI fell more than ~2% the prior day.

The next session it places LIMIT buys after 09:15 KST (max 8 names), attaches an OCO take-profit / stop, and overlays **open positions only** on 1-minute bars.

## One-time setup

Python 3.12+.

```powershell
cd C:\Users\nasan\krx-toss-trading
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

1. Put Toss Open API credentials in `config/creds.csv` (gitignored):

   ```text
   client_id,client_secret
   YOUR_CLIENT_ID,YOUR_CLIENT_SECRET
   ```

   That is the only secret file. You do **not** put an account number or password there.

2. In Toss WTS → Settings → Open API, allowlist this machine’s **public IP**. Unlisted IPs get HTTP 403.

3. Leave `account_seq: null` in `config/settings.yaml` unless you have more than one brokerage account. Null means “use the first `BROKERAGE` account from `GET /api/v1/accounts`”.

4. Leave `dry_run: true` until you are ready for real orders.

## Config files

| File | Purpose |
| --- | --- |
| `config/creds.csv` | Toss `client_id` / `client_secret` only |
| `config/settings.yaml` | Environment: dry-run, account, paths, poll interval |
| `config/strategy.yaml` | What to trade: universe, signals, entry, exit, risk, cost |

Restart the CLI after edits. YAML is read at process start; a running `krx-toss run` loop will not pick up changes until you stop and start it.

### `config/settings.yaml`

| Key | What it does |
| --- | --- |
| `dry_run` | `true` = log orders and fill the local blotter, no Toss `POST /orders`. `false` = real orders for `run` / `overlay` / `eod` / `live`. |
| `account_seq` | Toss account sequence. `null` auto-picks the first brokerage account. |
| `base_url` | Toss Open API host. Keep `https://openapi.tossinvest.com`. |
| `timezone` | Used for logs and session clock. Keep `Asia/Seoul`. |
| `poll.overlay_seconds` | Sleep between scheduler loops (default 60). Overlay itself runs once per loop during the session. |
| `paths.cache_dir` | Parquet cache of candles / flow / credit. |
| `paths.blotter_db` | Local SQLite of orders, fills, positions. |
| `paths.kill_switch` | If this file exists and `tripped` is true, new orders are blocked. |
| `paths.signals_path` | Last scan output. Entries read this file; they do not rescan at the open. |

### `config/strategy.yaml`

Change these when you want different names, fewer trades, wider stops, etc. Values are decimals unless noted (so `0.06` is 6%).

#### Universe — which names are even considered

There is no hand-maintained pool. These knobs size the **ranked watchlist**.

| Key | Default | Effect |
| --- | --- | --- |
| `ranking_type` | `MARKET_TRADING_AMOUNT` | Fallback if `ranking_types` is omitted. |
| `ranking_types` | amount + volume | Merge several ranking boards (Toss still caps each at 100). |
| `ranking_durations` | `[1d, 1w, 1m]` | Merge 1-day, 1-week, and 1-month lists. |
| `ranking_count` | `100` | Per-request cap (Toss max is 100; unique merge can be larger). |
| `watchlist_size` | `180` | Max names that get candles/flow/signal checks. |
| `markets` | `[KOSPI, KOSDAQ]` | Drop other venues. |
| `common_share_only` | `true` | Drop preferred shares when the API flags them. |
| `exclude_investment_caution` | `true` | Ask Toss rankings to exclude 투자주의. |
| `blocked_warning_types` | 정리매매 / 과열 / 경고 / 위험 / warrants | Skip at universe build **and** flatten if they appear on a holding. |

Raise `watchlist_size` only if you accept more API calls (warnings are 5 TPS). Lower it to scan fewer names.

#### Signal — who gets a buy ticket

Applied only to the watchlist. Accepted names are sorted by foreign + institution net buying.

| Key | Default | Effect |
| --- | --- | --- |
| `flow_lookback_sessions` | `3` | Sum of foreign / institution net over this many sessions. Both must be > 0. |
| `ma_window` | `20` | Close must be above this SMA. |
| `min_20d_return` | `0.0` | Require at least this 20-session return. Raise (e.g. `0.03`) for stronger trends. |
| `max_3d_return` | `0.13` | Reject if 3-session return is above this (already extended). |
| `credit_lookback` | `20` | Window for 융자 balance vs its average. |
| `max_credit_vs_avg` | `1.5` | Reject if margin balance is this many times its average. |
| `kospi_skip_1d_return` | `-0.02` | If KOSPI 1-day return is below this, **every** name is rejected (`kospi_risk_off`). |

#### Entry — when and how LIMIT buys are sent

| Key | Default | Effect |
| --- | --- | --- |
| `after_kst` | `"09:15"` | Scheduler will not place the daily entry batch before this KST time. |
| `no_new_orders_until` | `"09:15"` | Hard block inside `place_entries` even if you run `paper` / `live` by hand. |
| `limit_offset_ticks` | `0` | Shift the LIMIT price by this many ticks (`0` = last price, rounded to tick). Use a small positive number to pay up for a fill. |
| `time_in_force` | `DAY` | Documented intent; live orders currently send `DAY`. |

Entries also skip if the kill switch is tripped, if `signals.json` is missing, if the name is already held, or if `max_positions` is full.

#### Exit — how positions leave

| Key | Default | Effect |
| --- | --- | --- |
| `take_profit` | `0.06` | OCO LIMIT sell at entry × (1 + this). |
| `stop_loss` | `0.04` | OCO stop and per-name risk sizing. |
| `time_stop_sessions` | `5` | EOD job flattens names held this many sessions. |
| `oco_expire_days` | `7` | Intended OCO lifetime. |
| `flatten_near_limit_pct` | `0.02` | Overlay market-sells if last price is within 2% of the upper limit. |
| `overlay_vi_flatten` | `true` | Overlay flattens on VI warnings. |

Overlay also flattens if a holding picks up a `blocked_warning_types` flag.

#### Risk — size and circuit breakers

| Key | Default | Effect |
| --- | --- | --- |
| `max_positions` | `8` | Cap on concurrent names. |
| `position_nav_pct` | `0.10` | Target notional per name as a fraction of NAV. |
| `cash_buffer_pct` | `0.20` | Keep this fraction of NAV unspent. |
| `per_name_risk_pct` | `0.02` | Size so a stop-loss is about this fraction of NAV. |
| `daily_loss_kill_pct` | `0.02` | EOD trips the kill switch if realized loss ≥ this fraction of NAV. |
| `max_notional_per_name` | `5000000000` | Hard KRW cap per name. |
| `kospi_ownership_pct` / `kosdaq_ownership_pct` | `0.01` / `0.02` | Cap vs shares outstanding when the API provides it. |
| `high_value_threshold` | `100000000` | Sets Toss `confirmHighValueOrder` when notional ≥ 1억. |

In dry-run, NAV is at least 100,000,000 KRW so paper sizing still works with an empty account. Live NAV is buying power plus blotter positions.

#### Cost — fees used in sizing / backtest

Live trading tries `GET /api/v1/commissions` first. If that fails, it uses `fallback_commission_rate` (0.015%). `stt` is the 2026 securities transaction tax on sells. `slippage_ticks` is used by the backtest, not live LIMIT orders.

## How to run

Always activate the venv first:

```powershell
cd C:\Users\nasan\krx-toss-trading
.\.venv\Scripts\Activate.ps1
```

### Recommended path: paper, then live

**1. Confirm the API works and build a watchlist**

```powershell
krx-toss scan
```

Writes `data/cache/signals.json` with `accepted`, `rejected`, and `universe`. `rejected` tells you why a name was skipped (`below_ma`, `foreign_not_buying`, `kospi_risk_off`, …).

**2. Optional: cache history and replay**

```powershell
krx-toss fetch-cache
krx-toss backtest --nav 100000000
```

Backtest needs a cached universe from `scan` or `fetch-cache`. It replays daily bars with tax and fees; it is not a tick-level simulator.

**3. Paper entries (always dry-run, even if `dry_run` is false)**

```powershell
krx-toss paper
```

Places LIMIT buys from the last scan into the local blotter only. After 09:15 KST; before that it logs that entries are blocked.

**4. Paper overlay and time-stop**

```powershell
krx-toss overlay
krx-toss eod
krx-toss status
```

**5. Leave the scheduler running for a full paper day** (`dry_run: true`):

```powershell
krx-toss run
```

Ctrl+C to stop. `--once` runs a single pass and exits (useful to test clock logic).

**6. Live orders — two switches, both required for `live`**

In `config/settings.yaml`:

```yaml
dry_run: false
```

Then either:

```powershell
krx-toss live --i-understand-the-risk
```

or, after flipping `dry_run`, the scheduler itself will send real orders:

```powershell
krx-toss run
```

`krx-toss run`, `overlay`, and `eod` honor `dry_run` only. They do **not** require `--i-understand-the-risk`. Keep `dry_run: true` unless you intend to trade.

### Command cheat sheet

| Command | Needs Toss API | Places orders | Notes |
| --- | --- | --- | --- |
| `krx-toss scan` | Yes | No | Rebuilds watchlist + signals. |
| `krx-toss fetch-cache` | Yes | No | Scan plus extra KOSPI candles. |
| `krx-toss paper` | Yes | Dry-run only | Uses last `signals.json`; scans first if missing. |
| `krx-toss live --i-understand-the-risk` | Yes | Live | Refuses unless `dry_run: false`. |
| `krx-toss overlay` | Yes | Follows `dry_run` | Holdings only, 1-minute bars. |
| `krx-toss eod` | Yes | Follows `dry_run` | Time-stop flatten + daily-loss kill switch. |
| `krx-toss run` | Yes | Follows `dry_run` | Calendar loop. |
| `krx-toss run --once` | Yes | Follows `dry_run` | One loop, then exit. |
| `krx-toss backtest --nav 100000000` | No | No | Needs cached universe. |
| `krx-toss status` | No | No | Kill switch + blotter positions. |
| `pytest` | No | No | Unit tests; no credentials required. |

Pass `--root C:\path\to\krx-toss-trading` if you invoke the CLI from another directory.

### Scheduler clock (KST, session days only)

| When | Job |
| --- | --- |
| ≥ 09:15 and before 15:00 | One entry batch per day from `signals.json` |
| 09:15–15:30 | Overlay on open positions, once per `overlay_seconds` |
| ≥ 15:35 | EOD: bump session count, time-stop, daily-loss kill switch |
| ≥ 15:45 | Scan for the **next** session |

Holidays use Toss `GET` calendar; if that call fails, weekdays are treated as open.

## Day-to-day files

| Path | What you look at |
| --- | --- |
| `logs/krx-toss.log` | INFO log of scans, dry-run orders, overlay exits. |
| `data/cache/signals.json` | Last watchlist and accepted/rejected reasons. |
| `data/blotter.sqlite` | Orders, fills, positions. |
| `data/kill_switch.json` | Present when the kill switch is tripped. |

If the kill switch trips (daily loss, or you trip it by writing that file), new orders stop until you delete `data/kill_switch.json` or otherwise reset it. Check `krx-toss status` first.

## Typical config edits

**Fewer, larger names**

```yaml
universe:
  watchlist_size: 40
risk:
  max_positions: 4
  position_nav_pct: 0.15
```

**Stricter momentum**

```yaml
signal:
  min_20d_return: 0.03
  max_3d_return: 0.10
  flow_lookback_sessions: 2
```

**Wider stop, later time-stop**

```yaml
exit:
  take_profit: 0.08
  stop_loss: 0.06
  time_stop_sessions: 7
```

**Do not flatten on VI** (overlay will still flatten on blocked warnings and near upper limit):

```yaml
exit:
  overlay_vi_flatten: false
```

**Pay up one tick for a better chance of fill**

```yaml
entry:
  limit_offset_ticks: 1
```

After any of these, re-run `krx-toss scan` before the next entry batch so `signals.json` matches the new rules.

## Safety

- `paper` never sends live orders. `live` requires the confirmation flag **and** `dry_run: false`.
- `run` / `overlay` / `eod` send live orders as soon as `dry_run` is false.
- Order creates are not retried (double-fill risk). Each create has a `clientOrderId`.
- Overlay watches holdings only. It will not exit a name that is not in the local blotter.
- Round-trip cost is commission plus ~0.20% sell tax. Sub-1% targets are usually negative EV.
