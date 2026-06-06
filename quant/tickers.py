"""Managed ticker registry.

Tickers in this table drive auto-fetch (no --tickers flag needed).
Price history and events are always kept even after removal.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from .db import connect


def add(ticker: str, notes: str | None = None) -> bool:
    """Register a ticker as active. Returns True if newly added, False if re-activated."""
    t = ticker.upper()
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        existing = conn.execute(
            "SELECT active FROM tickers WHERE ticker = ?", (t,)
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO tickers (ticker, added_at, active, notes) VALUES (?, ?, 1, ?)",
                (t, now, notes),
            )
            conn.commit()
            return True
        else:
            conn.execute(
                "UPDATE tickers SET active = 1, notes = COALESCE(?, notes) WHERE ticker = ?",
                (notes, t),
            )
            conn.commit()
            return False


def remove(ticker: str) -> bool:
    """Deactivate a ticker (kept in DB, won't auto-fetch). Returns True if found."""
    t = ticker.upper()
    with connect() as conn:
        row = conn.execute("SELECT active FROM tickers WHERE ticker = ?", (t,)).fetchone()
        if row is None:
            return False
        conn.execute("UPDATE tickers SET active = 0 WHERE ticker = ?", (t,))
        conn.commit()
        return True


def get_active() -> list[str]:
    """Return tickers currently marked active, in alphabetical order."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT ticker FROM tickers WHERE active = 1 ORDER BY ticker"
        ).fetchall()
    return [r["ticker"] for r in rows]


def summary() -> pd.DataFrame:
    """Return a DataFrame of all tickers (active + inactive) with price stats."""
    with connect() as conn:
        df = pd.read_sql(
            """
            SELECT
                t.ticker,
                t.active,
                t.added_at,
                t.notes,
                COUNT(p.date)        AS price_rows,
                MIN(p.date)          AS earliest,
                MAX(p.date)          AS latest,
                MAX(p.close)         AS last_close
            FROM tickers t
            LEFT JOIN prices_daily p ON p.ticker = t.ticker
            GROUP BY t.ticker
            ORDER BY t.active DESC, t.ticker
            """,
            conn,
        )
    if not df.empty:
        df["active"] = df["active"].map({1: "yes", 0: "no"})
    return df
