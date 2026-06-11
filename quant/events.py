"""事件日志: 手动笔记 + 自动拉取(财报/分红/新闻) + 价格异动 —— Peewee CRUD。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd
import yfinance as yf
from peewee import IntegrityError

from .db import connect
from .models import Event
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
    """落库一条事件。返回新行 id;`dedupe_key` 冲突返回 None。"""
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {VALID_KINDS}, got {kind!r}")
    try:
        e = Event.create(
            ts=_normalize_ts(ts), ticker=ticker.upper(), kind=kind,
            title=title, body=body, source_url=source_url,
            metadata=json.dumps(metadata) if metadata else None,
            dedupe_key=dedupe_key,
        )
        return int(e.id)
    except IntegrityError:
        return None  # dedupe_key 冲突


def get_event(event_id: int) -> Event | None:
    """按 id 取一条 -> `Event` 实例,或 None。"""
    return Event.get_or_none(Event.id == event_id)


def list_events(
    ticker: str | None = None,
    kind: str | None = None,
    since: str | datetime | None = None,
) -> pd.DataFrame:
    """读取事件 -> DataFrame(UI 直接消费)。"""
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
    df = pd.read_sql(q, connect(), params=params)
    if not df.empty:
        # 各源时区混杂(UTC for news/earnings,naive for manual)—— 统一到 naive UTC
        # 后续直接 `==` / `<` 比较。
        df["ts"] = pd.to_datetime(
            df["ts"], utc=True, errors="coerce", format="mixed"
        ).dt.tz_localize(None)
    return df


def delete_event(event_id: int) -> bool:
    """按 id 删一条。返回是否删了一行。"""
    return Event.delete().where(Event.id == event_id).execute() > 0


def update_event(event_id: int, **fields) -> bool:
    """部分更新。允许字段: ts/ticker/kind/title/body/source_url。返回是否真的改了一行。"""
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

    n = Event.update(**updates).where(Event.id == event_id).execute()
    return n > 0


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
    lookback_days: int | None = None,
) -> int:
    """Flag days where |daily_return| > threshold * volatility_20 as 'anomaly' events.

    Idempotent via dedupe_key keyed on (ticker, date).
    `lookback_days`: if set, only scan the last N days of each ticker's history
    (default None = full history). Useful from the UI to limit work per click.

    Indicators are computed on the fly via load_daily (daily_return / volatility_20)
    so this only depends on prices_daily — no stale indicator columns.
    """
    from .prices import load_daily, list_tickers  # local import: avoid cycle

    if tickers:
        tickers = [t.upper() for t in tickers]
    else:
        tickers = list_tickers()

    if not tickers:
        print("[OK]   anomalies: no tickers")
        return 0

    cutoff = None
    if lookback_days is not None:
        cutoff = pd.Timestamp(
            (datetime.now() - pd.Timedelta(days=int(lookback_days))).date()
        )

    inserted = 0
    for ticker in tickers:
        df = load_daily(ticker)
        if df.empty or "daily_return" not in df.columns or "volatility_20" not in df.columns:
            continue
        sub = df.dropna(subset=["daily_return", "volatility_20"])
        if cutoff is not None:
            sub = sub[sub.index >= cutoff]
        if sub.empty:
            continue
        zscore = sub["daily_return"] / sub["volatility_20"]
        flagged = sub[zscore.abs() >= threshold]
        if flagged.empty:
            continue
        for date_idx, row in flagged.iterrows():
            d = date_idx.date().isoformat() if hasattr(date_idx, "date") else str(date_idx)
            z = float(zscore.loc[date_idx])
            when = f"{d}T16:00:00"  # market close convention
            key = f"anomaly:{ticker}:{d}"
            direction = "spike" if z > 0 else "drop"
            title = (
                f"{ticker} {direction} {row['daily_return']*100:+.2f}% "
                f"(z={z:+.2f})"
            )
            res = add_event(
                ticker=ticker,
                kind="anomaly",
                title=title,
                ts=when,
                metadata={
                    "daily_return": float(row["daily_return"]),
                    "zscore": z,
                    "close": float(row["close"]),
                },
                dedupe_key=key,
            )
            if res is not None:
                inserted += 1
    print(f"[OK]   anomalies: {inserted} new rows (threshold={threshold} sigma)")
    record_op("detect_anomalies", f"{inserted} new rows (threshold={threshold} sigma)")
    return inserted


def pull_all(
    tickers: Iterable[str],
    anomaly_threshold: float = 2.0,
    anomaly_lookback_days: int | None = None,
    on_progress: "Callable[[int, int, str], None] | None" = None,
) -> dict[str, int]:
    """Run every auto-pull in sequence: earnings, dividends, news, anomalies.

    `anomaly_lookback_days` limits the anomaly scan window (the other auto-pulls
    don't have a date-range knob — yfinance returns what it returns and the
    dedupe_key keeps things idempotent).
    `on_progress(step, total_steps, label)`: 每完成一步后回调,用于 UI 进度条。

    Returns a dict {op: inserted_rows}.
    """
    tickers = list(tickers)
    steps = [
        ("earnings",  "财报",  lambda: pull_earnings(tickers)),
        ("dividends", "分红",  lambda: pull_dividends(tickers)),
        ("news",      "新闻",  lambda: pull_news(tickers)),
        ("anomalies", "异动",  lambda: detect_anomalies(
            tickers, threshold=anomaly_threshold,
            lookback_days=anomaly_lookback_days)),
    ]
    results: dict[str, int] = {}
    for i, (key, label, fn) in enumerate(steps):
        results[key] = fn()
        if on_progress:
            on_progress(i + 1, len(steps), label)
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
