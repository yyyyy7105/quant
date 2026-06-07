# quant

Personal stock tracker, trade journal, **and backtester**. Fetches daily price data, computes technical indicators, logs your trades, auto-pulls events (earnings, dividends, news, price anomalies), runs strategy backtests with `vectorbt`, and surfaces everything in a local Streamlit dashboard.

## Stack

| Layer | Tool |
|---|---|
| Package manager | `uv` |
| Data source | `yfinance` (Yahoo Finance, free, no API key) |
| Storage | SQLite (`data/quant.db`) |
| Dashboard | Streamlit + Plotly |
| Backtesting | `vectorbt` (vectorized, fast on daily bars) |

---

## Quick start

```bash
# Install deps
uv sync

# Register tickers to watch (fetches history automatically)
uv run python cli.py ticker add NOK RKLB DRAM

# Pull all events (earnings + dividends + news + anomalies) in one call
uv run python cli.py event pull-all

# Open dashboard
uv run streamlit run app.py
```

After this, do everything from the **dashboard** — add tickers/trades/events, fix mistakes, refresh data — all via buttons and forms. The sidebar shows when each slice was last refreshed.

---

## CLI reference

### `ticker` — manage your watch list

This is the source of truth for which tickers get fetched and pulled by default.

```bash
# Add (fetches 5y of history immediately)
uv run python cli.py ticker add NVDA
uv run python cli.py ticker add NVDA AAPL MSFT        # multiple at once
uv run python cli.py ticker add NVDA --period 1y      # shorter history window

# Remove (deactivates — history is kept, won't auto-fetch)
uv run python cli.py ticker remove NOK

# List all tickers (active + inactive) with price row counts
uv run python cli.py ticker list
```

```
ticker active            added_at  price_rows   earliest     latest  last_close
  DRAM    yes 2026-06-05T21:39:30          45 2026-04-02 2026-06-05      55.79
   NOK    yes 2026-06-05T21:39:29        1256 2021-06-07 2026-06-05      14.38
  RKLB    yes 2026-06-05T21:39:30        1256 2021-06-07 2026-06-05     110.08
```

All `fetch` and `event pull-*` commands use the active list automatically. Pass `--tickers` to override for a one-off run.

---

### `fetch` — daily prices

Downloads OHLCV history and computes indicators. Writes to `prices_daily` in SQLite.

```bash
uv run python cli.py fetch                          # NOK, RKLB, DRAM (default)
uv run python cli.py fetch --tickers AAPL MSFT      # custom tickers
uv run python cli.py fetch --period 1y              # shorter window (default: 5y)
```

### `trade` — log buy/sell

```bash
# Add a trade
uv run python cli.py trade add NOK BUY 100 15.41
uv run python cli.py trade add RKLB BUY 50 95.20 --fees 1 --notes "space play" --tags long-term

# List trades
uv run python cli.py trade list
uv run python cli.py trade list --ticker NOK
```

Fields captured: `timestamp · ticker · side · qty · price · fees · notes · tags`

`total_cost` is derived on the fly (`qty × price ± fees`) and not stored.

### `positions` — current holdings

Derives net qty and weighted-average cost basis from the trade log. Attaches latest close and unrealized P&L.

```bash
uv run python cli.py positions
```

### `event` — event log

**Manual note:**
```bash
uv run python cli.py event add NOK "CEO statement on 5G rollout" --kind manual
uv run python cli.py event add RKLB "New launch contract" --kind manual --body "Details..."
```

**Auto pulls** (all idempotent — safe to re-run):
```bash
uv run python cli.py event pull-all                # earnings + dividends + news + anomalies (recommended)
uv run python cli.py event pull-all --threshold 2.5

# Or run individual pulls:
uv run python cli.py event pull-earnings
uv run python cli.py event pull-dividends
uv run python cli.py event pull-news
uv run python cli.py event detect-anomalies
```

**List events:**
```bash
uv run python cli.py event list
uv run python cli.py event list --ticker NOK --kind anomaly
```

Event kinds: `manual · earnings · dividend · news · anomaly`

> **Note:** `edit` and `delete` operations are only available in the Streamlit app — easier to use with a table than positional CLI args.

---

### `backtest` — run a strategy on historical data

Built-in strategies (more can be added in `quant/strategies.py`):

| Key | Strategy | Params |
|---|---|---|
| `golden_cross` | Buy when short SMA crosses above long SMA; sell on reverse | `short`, `long` |
| `rsi_mean_reversion` | Buy when RSI_14 drops below `lower`; sell when above `upper` | `lower`, `upper` |
| `bollinger_breakout` | Buy on upper-band breakout; sell on lower-band breakdown | `window`, `n_std` |

