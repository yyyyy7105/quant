"""美股 / 全球行情数据源 —— yfinance。"""

from __future__ import annotations

import pandas as pd
import yfinance as yf

_RENAME = {"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}


def fetch_history(ticker: str, period: str = "5y") -> pd.DataFrame:
    """拉取日线 OHLCV,归一化为小写列、date 索引。空则返回空 DataFrame。"""
    df = yf.Ticker(ticker).history(period=period, interval="1d")
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.rename(columns=_RENAME)[["open", "high", "low", "close", "volume"]].copy()

    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    df.index.name = "date"
    return df
