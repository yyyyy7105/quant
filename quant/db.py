"""SQLite connection helper + schema bootstrap.

All other modules import `connect()` and run their queries through that. The first
call creates `data/quant.db` and applies the schema.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from . import DATA_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices_daily (
    ticker          TEXT NOT NULL,
    date            TEXT NOT NULL,            -- ISO date (YYYY-MM-DD)
    open            REAL,
    high            REAL,
    low             REAL,
    close           REAL,
    volume          INTEGER,
    market          TEXT NOT NULL DEFAULT 'US',  -- 'US' | 'CN'
    PRIMARY KEY (ticker, date)
);
-- NOTE: technical indicators (sma/ema/rsi/macd/kdj/boll) are computed on the fly
-- in quant.metrics at load time, NOT stored here. Only raw OHLCV is persisted.

CREATE TABLE IF NOT EXISTS prices_intraday (
    ticker      TEXT NOT NULL,
    ts          TEXT NOT NULL,                -- ISO timestamp
    interval    TEXT NOT NULL,                -- '5m', '15m', '1h', ...
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      INTEGER,
    PRIMARY KEY (ticker, ts, interval)
);

CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,                -- ISO timestamp
    ticker      TEXT NOT NULL,
    side        TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    qty         REAL NOT NULL,
    price       REAL NOT NULL,
    fees        REAL NOT NULL DEFAULT 0,
    notes       TEXT,
    tags        TEXT                          -- comma-separated
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    kind        TEXT NOT NULL,                -- 'manual'|'earnings'|'dividend'|'news'|'anomaly'
    title       TEXT,
    body        TEXT,
    source_url  TEXT,
    metadata    TEXT,                         -- JSON blob
    dedupe_key  TEXT UNIQUE                   -- prevent duplicate auto-pulls
);

CREATE TABLE IF NOT EXISTS tickers (
    ticker      TEXT PRIMARY KEY,
    added_at    TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,   -- 0 = removed (history kept)
    notes       TEXT,
    market      TEXT NOT NULL DEFAULT 'US'    -- 'US' | 'CN'
);

CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT NOT NULL UNIQUE,
    pw_hash     TEXT NOT NULL,                -- PBKDF2-HMAC-SHA256
    pw_salt     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backtests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,
    ticker          TEXT NOT NULL,
    strategy        TEXT NOT NULL,
    params_json     TEXT NOT NULL,
    start_date      TEXT,
    end_date        TEXT,
    init_cash       REAL NOT NULL,
    fees            REAL NOT NULL,
    total_return    REAL,
    annualized_return REAL,
    sharpe          REAL,
    max_drawdown    REAL,
    win_rate        REAL,
    num_trades      INTEGER,
    avg_trade_pct   REAL,
    bh_total_return REAL,
    bh_max_drawdown REAL,
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_backtests_ticker ON backtests(ticker, created_at);

CREATE TABLE IF NOT EXISTS fetch_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    op          TEXT NOT NULL,                -- 'fetch_daily' | 'pull_earnings' | etc.
    ts          TEXT NOT NULL,                -- ISO timestamp when op finished
    details     TEXT                          -- e.g. "3 tickers, 0 new rows"
);
CREATE INDEX IF NOT EXISTS idx_fetch_log_op ON fetch_log(op, ts);

CREATE TABLE IF NOT EXISTS formulas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    expr        TEXT NOT NULL,                -- MyTT-format boolean expression
    description TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_tt ON trades(ticker, ts);
CREATE INDEX IF NOT EXISTS idx_events_tt ON events(ticker, ts);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
"""


_LEGACY_INDICATOR_COLS = {
    "sma_20", "sma_50", "sma_200", "ema_20",
    "daily_return", "volatility_20", "rsi_14", "vol_sma_20",
}


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)

    # Migration: drop total_cost column from trades if it still exists
    tcols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    if "total_cost" in tcols:
        conn.execute("ALTER TABLE trades DROP COLUMN total_cost")

    # Migration: prices_daily now stores raw OHLCV only (indicators computed on
    # the fly) + a `market` column. Rebuild the table if legacy indicator columns
    # are still present; otherwise just add `market` if missing.
    pcols = {row[1] for row in conn.execute("PRAGMA table_info(prices_daily)").fetchall()}
    if pcols & _LEGACY_INDICATOR_COLS:
        conn.executescript(
            """
            CREATE TABLE prices_daily_new (
                ticker  TEXT NOT NULL,
                date    TEXT NOT NULL,
                open    REAL,
                high    REAL,
                low     REAL,
                close   REAL,
                volume  INTEGER,
                market  TEXT NOT NULL DEFAULT 'US',
                PRIMARY KEY (ticker, date)
            );
            INSERT INTO prices_daily_new (ticker, date, open, high, low, close, volume, market)
                SELECT ticker, date, open, high, low, close, volume, 'US' FROM prices_daily;
            DROP TABLE prices_daily;
            ALTER TABLE prices_daily_new RENAME TO prices_daily;
            """
        )
    elif "market" not in pcols:
        conn.execute("ALTER TABLE prices_daily ADD COLUMN market TEXT NOT NULL DEFAULT 'US'")

    # Migration: add `market` to tickers if missing
    tkcols = {row[1] for row in conn.execute("PRAGMA table_info(tickers)").fetchall()}
    if "market" not in tkcols:
        conn.execute("ALTER TABLE tickers ADD COLUMN market TEXT NOT NULL DEFAULT 'US'")

    conn.commit()


def connect() -> sqlite3.Connection:
    """Open (or create) the project SQLite DB with the schema applied."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(conn)
    return conn


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    """Convenience context manager: commits on success, rolls back on error."""
    conn = connect()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
