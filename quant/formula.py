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
) -> pd.DataFrame:
    """对每个标的求值并汇总命中结果。

    mode='latest': 仅看最新一根 K 线;
    mode='recent': 最近 lookback 根内任意一根命中即算命中。
    返回列: ticker, last_close, match_date, status。
    """
    rows: list[dict] = []
    for t in tickers:
        try:
            df = load_daily(t)
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
# 公式持久化 (formulas 表)
# ---------------------------------------------------------------------------
def save_formula(name: str, expr: str, description: str | None = None) -> int:
    """新增或覆盖同名公式,返回行 id。"""
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO formulas (name, expr, description, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                expr = excluded.expr,
                description = excluded.description,
                created_at = excluded.created_at
            """,
            (name, expr, description, now),
        )
        conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        row = conn.execute("SELECT id FROM formulas WHERE name = ?", (name,)).fetchone()
        return row["id"] if row else -1


def list_formulas() -> pd.DataFrame:
    with connect() as conn:
        return pd.read_sql("SELECT * FROM formulas ORDER BY name", conn)


def get_formula(name: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM formulas WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def delete_formula(name: str) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM formulas WHERE name = ?", (name,))
        conn.commit()
        return cur.rowcount > 0
