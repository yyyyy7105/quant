"""Technical indicator math. Single source of truth for metric columns."""

from __future__ import annotations

import pandas as pd


def add_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Append commonly used technical indicators as new columns.

    Expects columns: Close, Volume. Modifies `df` in place and returns it.
    Extend this function to add MACD / Bollinger Bands / etc.
    """
    close = df["Close"]

    df["SMA_20"] = close.rolling(20).mean()
    df["SMA_50"] = close.rolling(50).mean()
    df["SMA_200"] = close.rolling(200).mean()
    df["EMA_20"] = close.ewm(span=20, adjust=False).mean()

    df["Daily_Return"] = close.pct_change()
    df["Volatility_20"] = df["Daily_Return"].rolling(20).std()

    df["RSI_14"] = rsi(close, window=14)
    df["Vol_SMA_20"] = df["Volume"].rolling(20).mean()

    return df


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's Relative Strength Index (0-100)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))
