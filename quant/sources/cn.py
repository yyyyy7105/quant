"""A股行情数据源 —— akshare(前复权 qfq)。

akshare 依赖较重,采用函数内惰性导入,避免仅用美股的用户付出导入成本。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

# akshare 无 "5y" 之类的快捷参数,需显式日期区间
_PERIOD_YEARS = {"1y": 1, "2y": 2, "5y": 5, "max": 30}

_RENAME = {
    "日期": "date", "开盘": "open", "收盘": "close",
    "最高": "high", "最低": "low", "成交量": "volume",
}


def fetch_history(ticker: str, period: str = "5y") -> pd.DataFrame:
    """拉取 A股日线(前复权),归一化为小写列、date 索引。

    注意: akshare 的成交量单位是「手」(1 手 = 100 股),此处 ×100 转换为股,
    与美股口径一致。
    """
    import akshare as ak  # 惰性导入

    years = _PERIOD_YEARS.get(period, 5)
    end = datetime.now()
    start = end - timedelta(days=365 * years + 10)

    df = ak.stock_zh_a_hist(
        symbol=str(ticker),
        period="daily",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        adjust="qfq",
    )
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.rename(columns=_RENAME)[["date", "open", "high", "low", "close", "volume"]].copy()
    df["volume"] = df["volume"].astype("float64") * 100  # 手 -> 股
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return df
