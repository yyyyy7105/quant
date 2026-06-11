"""选股器:解释 MyTT 格式的外部公式,扫描自选股是否命中。

这是 **筛选器,不是回测** —— 不涉及买卖点 / 收益 / P&L,只判断「条件当前是否成立」。

公式在 **受限命名空间** 中 eval:
- 禁用所有内置(__builtins__ 置空),无 import、无属性访问到危险对象;
- 仅暴露 quant.mytt 的白名单函数 + 价格序列 CLOSE/OPEN/HIGH/LOW/VOL。
公式与数据全程本地计算,不外泄(mytt 纯数学,无网络/文件 I/O)。
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from . import mytt
from .db import connect
from .models import Formula
from .prices import load_daily

# 白名单函数命名空间(来自 mytt.__all__)
_FUNCS = {name: getattr(mytt, name) for name in mytt.__all__}


def _namespace(df: pd.DataFrame) -> dict:
    ns = dict(_FUNCS)
    ns.update(
        {
            "CLOSE": df["close"], "C": df["close"],
            "OPEN": df["open"], "O": df["open"],
            "HIGH": df["high"], "H": df["high"],
            "LOW": df["low"], "L": df["low"],
            "VOL": df["volume"], "V": df["volume"],
        }
    )
    return ns


def evaluate(expr: str, df: pd.DataFrame) -> pd.Series:
    """在受限命名空间中求值,返回与 df 对齐的布尔 Series。"""
    if not expr or not expr.strip():
        raise ValueError("公式为空")
    result = eval(expr, {"__builtins__": {}}, _namespace(df))  # noqa: S307 受限命名空间
    if isinstance(result, pd.Series):
        return result.reindex(df.index).fillna(False).astype(bool)
    if isinstance(result, np.ndarray):
        return pd.Series(result, index=df.index).fillna(False).astype(bool)
    # 标量布尔 -> 广播
    return pd.Series(bool(result), index=df.index)


def scan(
    expr: str,
    tickers: list[str],
    mode: str = "latest",
    lookback: int = 1,
    loader=None,
) -> pd.DataFrame:
    """对每个标的求值并汇总命中结果。

    mode='latest': 仅看最新一根 K 线;
    mode='recent': 最近 lookback 根内任意一根命中即算命中。
    loader: 自定义加载函数(默认 load_daily)。在 Streamlit 中传入 @st.cache_data
            包装版本可避免重复读 SQLite + 重算指标。
    返回列: ticker, last_close, match_date, status。
    """
    _load = loader or load_daily
    rows: list[dict] = []
    for t in tickers:
        try:
            df = _load(t)
            if df.empty:
                rows.append({"ticker": t, "last_close": None, "match_date": None,
                             "status": "无数据"})
                continue
            sig = evaluate(expr, df)
            last_close = float(df["close"].iloc[-1])
            if mode == "recent":
                window = sig.iloc[-int(lookback):]
                matched = bool(window.any())
                hit_idx = window[window].index
                mdate = hit_idx[-1].date().isoformat() if len(hit_idx) else None
            else:
                matched = bool(sig.iloc[-1])
                mdate = df.index[-1].date().isoformat() if matched else None
            if matched:
                rows.append({"ticker": t, "last_close": last_close,
                             "match_date": mdate, "status": "命中"})
        except Exception as e:  # 单只标的公式出错不影响整体
            rows.append({"ticker": t, "last_close": None, "match_date": None,
                         "status": f"错误: {e}"})
    return pd.DataFrame(rows, columns=["ticker", "last_close", "match_date", "status"])


# ---------------------------------------------------------------------------
# 公式持久化 (formulas 表) —— Peewee
# ---------------------------------------------------------------------------
def save_formula(name: str, expr: str, description: str | None = None) -> int:
    """新增或覆盖同名公式,返回行 id。

    Peewee 的 `insert(...).on_conflict_replace()` 在 SQLite 上等价于
    `INSERT OR REPLACE`,正好覆盖「同名 -> 重写」语义。
    """
    now = datetime.now().isoformat(timespec="seconds")
    Formula.insert(
        name=name, expr=expr, description=description, created_at=now,
    ).on_conflict_replace().execute()
    f = Formula.get_or_none(Formula.name == name)
    return int(f.id) if f else -1


def list_formulas() -> pd.DataFrame:
    """列出全部已存公式 -> DataFrame(供 UI 表格用)。"""
    return pd.read_sql("SELECT * FROM formulas ORDER BY name", connect())


def get_formula(name: str) -> Formula | None:
    """按名称取一条 -> `Formula` 实例,或 None。"""
    return Formula.get_or_none(Formula.name == name)


def delete_formula(name: str) -> bool:
    """按名称删除。返回是否删了一行。"""
    return Formula.delete().where(Formula.name == name).execute() > 0
