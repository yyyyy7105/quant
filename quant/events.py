"""Event log: manual notes + auto pulls (earnings/dividends/news) + price-anomaly flags."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd
import yfinance as yf

from .db import connect
from .oplog import record as record_op

VALID_KINDS = {"manual", "earnings", "dividend", "news", "anomaly"}


def add_event(
    ticker: str,
    kind: str,
    title: str,
    body: str | None = None,
    ts: str | datetime | None = None,
    source_url: str | None = None,
    metadata: dict | None = None,
    dedupe_key: str | None = None,
) -> int | None:
    """Insert an event. Returns row id, or None if a duplicate was skipped."""
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {VALID_KINDS}, got {kind!r}")

    when = _normalize_ts(ts)
    meta_json = json.dumps(metadata) if metadata else None

    try:
        with connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO events (ts, ticker, kind, title, body, source_url, metadata, dedupe_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (when, ticker.upper(), kind, title, body, source_url, meta_json, dedupe_key),
            )
            conn.commit()
            return cur.lastrowid
    except sqlite3.IntegrityError:
        # dedupe_key collision
        return None


def list_events(
    ticker: str | None = None,
    kind: str | None = None,
    since: str | datetime | None = None,
) -> pd.DataFrame:
    q = "SELECT * FROM events WHERE 1=1"
    params: list = []
    if ticker:
        q += " AND ticker = ?"
        params.append(ticker.upper())
    if kind:
        q += " AND kind = ?"
        params.append(kind)
    if since:
        q += " AND ts >= ?"
        params.append(_normalize_ts(since))
    q += " ORDER BY ts DESC"
    with connect() as conn:
        df = pd.read_sql(q, conn, params=params)
    if not df.empty:
        # Events come from sources with mixed tz (UTC for news/earnings, naive for manual)
        # — coerce all to UTC then drop tz so downstream code can compare freely.
        df["ts"] = pd.to_datetime(
            df["ts"], utc=True, errors="coerce", format="mixed"
        ).dt.tz_localize(None)
    return df


def delete_event(event_id: int) -> bool:
    """Delete an event by id. Returns True if a row was removed."""
    with connect() as conn:
        cur = conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        conn.commit()
        return cur.rowcount > 0


def update_event(event_id: int, **fields) -> bool:
    """Update specific fields of an event by id. Returns True if a row was changed.

    Allowed fields: ts, ticker, kind, title, body, source_url.
    """
    allowed = {"ts", "ticker", "kind", "title", "body", "source_url"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return False

    if "ticker" in updates:
        updates["ticker"] = updates["ticker"].upper()
    if "kind" in updates and updates["kind"] not in VALID_KINDS:
        raise ValueError(f"kind must be one of {VALID_KINDS}")
    if "ts" in updates:
        updates["ts"] = _normalize_ts(updates["ts"])

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [event_id]
    with connect() as conn:
        cur = conn.execute(f"UPDATE events SET {set_clause} WHERE id = ?", values)
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Auto pulls
# ---------------------------------------------------------------------------

def pull_earnings(tickers: Iterable[str]) -> int:
    """Pull earnings dates per ticker. Idempotent via dedupe_key."""
    inserted = 0
    for ticker in tickers:
        t = ticker.upper()
        try:
            df = yf.Ticker(t).earnings_dates
        except Exception as e:
            print(f"[WARN] {t} earnings: {e}")
            continue
        if df is None or df.empty:
            continue
        for ts, row in df.iterrows():
            when = _ts_iso(ts)
            metadata = {
                k: (None if pd.isna(v) else (float(v) if hasattr(v, "real") else v))
                for k, v in row.items()
            }
            key = f"earnings:{t}:{when[:10]}"
            res = add_event(
                ticker=t,
                kind="earnings",
                title=f"{t} earnings",
                ts=when,
                metadata=metadata,
                dedupe_key=key,
            )
            if res is not None:
                inserted += 1
    print(f"[OK]   earnings: {inserted} new rows")
    record_op("pull_earnings", f"{inserted} new rows")
    return inserted


def pull_dividends(tickers: Iterable[str]) -> int:
    """Pull dividend payouts per ticker. Idempotent via dedupe_key."""
    inserted = 0
    for ticker in tickers:
        t = ticker.upper()
        try:
            series = yf.Ticker(t).dividends
        except Exception as e:
            print(f"[WARN] {t} dividends: {e}")
            continue
        if series is None or series.empty:
            continue
        for ts, amount in series.items():
            when = _ts_iso(ts)
            key = f"dividend:{t}:{when[:10]}"
            res = add_event(
                ticker=t,
                kind="dividend",
                title=f"{t} dividend ${float(amount):.4f}",
                ts=when,
                metadata={"amount": float(amount)},
                dedupe_key=key,
            )
            if res is not None:
                inserted += 1
    print(f"[OK]   dividends: {inserted} new rows")
    record_op("pull_dividends", f"{inserted} new rows")
    return inserted


def pull_news(tickers: Iterable[str]) -> int:
    """Pull recent news headlines per ticker. Idempotent via dedupe_key (uuid/url)."""
    inserted = 0
    for ticker in tickers:
        t = ticker.upper()
        try:
            items = yf.Ticker(t).news or []
        except Exception as e:
            print(f"[WARN] {t} news: {e}")
            continue
        for item in items:
            # yfinance has shipped two shapes for .news; handle both.
            content = item.get("content") if isinstance(item, dict) else None
            if content:  # newer schema
                title = content.get("title")
                pub = content.get("pubDate") or content.get("displayTime")
                url = (content.get("canonicalUrl") or {}).get("url") or (
                    content.get("clickThroughUrl") or {}
                ).get("url")
                publisher = (content.get("provider") or {}).get("displayName")
            else:  # legacy schema
                title = item.get("title")
                ts_epoch = item.get("providerPublishTime")
                pub = (
                    datetime.fromtimestamp(ts_epoch, tz=timezone.utc).isoformat()
                    if ts_epoch
                    else None
                )
                url = item.get("link")
                publisher = item.get("publisher")

            if not title:
                continue
            when = _ts_iso(pub) if pub else datetime.now().isoformat(timespec="seconds")
            uuid = item.get("uuid") or item.get("id") or url or title
            key = f"news:{t}:{uuid}"

            res = add_event(
                ticker=t,
                kind="news",
                title=title,
                body=publisher,
                ts=when,
                source_url=url,
                metadata={"publisher": publisher},
                dedupe_key=key,
            )
            if res is not None:
                inserted += 1
    print(f"[OK]   news: {inserted} new rows")
    record_op("pull_news", f"{inserted} new rows")
    return inserted


def detect_anomalies(
    tickers: Iterable[str] | None = None,
    threshold: float = 2.0,
) -> int:
    """Flag days where |daily_return| > threshold * volatility_20 as 'anomaly' events.

    Idempotent via dedupe_key keyed on (ticker, date).
    """
    q = """
        SELECT ticker, date, close, daily_return, volatility_20
        FROM prices_daily
        WHERE volatility_20 IS NOT NULL AND daily_return IS NOT NULL
    """
    params: list = []
    if tickers:
        placeholders = ",".join("?" * len(list(tickers)))
        tickers = [t.upper() for t in tickers]
        q += f" AND ticker IN ({placeholders})"
        params.extend(tickers)

    with connect() as conn:
        df = pd.read_sql(q, conn, params=params)

    if df.empty:
        print("[OK]   anomalies: no daily data available")
        return 0

    df["zscore"] = df["daily_return"] / df["volatility_20"]
    flagged = df[df["zscore"].abs() >= threshold]

    inserted = 0
    for _, row in flagged.iterrows():
        when = f"{row.date}T16:00:00"  # market close convention
        key = f"anomaly:{row.ticker}:{row.date}"
        direction = "spike" if row.zscore > 0 else "drop"
        title = (
            f"{row.ticker} {direction} {row.daily_return*100:+.2f}% "
            f"(z={row.zscore:+.2f})"
        )
        res = add_event(
            ticker=row.ticker,
            kind="anomaly",
            title=title,
            ts=when,
            metadata={
                "daily_return": float(row.daily_return),
                "zscore": float(row.zscore),
                "close": float(row.close),
            },
            dedupe_key=key,
        )
        if res is not None:
            inserted += 1
    print(f"[OK]   anomalies: {inserted} new rows (threshold={threshold} sigma)")
    record_op("detect_anomalies", f"{inserted} new rows (threshold={threshold} sigma)")
    return inserted


def pull_all(tickers: Iterable[str], anomaly_threshold: float = 2.0) -> dict[str, int]:
    """Run every auto-pull in sequence: earnings, dividends, news, anomalies.

    Returns a dict {op: inserted_rows}.
    """
    tickers = list(tickers)
    results = {
        "earnings":  pull_earnings(tickers),
        "dividends": pull_dividends(tickers),
        "news":      pull_news(tickers),
        "anomalies": detect_anomalies(tickers, threshold=anomaly_threshold),
    }
    total = sum(results.values())
    record_op("pull_all", f"{len(tickers)} tickers, {total} new rows total")
    return results


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _normalize_ts(ts: str | datetime | None) -> str:
    if ts is None:
        return datetime.now().isoformat(timespec="seconds")
    if isinstance(ts, datetime):
        return ts.isoformat(timespec="seconds")
    try:
        return datetime.fromisoformat(ts).isoformat(timespec="seconds")
    except ValueError:
        return datetime.strptime(ts, "%Y-%m-%d").isoformat(timespec="seconds")


def _ts_iso(ts) -> str:
    """Coerce pandas Timestamp / datetime / str into an ISO string."""
    if isinstance(ts, str):
        return _normalize_ts(ts)
    if hasattr(ts, "isoformat"):
        return ts.isoformat()
    return str(ts)
