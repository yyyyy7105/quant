"""A股行情数据源 —— akshare(前复权 qfq)。

akshare 依赖较重,采用函数内惰性导入,避免仅用美股的用户付出导入成本。

双数据源策略(均为 akshare 内置,无 token):
  1. 首选 eastmoney:`stock_zh_a_hist`(6 位代码, 成交量单位「手」)
  2. 失败回退 sina:`stock_zh_a_daily`(sh/sz 前缀代码, 成交量单位「股」)
两者本身均会偶发 RemoteDisconnected / ConnectionError,各自带短退避重试。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pandas as pd

# akshare 无 "5y" 之类的快捷参数,需显式日期区间。用「天」做单位以同时支持
# 日级别(1d/5d)和年级别(1y/5y)的 period 字符串。
_PERIOD_DAYS = {
    "1d": 1, "5d": 5,
    "1mo": 30, "3mo": 90, "6mo": 180,
    "1y": 365, "2y": 730, "5y": 1825, "10y": 3650,
    "max": 365 * 30,
}

_RENAME_EASTMONEY = {
    "日期": "date", "开盘": "open", "收盘": "close",
    "最高": "high", "最低": "low", "成交量": "volume",
}

# 重试参数:接口偶发断连,短退避足以恢复
_MAX_ATTEMPTS = 3
_BACKOFF_SEC = (1.0, 2.0)  # 第 2/3 次尝试前的等待


def _sh_sz_prefix(ticker: str) -> str:
    """600/601/603/605/688... -> sh; 000/002/300... -> sz。"""
    t = str(ticker).strip()
    if t.lower().startswith(("sh", "sz")):
        return t.lower()
    if t.startswith("6"):
        return f"sh{t}"
    return f"sz{t}"  # 0xx / 3xx 默认深市


def _try_with_retry(fn, label: str, ticker: str) -> pd.DataFrame | None:
    """运行 fn(),失败短退避重试。全部失败返回 None(不抛,交给上层尝试下一源)。"""
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return fn()
        except Exception as e:
            if attempt < _MAX_ATTEMPTS - 1:
                wait = _BACKOFF_SEC[attempt]
                print(f"[RETRY] {ticker} ({label}) attempt {attempt+1}/{_MAX_ATTEMPTS} "
                      f"failed ({type(e).__name__}); waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"[WARN] {ticker} ({label}) exhausted retries: {type(e).__name__}")
                return None


def _fetch_eastmoney(ak, ticker: str, sd: str, ed: str) -> pd.DataFrame:
    """eastmoney 源:6 位代码、成交量为「手」-> 转股。"""
    df = ak.stock_zh_a_hist(
        symbol=str(ticker), period="daily",
        start_date=sd, end_date=ed, adjust="qfq",
    )
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns=_RENAME_EASTMONEY)[
        ["date", "open", "high", "low", "close", "volume"]
    ].copy()
    df["volume"] = df["volume"].astype("float64") * 100  # 手 -> 股
    return df


def _fetch_sina(ak, ticker: str, sd: str, ed: str) -> pd.DataFrame:
    """sina 源:sh/sz 前缀、列名已英文小写、成交量已是「股」。"""
    sym = _sh_sz_prefix(ticker)
    df = ak.stock_zh_a_daily(
        symbol=sym, adjust="qfq", start_date=sd, end_date=ed,
    )
    if df is None or df.empty:
        return pd.DataFrame()
    df = df[["date", "open", "high", "low", "close", "volume"]].copy()
    df["volume"] = df["volume"].astype("float64")
    return df


def fetch_history(ticker: str, period: str = "5y") -> pd.DataFrame:
    """拉取 A股日线(前复权),归一化为小写列、date 索引。

    先试 eastmoney(带重试),再回退 sina(带重试)。两源成交量都已对齐到「股」,
    与美股口径一致。两源都失败则抛出最后一次异常,上层 fetch_daily 记 [WARN]。
    """
    import akshare as ak  # 惰性导入

    days = _PERIOD_DAYS.get(period, 365 * 5)
    end = datetime.now()
    start = end - timedelta(days=days + 5)  # +5 缓冲(周末/节假日)
    sd, ed = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    df = _try_with_retry(lambda: _fetch_eastmoney(ak, ticker, sd, ed),
                         label="eastmoney", ticker=ticker)
    if df is None or df.empty:
        print(f"[INFO] {ticker} (CN): eastmoney failed/empty, trying sina...")
        df = _try_with_retry(lambda: _fetch_sina(ak, ticker, sd, ed),
                             label="sina", ticker=ticker)

    if df is None or df.empty:
        raise RuntimeError(f"{ticker}: both eastmoney and sina failed")

    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return df
