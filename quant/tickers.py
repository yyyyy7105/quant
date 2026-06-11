"""自选股关注列表 (`tickers` 表) —— Peewee CRUD。

- 列表中的标的驱动「拉取最新行情」和「拉取全部事件」(无需 --tickers 参数)。
- 「移除」只是 `active = 0`,行不删,日线/事件历史永远保留。

简单 CRUD 走 `Ticker` 模型方法;`summary()` 的 JOIN 仍走 pandas + raw SQL。
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from .db import connect
from .models import Ticker


def add(ticker: str, notes: str | None = None, market: str = "US") -> bool:
    """登记 / 重激活一只标的。返回 True 表示新增,False 表示已存在被重激活。"""
    t = ticker.upper()
    market = market.upper()
    existing: Ticker | None = Ticker.get_or_none(Ticker.ticker == t)
    if existing is None:
        Ticker.create(
            ticker=t,
            added_at=datetime.now().isoformat(timespec="seconds"),
            active=1,
            notes=notes,
            market=market,
        )
        return True
    # 已存在 -> 重激活 + 可选刷新备注 + 覆盖市场。COALESCE 保留旧 notes 的语义,
    # Peewee 没有内置 COALESCE 表达 update,这里手写 SQL 一行(简洁清晰)。
    conn = connect()
    conn.execute(
        "UPDATE tickers SET active = 1, notes = COALESCE(?, notes), market = ? "
        "WHERE ticker = ?",
        (notes, market, t),
    )
    conn.commit()
    return False


def remove(ticker: str) -> bool:
    """停用一只标的(`active=0`,历史保留)。返回 True 表示行存在并被改动。"""
    t = ticker.upper()
    n = Ticker.update(active=0).where(Ticker.ticker == t).execute()
    return n > 0


def get(ticker: str) -> Ticker | None:
    """单行查询 -> `Ticker` 实例,或 None。"""
    return Ticker.get_or_none(Ticker.ticker == ticker.upper())


def get_active(market: str | None = None) -> list[str]:
    """当前激活的标的 ticker 列表。传 market 则按市场过滤。"""
    q = Ticker.select(Ticker.ticker).where(Ticker.active == 1)
    if market:
        q = q.where(Ticker.market == market.upper())
    q = q.order_by(Ticker.ticker)
    return [t.ticker for t in q]


def all_tickers(market: str | None = None) -> list[Ticker]:
    """全部已登记标的(含已停用)。"""
    q = Ticker.select()
    if market:
        q = q.where(Ticker.market == market.upper())
    q = q.order_by(Ticker.active.desc(), Ticker.ticker)
    return list(q)


def summary() -> pd.DataFrame:
    """带价格统计的总览(供 UI 表格用) —— 涉及与 prices_daily 的 JOIN,仍走原生 SQL。"""
    conn = connect()
    df = pd.read_sql(
        """
        SELECT
            t.ticker,
            t.market,
            t.active,
            t.added_at,
            t.notes,
            COUNT(p.date)  AS price_rows,
            MIN(p.date)    AS earliest,
            MAX(p.date)    AS latest,
            MAX(p.close)   AS last_close
        FROM tickers t
        LEFT JOIN prices_daily p ON p.ticker = t.ticker
        GROUP BY t.ticker
        ORDER BY t.active DESC, t.ticker
        """,
        conn,
    )
    if not df.empty:
        df["active"] = df["active"].map({1: "yes", 0: "no"})
    return df
