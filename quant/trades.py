"""交易日志 (`trades` 表) —— Peewee CRUD + 派生持仓。

`total_cost` 列已废弃(由 db.py 的迁移移除)—— 加载时按 `qty*price ± fees` 派生。
聚合(positions, list_trades)走 pandas 友好的原生 SQL,简单 CRUD 走 Peewee。
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from .db import connect
from .models import Trade


def add_trade(
    ticker: str,
    side: str,
    qty: float,
    price: float,
    ts: str | datetime | None = None,
    fees: float = 0.0,
    notes: str | None = None,
    tags: str | None = None,
) -> int:
    """记录一笔买卖。返回新行 id。"""
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be BUY or SELL, got {side!r}")
    t = Trade.create(
        ts=_normalize_ts(ts), ticker=ticker.upper(), side=side,
        qty=qty, price=price, fees=fees,
        notes=notes, tags=tags,
    )
    return int(t.id)


def get_trade(trade_id: int) -> Trade | None:
    """按 id 取一条 -> `Trade` 实例,或 None。"""
    return Trade.get_or_none(Trade.id == trade_id)


def list_trades(
    ticker: str | None = None,
    since: str | datetime | None = None,
) -> pd.DataFrame:
    """按筛选条件读取交易 -> DataFrame(UI 直接消费)。"""
    q = "SELECT * FROM trades WHERE 1=1"
    params: list = []
    if ticker:
        q += " AND ticker = ?"
        params.append(ticker.upper())
    if since:
        q += " AND ts >= ?"
        params.append(_normalize_ts(since))
    q += " ORDER BY ts"
    df = pd.read_sql(q, connect(), params=params)
    if not df.empty:
        df["ts"] = pd.to_datetime(
            df["ts"], utc=True, errors="coerce", format="mixed"
        ).dt.tz_localize(None)
    return df


def delete_trade(trade_id: int) -> bool:
    """按 id 删一条。返回是否删了一行。"""
    return Trade.delete().where(Trade.id == trade_id).execute() > 0


def update_trade(trade_id: int, **fields) -> bool:
    """部分更新。返回是否真的改了一行。允许字段: ts/ticker/side/qty/price/fees/notes/tags。"""
    allowed = {"ts", "ticker", "side", "qty", "price", "fees", "notes", "tags"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return False

    if "ticker" in updates:
        updates["ticker"] = updates["ticker"].upper()
    if "side" in updates:
        updates["side"] = updates["side"].upper()
        if updates["side"] not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY or SELL, got {updates['side']!r}")
    if "ts" in updates:
        updates["ts"] = _normalize_ts(updates["ts"])

    n = Trade.update(**updates).where(Trade.id == trade_id).execute()
    return n > 0


def positions() -> pd.DataFrame:
    """当前持仓:按 ticker 求净仓 + 加权平均成本。

    若 prices_daily 有数据则附最新收盘价 + 浮动盈亏。返回固定 8 列 DataFrame。
    """
    trades_df = list_trades()
    if trades_df.empty:
        return pd.DataFrame(
            columns=["ticker", "qty", "avg_cost", "cost_basis",
                     "last_close", "as_of", "market_value", "unrealized_pl"]
        )

    rows: list[dict] = []
    for ticker, group in trades_df.groupby("ticker"):
        net_qty = 0.0
        cost_basis = 0.0
        for r in group.itertuples(index=False):
            side = str(r.side)
            qty, price, fees = float(r.qty), float(r.price), float(r.fees)
            total_cost = qty * price + (fees if side == "BUY" else -fees)
            if side == "BUY":
                cost_basis += total_cost
                net_qty   += qty
            else:  # SELL: 按比例减成本
                if net_qty > 0:
                    avg = cost_basis / net_qty
                    cost_basis -= avg * qty
                net_qty -= qty
        if abs(net_qty) < 1e-9:
            continue
        rows.append({
            "ticker":     ticker,
            "qty":        round(net_qty, 6),
            "avg_cost":   round(cost_basis / net_qty, 4) if net_qty else None,
            "cost_basis": round(cost_basis, 2),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # 附最新收盘 + 来源日期(JOIN 仍走 SQL)
    last = pd.read_sql(
        """
        SELECT p.ticker, p.close AS last_close, p.date AS as_of
        FROM prices_daily p
        JOIN (
            SELECT ticker, MAX(date) AS max_date FROM prices_daily GROUP BY ticker
        ) m ON m.ticker = p.ticker AND m.max_date = p.date
        """,
        connect(),
    )
    df = df.merge(last, on="ticker", how="left")
    df["market_value"]  = (df["qty"] * df["last_close"]).round(2)
    df["unrealized_pl"] = (df["market_value"] - df["cost_basis"]).round(2)
    return df


def _normalize_ts(ts: str | datetime | None) -> str:
    if ts is None:
        return datetime.now().isoformat(timespec="seconds")
    if isinstance(ts, datetime):
        return ts.isoformat(timespec="seconds")
    try:
        return datetime.fromisoformat(ts).isoformat(timespec="seconds")
    except ValueError:
        return datetime.strptime(ts, "%Y-%m-%d").isoformat(timespec="seconds")
