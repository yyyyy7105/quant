"""技术指标计算。指标列的唯一来源。

指标在 **加载时即时计算**(不再落库),数学函数统一复用 quant.mytt。
列名为小写,与 prices_daily 存储/加载的 OHLCV 格式一致。
"""

from __future__ import annotations

import pandas as pd

from . import mytt


def add_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """在原始 OHLCV(小写列)基础上追加常用指标列。

    要求列: close, volume(以及 high/low 供其它指标)。原地修改并返回 df。
    """
    close = df["close"]

    df["sma_20"] = close.rolling(20).mean()
    df["sma_50"] = close.rolling(50).mean()
    df["sma_200"] = close.rolling(200).mean()
    df["ema_20"] = close.ewm(span=20, adjust=False).mean()

    df["daily_return"] = close.pct_change()
    df["volatility_20"] = df["daily_return"].rolling(20).std()

    df["rsi_14"] = rsi(close, window=14)
    df["vol_sma_20"] = df["volume"].rolling(20).mean()

    return df


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's Relative Strength Index (0-100)。"""
    return mytt.RSI(close, window)


def macd(close: pd.Series, short: int = 12, long: int = 26, mid: int = 9) -> pd.DataFrame:
    """返回含 dif/dea/hist 列的 DataFrame(用于副图)。"""
    dif, dea, hist = mytt.MACD(close, short, long, mid)
    return pd.DataFrame({"dif": dif, "dea": dea, "hist": hist})


def kdj(high: pd.Series, low: pd.Series, close: pd.Series,
        n: int = 9, m1: int = 3, m2: int = 3) -> pd.DataFrame:
    """返回含 k/d/j 列的 DataFrame(用于副图)。"""
    k, d, j = mytt.KDJ(high, low, close, n, m1, m2)
    return pd.DataFrame({"k": k, "d": d, "j": j})


def boll(close: pd.Series, n: int = 20, p: float = 2.0) -> pd.DataFrame:
    """返回含 mid/upper/lower 列的 DataFrame(主图叠加)。"""
    mid, upper, lower = mytt.BOLL(close, n, p)
    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower})
