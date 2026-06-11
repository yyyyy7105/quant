"""SQLite 连接与初始化(Peewee 之上的薄壳)。

模式 = 一个进程内共享的 `DB = SqliteDatabase(...)`,所有模型挂在它上面。
对外仍暴露 `connect()` 返回**底层的 `sqlite3.Connection`**,这样:
  - `pd.read_sql(query, connect())` 直接可用(pandas + DBAPI 连接)
  - `quant.prices.fetch_daily` 的批量 `INSERT OR REPLACE` / `executemany` 也照旧
首次调用 `connect()` 时会执行:
  1. 用 `DB.create_tables(ALL_MODELS, safe=True)` 建表(IF NOT EXISTS,幂等)
  2. 执行 `_migrate()` 修补老库形态(老指标列、市场字段、旧短名索引等)
"""

from __future__ import annotations

import sqlite3

from peewee import SqliteDatabase

from . import DATA_DIR, DB_PATH

# 单进程共享数据库;`connect()` 会确保 connection 已打开
DB = SqliteDatabase(
    str(DB_PATH),
    pragmas={"foreign_keys": 1},
    autoconnect=True,                # Peewee 默认 True,显式说明
)

_schema_applied = False

# 加载时已落库过的「指标列」(旧 schema)。出现任一即触发 prices_daily 重建。
_LEGACY_INDICATOR_COLS = {
    "sma_20", "sma_50", "sma_200", "ema_20",
    "daily_return", "volatility_20", "rsi_14", "vol_sma_20",
}


def _migrate(conn: sqlite3.Connection) -> None:
    """对老 DB 的一次性命令式修补。每条迁移都需自检前置条件,不重复执行。"""
    # 1) trades: 删除老的 total_cost 派生列
    tcols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    if "total_cost" in tcols:
        conn.execute("ALTER TABLE trades DROP COLUMN total_cost")

    # 2) prices_daily: 指标改为加载时即时计算,不再落库。检测到旧指标列则重建。
    pcols = {row[1] for row in conn.execute("PRAGMA table_info(prices_daily)").fetchall()}
    if pcols & _LEGACY_INDICATOR_COLS:
        conn.executescript(
            """
            CREATE TABLE prices_daily_new (
                ticker  TEXT NOT NULL,
                date    TEXT NOT NULL,
                open    REAL,
                high    REAL,
                low     REAL,
                close   REAL,
                volume  INTEGER,
                market  TEXT NOT NULL DEFAULT 'US',
                PRIMARY KEY (ticker, date)
            );
            INSERT INTO prices_daily_new (ticker, date, open, high, low, close, volume, market)
                SELECT ticker, date, open, high, low, close, volume, 'US' FROM prices_daily;
            DROP TABLE prices_daily;
            ALTER TABLE prices_daily_new RENAME TO prices_daily;
            """
        )
    elif "market" not in pcols:
        conn.execute("ALTER TABLE prices_daily ADD COLUMN market TEXT NOT NULL DEFAULT 'US'")

    # 3) tickers: 补 market 列
    tkcols = {row[1] for row in conn.execute("PRAGMA table_info(tickers)").fetchall()}
    if "market" not in tkcols:
        conn.execute("ALTER TABLE tickers ADD COLUMN market TEXT NOT NULL DEFAULT 'US'")

    # 4) 老索引名清理 —— Peewee 用 `<model>_<cols>` 命名(如 `trade_ticker_ts`),
    #    把历史两版命名都丢掉,让 Peewee 一套命名来源。
    for old_name in (
        # 最早期的手写短名
        "idx_trades_tt", "idx_events_tt", "idx_events_kind",
        "idx_fetch_log_op", "idx_backtests_ticker",
        # 上一版自动生成的 idx_<table>_<cols>
        "idx_trades_ticker_ts", "idx_events_ticker_ts",
        "idx_fetch_log_op_ts", "idx_backtests_ticker_created_at",
    ):
        conn.execute(f"DROP INDEX IF EXISTS {old_name}")

    conn.commit()


def _ensure_schema() -> None:
    """惰性初始化:首次调用 `connect()` 时建表 + 跑迁移。后续调用零成本。"""
    global _schema_applied
    if _schema_applied:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    from . import models  # 惰性导入,避免循环
    if DB.is_closed():
        DB.connect()
    DB.create_tables(models.ALL_MODELS, safe=True)
    _migrate(DB.connection())
    _schema_applied = True


def connect() -> sqlite3.Connection:
    """返回底层 `sqlite3.Connection`(Peewee 共享的同一连接)。

    为兼容 `pd.read_sql(query, connect())` 和 `quant.prices` 的 `executemany`,
    我们暴露 DBAPI 连接而不是 Peewee 的高阶 API。Peewee 配的 `row_factory` 是
    `sqlite3.Row`,所以 `row["col"]` 也能用。
    """
    _ensure_schema()
    if DB.is_closed():
        DB.connect()
    conn = DB.connection()
    conn.row_factory = sqlite3.Row
    return conn
