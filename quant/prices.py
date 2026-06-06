"""Price fetching: daily (long-history cache) + intraday (on-demand event windows)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

import pandas as pd
import yfinance as yf

from .db import connect
from .metrics import add_metrics
from .oplog import record as record_op

# yfinance intraday history limits (approx)
INTRADAY_MAX_DAYS = {
    "1m": 7,
    "2m": 60,
    "5m": 60,
    "15m": 60,
    "30m": 60,
    "60m": 730,
    "90m": 60,
    "1h": 730,
}

_DAILY_COLS = [
    "ticker", "date", "open", "high", "low", "close", "volume",
    "sma_20", "sma_50", "sma_200", "ema_20",
    "daily_return", "volatility_20", "rsi_14", "vol_sma_20",
]


def fetch_daily(tickers: Iterable[str], period: str = "5y") -> dict[str, int]:
    """Fetch daily OHLCV + metrics for each ticker and upsert into `prices_daily`.

    Returns {ticker: rows_written}.
    """
    written: dict[str, int] = {}
    with connect() as conn:
        for ticker in tickers:
            df = yf.Ticker(ticker).history(period=period, interval="1d")
            if df.empty:
                print(f"[WARN] {ticker}: no data returned")
                written[ticker] = 0
                continue

            df = add_metrics(df)
            rows = _daily_to_rows(ticker, df)
            conn.executemany(
                f"INSERT OR REPLACE INTO prices_daily ({','.join(_DAILY_COLS)}) "
                f"VALUES ({','.join('?' * len(_DAILY_COLS))})",
                rows,
            )
            conn.commit()
            written[ticker] = len(rows)
            print(
                f"[OK]   {ticker}: {len(rows)} rows "
                f"{df.index[0].date()} -> {df.index[-1].date()}, "
                f"latest Close={df['Close'].iloc[-1]:.2f}"
            )
    total_rows = sum(written.values())
    record_op("fetch_daily", f"{len(written)} tickers, {total_rows} rows")
    return written


def fetch_intraday(
    ticker: str,
    start: str | datetime,
    end: str | datetime,
    interval: str = "5m",
) -> int:
    """Pull intraday bars for an event window and upsert into `prices_intraday`.

    Returns rows written. Warns if the window likely exceeds yfinance's limit.
    """
    if isinstance(start, str):
        start = datetime.fromisoformat(start)
    if isinstance(end, str):
        end = datetime.fromisoformat(end)

    max_days = INTRADAY_MAX_DAYS.get(interval)
    if max_days is not None:
        cutoff = datetime.now() - timedelta(days=max_days)
        if start < cutoff:
            print(
                f"[WARN] interval={interval} only supports last ~{max_days} days; "
                f"start={start.date()} may be truncated by Yahoo."
            )

    df = yf.Ticker(ticker).history(
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        interval=interval,
    )
    if df.empty:
        print(f"[WARN] {ticker} intraday: no data for {start.date()}..{end.date()}")
        return 0

    rows = [
        (
            ticker,
            ts.isoformat(),
            interval,
            float(row.Open) if pd.notna(row.Open) else None,
            float(row.High) if pd.notna(row.High) else None,
            float(row.Low) if pd.notna(row.Low) else None,
            float(row.Close) if pd.notna(row.Close) else None,
            int(row.Volume) if pd.notna(row.Volume) else None,
        )
        for ts, row in df.iterrows()
    ]
    with connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO prices_intraday "
            "(ticker, ts, interval, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    print(f"[OK]   {ticker} {interval}: {len(rows)} intraday rows")
    record_op("fetch_intraday", f"{ticker} {interval} {start.date()}..{end.date()}, {len(rows)} rows")
    return len(rows)


def load_daily(
    ticker: str,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Read enriched daily bars for a ticker from the DB into a DataFrame."""
    q = "SELECT * FROM prices_daily WHERE ticker = ?"
    params: list = [ticker]
    if start:
        q += " AND date >= ?"
        params.append(start)
    if end:
        q += " AND date <= ?"
        params.append(end)
    q += " ORDER BY date"
    with connect() as conn:
        df = pd.read_sql(q, conn, params=params, parse_dates=["date"])
    return df.set_index("date") if not df.empty else df


def list_tickers() -> list[str]:
    """All tickers that currently have any daily rows."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM prices_daily ORDER BY ticker"
        ).fetchall()
    return [r["ticker"] for r in rows]


def _daily_to_rows(ticker: str, df: pd.DataFrame) -> list[tuple]:
    out: list[tuple] = []
    for ts, row in df.iterrows():
        out.append(
            (
                ticker,
                ts.date().isoformat(),
                _f(row.get("Open")),
                _f(row.get("High")),
                _f(row.get("Low")),
                _f(row.get("Close")),
                _i(row.get("Volume")),
                _f(row.get("SMA_20")),
                _f(row.get("SMA_50")),
                _f(row.get("SMA_200")),
                _f(row.get("EMA_20")),
                _f(row.get("Daily_Return")),
                _f(row.get("Volatility_20")),
                _f(row.get("RSI_14")),
                _f(row.get("Vol_SMA_20")),
            )
        )
    return out


def _f(v) -> float | None:
    return float(v) if v is not None and pd.notna(v) else None


def _i(v) -> int | None:
    return int(v) if v is not None and pd.notna(v) else None
