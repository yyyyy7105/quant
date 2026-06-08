# quant

Personal stock tracker, trade journal, **and backtester**. Fetches daily price data for both **US stocks and 中国 A股**, computes technical indicators on the fly, logs your trades, auto-pulls events (earnings, dividends, news, price anomalies), runs strategy backtests with `vectorbt`, screens your watch list with **MyTT-format formulas (选股器)**, and surfaces everything in a local Streamlit dashboard. The dashboard UI is in **简体中文**.

## Stack

| Layer | Tool |
|---|---|
| Package manager | `uv` |
| Data source (US) | `yfinance` (Yahoo Finance, free, no API key) |
| Data source (A股) | `akshare` (free, no token, 前复权/qfq) |
| Storage | SQLite (`data/quant.db`) |
| Dashboard | Streamlit + Plotly |
| Backtesting | `vectorbt` (vectorized, fast on daily bars) |
| Screener math | `quant/mytt.py` (vendored, pure-math MyTT primitives — no network/file I/O) |

### Markets

One application codepath serves both markets, distinguished by a `market` column (`'US'` | `'CN'`):

- **US** (`--market US`, default) → yfinance. Symbols are alpha (e.g. `NVDA`).
- **A股 / CN** (`--market CN`) → akshare, 前复权 (qfq). Symbols are 6-digit codes (e.g. `600519`). Volume is normalized to **shares** (akshare returns 手/lots → ×100).

In the dashboard, a **sidebar 市场 toggle** (美股 US / A股 CN) switches the active market everywhere (ticker lists, fetch, screener, backtest). A股 event auto-pull (earnings/dividends/news) is **out of scope** — the "拉取全部事件" button is disabled for CN; manual events still work.

---

## Quick start

```bash
# Install deps
uv sync

# Register tickers to watch (fetches history automatically)
uv run python cli.py ticker add NOK RKLB DRAM           # US (default)
uv run python cli.py ticker add --market CN 600519       # 中国 A股 (akshare)

# Pull all events (earnings + dividends + news + anomalies) in one call (US only)
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
uv run python cli.py ticker add --market CN 600519    # A股 via akshare (default --market US)

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

Downloads **raw OHLCV** history and writes it to `prices_daily` in SQLite. Indicators are **not** stored — they're computed on the fly at load/display time (see below).

```bash
uv run python cli.py fetch                          # active US watch list (default)
uv run python cli.py fetch --tickers AAPL MSFT      # custom tickers
uv run python cli.py fetch --period 1y              # shorter window (default: 5y)
uv run python cli.py fetch --market CN              # refresh only active A股 tickers
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

The dashboard mirrors all CLI capabilities (and is the only place to edit/delete rows). The UI is in **简体中文**.

**Sidebar (侧边栏):**
- **市场 toggle** — switch between 美股 (US) and A股 (CN); affects every page (ticker lists, fetch, screener, backtest)
- **拉取最新行情** button — refresh raw OHLCV for the active market's watch list
- **拉取全部事件** button — earnings + dividends + news + anomalies in one click (**disabled for A股**)
- **最新更新** — most recent bar date and when each fetch/pull last ran (e.g. "2026-06-05 (更新于:5 分钟前)")

**Pages (页面):**

