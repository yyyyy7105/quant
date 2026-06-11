"""操作日志 (`fetch_log` 表) —— 记录每次抓取/拉取的时间戳。

数据不是实时的,这些时间戳让用户看到「上次刷新是什么时候」。
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from .db import connect
from .models import FetchLog


def record(op: str, details: str | None = None) -> int:
    """追加一行 fetch_log 标记 `op` 刚完成。返回新行 id。"""
    f = FetchLog.create(
        op=op,
        ts=datetime.now().isoformat(timespec="seconds"),
        details=details,
    )
    return int(f.id)


def last_run(op: str) -> tuple[str | None, str | None]:
    """该 op 的最近一次运行 `(ts, details)`,无则 `(None, None)`。

    兼容老接口形态;新代码可用 `last_run_log(op)` 拿 `FetchLog`。
    """
    log = last_run_log(op)
    return (log.ts, log.details) if log else (None, None)


def last_run_log(op: str) -> FetchLog | None:
    """该 op 的最近一次运行 -> `FetchLog` 或 None。"""
    return (FetchLog
            .select()
            .where(FetchLog.op == op)
            .order_by(FetchLog.ts.desc())
            .first())


def latest_per_op() -> pd.DataFrame:
    """每个 op 一行,展示最近一次运行 —— 供侧边栏「最近更新」展示。"""
    return pd.read_sql(
        """
        SELECT op, MAX(ts) AS last_run, COUNT(*) AS times_run
        FROM fetch_log
        GROUP BY op
        ORDER BY last_run DESC
        """,
        connect(),
    )


def latest_bar_date() -> str | None:
    """`prices_daily` 中跨全部标的的最近交易日(ISO yyyy-mm-dd)。"""
    row = connect().execute("SELECT MAX(date) AS d FROM prices_daily").fetchone()
    return row["d"] if row and row["d"] else None
