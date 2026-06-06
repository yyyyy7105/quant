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
    sma_20          REAL,
    sma_50          REAL,
    sma_200         REAL,
    ema_20          REAL,
    daily_return    REAL,
    volatility_20   REAL,
    rsi_14          REAL,
    vol_sma_20      REAL,
    PRIMARY KEY (ticker, date)
);

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
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT NOT NULL UNIQUE,
    pw_hash     TEXT NOT NULL,                -- PBKDF2-HMAC-SHA256
    pw_salt     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    op          TEXT NOT NULL,                -- 'fetch_daily' | 'pull_earnings' | etc.
    ts          TEXT NOT NULL,                -- ISO timestamp when op finished
    details     TEXT                          -- e.g. "3 tickers, 0 new rows"
);
CREATE INDEX IF NOT EXISTS idx_fetch_log_op ON fetch_log(op, ts);

CREATE INDEX IF NOT EXISTS idx_trades_tt ON trades(ticker, ts);
CREATE INDEX IF NOT EXISTS idx_events_tt ON events(ticker, ts);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # Migration: drop total_cost column from trades if it still exists
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    if "total_cost" in cols:
        conn.execute("ALTER TABLE trades DROP COLUMN total_cost")
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
