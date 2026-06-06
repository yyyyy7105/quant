"""Trade log: add, list, and derive current positions."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from .db import connect


def add_trade(
    ticker: str,
    side: str,
    qty: float,
    price: float,
    ts: str | datetime | None = None,
    fees: float = 0.0,
    notes: str | None = None,
    tags: str | None = None,
) -> int:
    """Insert a trade. Returns the new row id."""
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be BUY or SELL, got {side!r}")

    when = _normalize_ts(ts)

    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO trades (ts, ticker, side, qty, price, fees, notes, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (when, ticker.upper(), side, qty, price, fees, notes, tags),
        )
        conn.commit()
        return cur.lastrowid


def list_trades(
    ticker: str | None = None,
    since: str | datetime | None = None,
) -> pd.DataFrame:
    q = "SELECT * FROM trades WHERE 1=1"
    params: list = []
    if ticker:
        q += " AND ticker = ?"
        params.append(ticker.upper())
    if since:
        q += " AND ts >= ?"
        params.append(_normalize_ts(since))
    q += " ORDER BY ts"
    with connect() as conn:
        df = pd.read_sql(q, conn, params=params)
    if not df.empty:
        df["ts"] = pd.to_datetime(
            df["ts"], utc=True, errors="coerce", format="mixed"
        ).dt.tz_localize(None)
    return df


def delete_trade(trade_id: int) -> bool:
    """Delete a trade by id. Returns True if a row was removed."""
    with connect() as conn:
        cur = conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
        conn.commit()
        return cur.rowcount > 0


def update_trade(trade_id: int, **fields) -> bool:
    """Update specific fields of a trade by id. Returns True if a row was changed.

    Allowed fields: ts, ticker, side, qty, price, fees, notes, tags.
    """
    allowed = {"ts", "ticker", "side", "qty", "price", "fees", "notes", "tags"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return False

    if "ticker" in updates:
        updates["ticker"] = updates["ticker"].upper()
    if "side" in updates:
        updates["side"] = updates["side"].upper()
        if updates["side"] not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY or SELL, got {updates['side']!r}")
    if "ts" in updates:
        updates["ts"] = _normalize_ts(updates["ts"])

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [trade_id]
    with connect() as conn:
        cur = conn.execute(f"UPDATE trades SET {set_clause} WHERE id = ?", values)
        conn.commit()
        return cur.rowcount > 0


def positions() -> pd.DataFrame:
    """Current holdings: net qty per ticker with weighted-average cost basis.

    Adds latest close and unrealized P&L when daily price data is available.
    """
    trades = list_trades()
    if trades.empty:
        return pd.DataFrame(
            columns=["ticker", "qty", "avg_cost", "cost_basis", "last_close", "as_of", "market_value", "unrealized_pl"]
        )

    rows = []
    for ticker, g in trades.groupby("ticker"):
        net_qty = 0.0
        cost_basis = 0.0
        for _, t in g.iterrows():
            total_cost = t.qty * t.price + t.fees if t.side == "BUY" else t.qty * t.price - t.fees
            if t.side == "BUY":
                cost_basis += total_cost
                net_qty += t.qty
            else:  # SELL: reduce basis proportionally
                if net_qty > 0:
                    avg = cost_basis / net_qty
                    cost_basis -= avg * t.qty
                net_qty -= t.qty
        if abs(net_qty) < 1e-9:
            continue
        avg_cost = cost_basis / net_qty if net_qty else None
        rows.append(
            {
                "ticker": ticker,
                "qty": round(net_qty, 6),
                "avg_cost": round(avg_cost, 4) if avg_cost is not None else None,
                "cost_basis": round(cost_basis, 2),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Attach latest close + the date it came from (so the user knows data freshness)
    with connect() as conn:
        last = pd.read_sql(
            """
            SELECT p.ticker, p.close AS last_close, p.date AS as_of
            FROM prices_daily p
            JOIN (
                SELECT ticker, MAX(date) AS max_date FROM prices_daily GROUP BY ticker
            ) m ON m.ticker = p.ticker AND m.max_date = p.date
            """,
            conn,
        )
    df = df.merge(last, on="ticker", how="left")
    df["market_value"] = (df["qty"] * df["last_close"]).round(2)
    df["unrealized_pl"] = (df["market_value"] - df["cost_basis"]).round(2)
    return df


def _normalize_ts(ts: str | datetime | None) -> str:
    if ts is None:
        return datetime.now().isoformat(timespec="seconds")
    if isinstance(ts, datetime):
        return ts.isoformat(timespec="seconds")
    # Accept date-only strings
    try:
        return datetime.fromisoformat(ts).isoformat(timespec="seconds")
    except ValueError:
        return datetime.strptime(ts, "%Y-%m-%d").isoformat(timespec="seconds")
