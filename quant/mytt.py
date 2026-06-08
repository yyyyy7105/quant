"""MyTT — 通达信/同花顺 公式函数的纯 Python 实现 (pandas/numpy)。

设计原则:
- **纯数学运算,无任何网络/文件 I/O** —— 公式与数据不会外泄,可放心解释外部公式。
- 所有函数输入/输出均为 pandas.Series(与 K 线对齐),布尔条件返回 bool Series。
- 作为指标计算的唯一数学来源: quant.metrics 与选股器 quant.formula 都复用本模块。

参考自公开的 MyTT 项目(MIT),此处为精简、可审计的重写版本。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "REF", "MA", "EMA", "SMA", "DMA", "WMA",
    "HHV", "LLV", "SUM", "STD", "COUNT",
    "CROSS", "ABS", "MAX", "MIN", "IF", "EVERY", "EXIST",
    "RSI", "MACD", "KDJ", "BOLL",
]


# ---------------------------------------------------------------------------
# 基础位移 / 均线
# ---------------------------------------------------------------------------
def REF(s: pd.Series, n: int = 1) -> pd.Series:
    """向前引用 n 周期前的值。"""
    return s.shift(int(n))


def MA(s: pd.Series, n: int) -> pd.Series:
    """简单移动平均。"""
    return s.rolling(int(n)).mean()


def EMA(s: pd.Series, n: int) -> pd.Series:
    """指数移动平均 (span=n)。"""
    return s.ewm(span=int(n), adjust=False).mean()


def SMA(s: pd.Series, n: int, m: int = 1) -> pd.Series:
    """中国式 SMA: Y = (M*X + (N-M)*Y') / N,等价 ewm(alpha=M/N)。"""
    return s.ewm(alpha=m / n, adjust=False).mean()


def WMA(s: pd.Series, n: int) -> pd.Series:
    """加权移动平均,权重 1..n。"""
    n = int(n)
    weights = np.arange(1, n + 1)
    return s.rolling(n).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)


def DMA(s: pd.Series, a: float) -> pd.Series:
    """动态移动平均 (固定平滑系数 a)。"""
    return s.ewm(alpha=a, adjust=False).mean()


# ---------------------------------------------------------------------------
# 区间统计
# ---------------------------------------------------------------------------
def HHV(s: pd.Series, n: int) -> pd.Series:
    """n 周期内最高值。"""
    return s.rolling(int(n)).max()


def LLV(s: pd.Series, n: int) -> pd.Series:
    """n 周期内最低值。"""
    return s.rolling(int(n)).min()


def SUM(s: pd.Series, n: int) -> pd.Series:
    """n 周期求和 (n<=0 表示累计求和)。"""
    n = int(n)
    return s.cumsum() if n <= 0 else s.rolling(n).sum()


def STD(s: pd.Series, n: int) -> pd.Series:
    """n 周期样本标准差。"""
    return s.rolling(int(n)).std()


def COUNT(cond: pd.Series, n: int) -> pd.Series:
    """统计最近 n 周期内条件成立的次数。"""
    return pd.Series(cond, dtype="float64").rolling(int(n)).sum()


# ---------------------------------------------------------------------------
# 逻辑 / 比较
# ---------------------------------------------------------------------------
def CROSS(s1, s2) -> pd.Series:
    """金叉判断: s1 上穿 s2 当根为 True。"""
    s1 = _as_series(s1, s2)
    s2 = _as_series(s2, s1)
    return (s1 > s2) & (s1.shift(1) <= s2.shift(1))


def ABS(s: pd.Series) -> pd.Series:
    return s.abs() if isinstance(s, pd.Series) else abs(s)


def MAX(a, b):
    if isinstance(a, pd.Series) or isinstance(b, pd.Series):
        return np.maximum(a, b)
    return max(a, b)


def MIN(a, b):
    if isinstance(a, pd.Series) or isinstance(b, pd.Series):
        return np.minimum(a, b)
    return min(a, b)


def IF(cond, a, b):
    """逐元素三目: cond ? a : b。"""
    return pd.Series(np.where(cond, a, b), index=_index_of(cond, a, b))


def EVERY(cond: pd.Series, n: int) -> pd.Series:
    """最近 n 周期内条件是否始终成立。"""
    return COUNT(cond, n) == int(n)


def EXIST(cond: pd.Series, n: int) -> pd.Series:
    """最近 n 周期内条件是否曾经成立。"""
    return COUNT(cond, n) > 0


# ---------------------------------------------------------------------------
# 常用指标 (返回 Series 或 (Series, ...) 元组)
# ---------------------------------------------------------------------------
def RSI(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI (0-100)。"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def MACD(close: pd.Series, short: int = 12, long: int = 26, mid: int = 9):
    """返回 (DIF, DEA, MACD柱)。MACD柱 = (DIF-DEA)*2 (通达信口径)。"""
    dif = EMA(close, short) - EMA(close, long)
    dea = EMA(dif, mid)
    hist = (dif - dea) * 2
    return dif, dea, hist


def KDJ(high: pd.Series, low: pd.Series, close: pd.Series,
        n: int = 9, m1: int = 3, m2: int = 3):
    """返回 (K, D, J)。"""
    llv = LLV(low, n)
    hhv = HHV(high, n)
    rsv = (close - llv) / (hhv - llv) * 100
    rsv = rsv.replace([np.inf, -np.inf], np.nan).fillna(50)
    k = SMA(rsv, m1, 1)
    d = SMA(k, m2, 1)
    j = 3 * k - 2 * d
    return k, d, j


def BOLL(close: pd.Series, n: int = 20, p: float = 2.0):
    """返回 (MID, UPPER, LOWER)。"""
    mid = MA(close, n)
    sd = STD(close, n)
    return mid, mid + p * sd, mid - p * sd


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _as_series(x, like) -> pd.Series:
    if isinstance(x, pd.Series):
        return x
    idx = like.index if isinstance(like, pd.Series) else None
    return pd.Series(x, index=idx)


def _index_of(*objs):
    for o in objs:
        if isinstance(o, pd.Series):
            return o.index
    return None
