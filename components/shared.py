"""组件间共享的常量与工具函数。"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from quant import tickers as tk
from quant.prices import list_tickers, load_daily

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
PAGES = ["投资组合", "个股看板", "回测", "选股器", "交易记录", "事件流", "自选股"]

MARKETS = {"美股 (US)": "US", "A股 (CN)": "CN"}

# 增量更新窗口选项 —— 与 yfinance 原生 period 字符串一致
PERIOD_OPTIONS = ["1d", "5d", "1mo", "6mo", "1y", "5y"]
PERIOD_LABELS = {
    "1d":  "1天",
    "5d":  "1周(5个交易日)",
    "1mo": "1月",
    "6mo": "6月",
    "1y":  "1年",
    "5y":  "5年",
}
# period 字符串 -> 天数(供事件侧 anomaly_lookback_days 用)
PERIOD_DAYS = {"1d": 1, "5d": 5, "1mo": 30, "6mo": 180, "1y": 365, "5y": 1825}

EVENT_STYLE = {
    "earnings": ("★", "#9b59b6"),
    "dividend": ("$", "#27ae60"),
    "news":     ("📰", "#3498db"),
    "anomaly":  ("⚠️", "#e74c3c"),
    "manual":   ("●", "#f39c12"),
}

# 概览/事件等界面中文标签
KIND_LABELS = {
    "earnings": "财报", "dividend": "分红", "news": "新闻",
    "anomaly": "异动", "manual": "手动",
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def market_ticker_set(market: str) -> set[str]:
    """当前市场下的全部标的集合 —— 用于把交易/事件等「无 market 列」的表按市场过滤。

    取并集:① 已在 tickers 表登记到该市场(含已停用) + ② 有日线数据在该市场。
    覆盖「登记但拉取失败」和「老数据但已停用」两种边缘情况。
    """
    summary = tk.summary()
    registered = (set(summary[summary["market"] == market]["ticker"])
                  if not summary.empty else set())
    with_data = set(list_tickers(market))
    return registered | with_data


@st.cache_data(show_spinner=False)
def cached_load_daily(ticker: str) -> pd.DataFrame:
    """会话内缓存的 load_daily —— 同一标的的「原始 OHLCV + 即时指标」只算一次。

    Streamlit 每次交互(拖动滑块、勾选 checkbox)都会重跑脚本。若直接调 load_daily,
    会反复读 SQLite + 重算 sma/rsi/macd... 这里用 @st.cache_data 把结果按 ticker
    缓存在内存(不落库)。日期范围切片在缓存的 df 上做,瞬时完成。
    缓存在每次 fetch_daily 后由侧边栏按钮调用 .clear() 失效。
    """
    return load_daily(ticker)


def humanize_age(ts_str: str | None) -> str:
    """'2026-06-05T21:39:30' -> '刚刚' / '5 分钟前' / '2 小时前' / '3 天前'。"""
    if not ts_str:
        return "从未"
    try:
        ts = datetime.fromisoformat(ts_str)
    except ValueError:
        return ts_str
    secs = (datetime.now() - ts).total_seconds()
    if secs < 60:
        return "刚刚"
    if secs < 3600:
        return f"{int(secs // 60)} 分钟前"
    if secs < 86400:
        return f"{int(secs // 3600)} 小时前"
    return f"{int(secs // 86400)} 天前"
