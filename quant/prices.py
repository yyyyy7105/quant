"""Price fetching: daily (long-history cache) + intraday (on-demand event windows)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

import pandas as pd
import yfinance as yf

from .db import connect
from .metrics import add_metrics
from .oplog import record as record_op
from .sources import cn as cn_source
from .sources import us as us_source

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

# prices_daily 仅存原始 OHLCV + market;指标在 load_daily 时即时计算
_DAILY_COLS = ["ticker", "date", "open", "high", "low", "close", "volume", "market"]


def fetch_daily(
    tickers: Iterable[str], period: str = "5y", market: str = "US"
) -> dict[str, int]:
    """按市场拉取日线原始 OHLCV 并写入 `prices_daily`(不再落库指标)。

    market='US' 走 yfinance,'CN' 走 akshare(前复权)。返回 {ticker: rows_written}。
    """
    market = market.upper()
    source = cn_source if market == "CN" else us_source

    written: dict[str, int] = {}
    with connect() as conn:
        for ticker in tickers:
            try:
                df = source.fetch_history(ticker, period=period)
            except Exception as e:  # 单个标的失败不影响其它
                print(f"[WARN] {ticker} ({market}): fetch failed: {e}")
                written[ticker] = 0
                continue
            if df is None or df.empty:
                print(f"[WARN] {ticker} ({market}): no data returned")
                written[ticker] = 0
                continue

            rows = _daily_to_rows(ticker, df, market)
            conn.executemany(
                f"INSERT OR REPLACE INTO prices_daily ({','.join(_DAILY_COLS)}) "
                f"VALUES ({','.join('?' * len(_DAILY_COLS))})",
                rows,
            )
            conn.commit()
            written[ticker] = len(rows)
            print(
                f"[OK]   {ticker} ({market}): {len(rows)} rows "
                f"{df.index[0].date()} -> {df.index[-1].date()}, "
                f"latest Close={df['close'].iloc[-1]:.2f}"
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
    """读取某标的的原始日线,**即时计算指标后再按区间切片**。

    指标(SMA/RSI 等)需要完整历史做滚动窗口预热,因此先在全量历史上计算,
    再切到 [start, end],避免窗口起点出现整段 NaN。返回列含小写 OHLCV + 指标。
    """
    with connect() as conn:
        df = pd.read_sql(
            "SELECT * FROM prices_daily WHERE ticker = ? ORDER BY date",
            conn, params=[ticker.upper()], parse_dates=["date"],
        )
    if df.empty:
        return df

    df = df.set_index("date")
    df = add_metrics(df)
    if start:
        df = df.loc[str(start):]
    if end:
        df = df.loc[:str(end)]
    return df


def list_tickers(market: str | None = None) -> list[str]:
    """有日线数据的全部标的;传 market 则按市场过滤。"""
    q = "SELECT DISTINCT ticker FROM prices_daily"
    params: list = []
    if market:
        q += " WHERE market = ?"
        params.append(market.upper())
    q += " ORDER BY ticker"
    with connect() as conn:
        rows = conn.execute(q, params).fetchall()
    return [r["ticker"] for r in rows]


def _daily_to_rows(ticker: str, df: pd.DataFrame, market: str = "US") -> list[tuple]:
    t = ticker.upper()
    market = market.upper()
    out: list[tuple] = []
    for ts, row in df.iterrows():
        out.append(
            (
                t,
                ts.date().isoformat(),
                _f(row.get("open")),
                _f(row.get("high")),
                _f(row.get("low")),
                _f(row.get("close")),
                _i(row.get("volume")),
                market,
            )
        )
    return out


def _f(v) -> float | None:
    return float(v) if v is not None and pd.notna(v) else None


def _i(v) -> int | None:
    return int(v) if v is not None and pd.notna(v) else None