```bash
# Run with defaults
uv run python cli.py backtest run NOK --strategy golden_cross

# Custom params (any --<param> matching the strategy)
uv run python cli.py backtest run NOK --strategy golden_cross --short 20 --long 50
uv run python cli.py backtest run RKLB --strategy rsi_mean_reversion --lower 25 --upper 75
uv run python cli.py backtest run NOK --strategy bollinger_breakout --window 20 --n_std 2.5

# Restrict window, change cash/fees
uv run python cli.py backtest run NOK --strategy golden_cross --start 2024-01-01 --end 2026-01-01 --init-cash 50000 --fees 0.0015

# List / inspect / delete
uv run python cli.py backtest list                # all runs
uv run python cli.py backtest list --ticker NOK   # filter
uv run python cli.py backtest show 7              # print one run's full config
uv run python cli.py backtest delete 7
```

Each run is saved to `backtests` table with config + metrics (total return, Sharpe, max drawdown, win rate, etc.) plus a buy-and-hold benchmark for the same window — so you can compare runs later.

---

## Dashboard

```bash
uv run streamlit run app.py
```

The dashboard mirrors all CLI capabilities (and is the only place to edit/delete rows).

**Sidebar:**
- **Fetch latest prices** button — refresh OHLCV for all watched tickers
- **Pull all events** button — earnings + dividends + news + anomalies in one click
- **Latest price bar** — shows the most recent bar date and when the fetch last ran (e.g. "2026-06-05 (updated: 5 min ago)")

**Pages:**

| Page | What you see |
|---|---|
| **Portfolio** | Open positions, cost basis, market value, unrealized P&L, allocation pie |
| **Ticker view** | Candlestick chart with buy/sell markers, event overlays (earnings ★, dividend $, news, anomaly ⚠), SMA/EMA toggles, scrollable date window, clickable event links in table below |
| **Backtest** | Pick ticker + strategy, tune params via sliders, run vectorbt backtest. Shows equity curve vs buy-and-hold, drawdown chart, trade markers, full metrics. Saved runs list at the bottom for comparison. |
| **Trade log** | Add new trades, edit cells inline (including date/time), tick rows + Delete selected, filter by ticker/tag |
| **Events feed** | Add manual events, edit/delete, filter by kind/ticker |
| **Tickers** | Watch list management — add (with initial fetch), remove, see row counts |

---

## Technical indicators (computed on every `fetch`)

| Column | Description |
|---|---|
| `SMA_20/50/200` | Simple moving averages |
| `EMA_20` | Exponential moving average (span=20) |
| `Daily_Return` | `Close.pct_change()` |
| `Volatility_20` | 20-day rolling std of daily returns |
| `RSI_14` | Wilder's RSI (0-100) |
| `Vol_SMA_20` | 20-day average volume |

Add more indicators by extending `add_metrics()` in `quant/metrics.py`.

---

## Project layout

```
quant/
  cli.py              unified CLI entry point
  app.py              Streamlit dashboard
  fetch_stocks.py     legacy shim (delegates to quant.prices)
  quant/
    db.py             SQLite connection + schema
    prices.py         fetch_daily, load_daily
    metrics.py        technical indicator math
    trades.py         add_trade, list_trades, positions
    events.py         add_event, pull_*, detect_anomalies
    oplog.py          operation timestamps + data freshness helpers
    tickers.py        watch list registry (add/remove/list)
    strategies.py     built-in backtest strategies (signal generators)
    backtest.py       run_backtest() wrapping vectorbt + result persistence
  data/
    quant.db          all data (prices, trades, events)
  pyproject.toml
  uv.lock
```

---

## Database tables

| Table | Key columns |
|---|---|
| `prices_daily` | `ticker, date` + OHLCV + indicator columns |
| `trades` | `id, ts, ticker, side, qty, price, fees, notes, tags` |
| `events` | `id, ts, ticker, kind, title, body, source_url, metadata` |
| `tickers` | `ticker, added_at, active, notes` |
| `fetch_log` | `id, op, ts, details` |
| `backtests` | `id, created_at, ticker, strategy, params_json, init_cash, fees, total_return, sharpe, max_drawdown, win_rate, num_trades, bh_total_return, ...` |

Query directly with any SQLite client:
```bash
sqlite3 data/quant.db "SELECT kind, COUNT(*) FROM events GROUP BY kind"
```

---

## Adding or removing a ticker

```bash
# Add — registers it and fetches history immediately
uv run python cli.py ticker add NVDA

# Remove — deactivates (history stays, won't be included in future auto-fetches)
uv run python cli.py ticker remove NOK

# Re-add a removed ticker
uv run python cli.py ticker add NOK
```

From that point, `cli.py fetch` and all `event pull-*` commands will include (or exclude) it automatically.
