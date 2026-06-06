"""Operation log: records when each fetch/pull last ran so the app can show timestamps.

Data is not real-time — these timestamps tell the user when each slice was last refreshed.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from .db import connect


def record(op: str, details: str | None = None) -> None:
    """Append a row to fetch_log marking this op as just completed."""
    ts = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            "INSERT INTO fetch_log (op, ts, details) VALUES (?, ?, ?)",
            (op, ts, details),
        )
        conn.commit()


def last_run(op: str) -> tuple[str | None, str | None]:
    """Return (ts, details) of the most recent run of `op`, or (None, None)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT ts, details FROM fetch_log WHERE op = ? ORDER BY ts DESC LIMIT 1",
            (op,),
        ).fetchone()
    return (row["ts"], row["details"]) if row else (None, None)


def latest_per_op() -> pd.DataFrame:
    """One row per op showing the most recent run."""
    with connect() as conn:
        return pd.read_sql(
            """
            SELECT op, MAX(ts) AS last_run, COUNT(*) AS times_run
            FROM fetch_log
            GROUP BY op
            ORDER BY last_run DESC
            """,
            conn,
        )


def latest_bar_date() -> str | None:
    """Most recent date present in prices_daily across all tickers, ISO yyyy-mm-dd."""
    with connect() as conn:
        row = conn.execute("SELECT MAX(date) AS d FROM prices_daily").fetchone()
    return row["d"] if row and row["d"] else None