| Page | What you see |
|---|---|
| **投资组合** (Portfolio) | Open positions, cost basis, market value, unrealized P&L, allocation pie |
| **个股看板** (Ticker view) | **Multi-panel chart** — candlestick (红涨绿跌) with buy/sell markers + event overlays (earnings ★, dividend $, news 📰, anomaly ⚠), main-chart MA/**BOLL** overlays, a **volume** row, and selectable **MACD / RSI / KDJ** indicator panels. Scrollable date window; clickable event links in the table below |
| **回测** (Backtest) | Pick ticker + strategy, tune params via sliders, run vectorbt backtest. Equity curve vs buy-and-hold, drawdown chart, trade markers, full metrics. Saved runs list for comparison |
| **选股器** (Screener) | Write a **MyTT-format formula**, save it, and scan the active market's watch list for tickers currently matching. **This is a screener, not a backtest** — no entries/exits/P&L. Matches on the latest bar or any of the last N bars |
| **交易记录** (Trade log) | Add trades, edit cells inline (incl. date/time), tick rows + Delete selected, filter by ticker/tag |
| **事件流** (Events feed) | Add manual events, edit/delete, filter by kind/ticker |
| **自选股** (Tickers) | Watch list management — add (with initial fetch), remove, see row counts; respects the active market |

---

## Screener (选股器) — MyTT formulas

Write a boolean formula in **MyTT / 通达信 syntax** in the 选股器 page, save it to the `formulas` table, and scan your watch list for tickers where the condition currently holds. Formulas are evaluated in a **restricted, sandboxed namespace** (`{"__builtins__": {}}` + a whitelist of pure-math functions only) — no imports, no file/network access. The math lives in `quant/mytt.py` (vendored, auditable, pure pandas/numpy).

**Available names:**
- Price series: `CLOSE`/`C`, `OPEN`/`O`, `HIGH`/`H`, `LOW`/`L`, `VOL`/`V`
- Functions: `MA, EMA, SMA, REF, CROSS, COUNT, HHV, LLV, SUM, STD, RSI, ABS, MAX, MIN, IF, EVERY, EXIST` (plus `MACD, KDJ, BOLL`)

**Examples:**
```text
CROSS(MA(CLOSE,5), MA(CLOSE,20)) & (RSI(CLOSE,14) < 70)   # 5/20 golden cross, not overbought
CLOSE >= HHV(CLOSE, 60)                                    # 60-day breakout
VOL > MA(VOL, 20) * 2                                      # volume spike
```

---

## Technical indicators (computed on the fly at load time)

Indicators are **no longer stored** in the database. `prices_daily` holds only raw OHLCV; `load_daily()` reads a ticker's full history, computes indicators over it (so rolling windows like SMA_200 warm up correctly), then slices to the requested date range. The math source is `quant/mytt.py`, reused by both the chart panels and the screener.

| Column / panel | Description |
|---|---|
| `sma_20/50/200` | Simple moving averages |
| `ema_20` | Exponential moving average (span=20) |
| `daily_return` | `close.pct_change()` |
| `volatility_20` | 20-day rolling std of daily returns |
| `rsi_14` | Wilder's RSI (0-100) |
| `vol_sma_20` | 20-day average volume |
| MACD / KDJ / BOLL | Computed via `metrics.macd/kdj/boll` (→ `mytt`) for the chart panels/overlay |

Add more indicators by extending `add_metrics()` in `quant/metrics.py`, or add primitives to `quant/mytt.py`.

---

## Project layout

```
quant/
  cli.py              unified CLI entry point
  app.py              Streamlit dashboard
  fetch_stocks.py     legacy shim (delegates to quant.prices)
  quant/
    db.py             SQLite connection + schema (+ migrations)
    prices.py         fetch_daily (market dispatch), load_daily (enrich-then-slice)
    sources/          per-market fetchers (same signature, raw OHLCV out)
      us.py           yfinance (US/global)
      cn.py           akshare (A股, 前复权)
    metrics.py        on-the-fly indicator math (delegates to mytt)
    mytt.py           vendored MyTT primitives — pure math, no I/O (screener + charts)
    formula.py        screener: evaluate/scan + formula CRUD
    trades.py         add_trade, list_trades, positions
    events.py         add_event, pull_*, detect_anomalies
    oplog.py          operation timestamps + data freshness helpers
    tickers.py        watch list registry (add/remove/list, market-aware)
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
| `prices_daily` | `ticker, date` + **raw OHLCV** + `market` (`'US'`\|`'CN'`) — no indicator columns |
| `trades` | `id, ts, ticker, side, qty, price, fees, notes, tags` |
| `events` | `id, ts, ticker, kind, title, body, source_url, metadata` |
| `tickers` | `ticker, added_at, active, notes, market` |
| `formulas` | `id, name, expr, description, created_at` (saved screener formulas) |
| `fetch_log` | `id, op, ts, details` |
| `backtests` | `id, created_at, ticker, strategy, params_json, init_cash, fees, total_return, sharpe, max_drawdown, win_rate, num_trades, bh_total_return, ...` |

> **Migration:** opening the app (or any `connect()`) auto-migrates an older DB — it rebuilds `prices_daily` to drop the legacy indicator columns (existing rows are stamped `market='US'`) and adds `market` to `tickers`. Raw OHLCV is preserved.

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
