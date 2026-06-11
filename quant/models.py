"""数据表 schema —— Peewee ORM 实现。

为什么用 Peewee:
    上一版我们手写了 `Model` 基类 + 自动 DDL + 继承 CRUD。Peewee 给我们同样
    形态的 active-record(`Trade.create(...)`、`Trade.get_or_none(Trade.id==i)`、
    `Trade.delete().where(...).execute()`),但额外送来:
      - 真正的查询表达式(`Trade.qty > 10`、`Trade.ticker << ['NOK','NVDA']`)
      - `playhouse.migrate` 做 schema 迁移
      - 10+ 年线上验证 + 大社区
    SQLite 是 Peewee 的一等公民,无依赖之外的额外负担。

设计要点
========
1. **单个 `SqliteDatabase` 实例** 在 `quant.db` 模块,所有模型通过 `BaseModel`
   共享。
2. **字段顺序 = 声明顺序 = SQL 列顺序**,与历史 schema 完全一致。
3. SQL 侧 `DEFAULT` 用 `constraints=[SQL("DEFAULT 'US'")]`,这样 Peewee
   `create_tables()` 生成的 DDL 与历史手写的一字不差(新部署/重建库时仍精确匹配)。
4. **复合主键** 用 `Meta.primary_key = CompositeKey('ticker','date')`。
5. **CHECK / UNIQUE** 用 `constraints=[Check(...)]` / `unique=True`。
6. **索引** 用 `Meta.indexes = ((('ticker','ts'), False),)` —— 第二位 `False` 表示
   非唯一(避免和 UNIQUE 索引混淆)。
"""

from __future__ import annotations

from peewee import (
    SQL, AutoField, Check, CharField, CompositeKey, FloatField,
    IntegerField, Model, TextField,
)

from .db import DB


class BaseModel(Model):
    """所有表的 Peewee 基类,绑定到全局 `DB`。"""

    class Meta:
        database = DB


# ---------------------------------------------------------------------------
# 行情
# ---------------------------------------------------------------------------
class PriceDaily(BaseModel):
    """`prices_daily` —— 仅原始 OHLCV + 市场标签。指标在加载时即时计算,不落库。"""

    ticker = CharField(null=False)
    date = CharField(null=False)               # ISO YYYY-MM-DD
    open = FloatField(null=True)
    high = FloatField(null=True)
    low = FloatField(null=True)
    close = FloatField(null=True)
    volume = IntegerField(null=True)
    market = CharField(null=False, default="US",
                       constraints=[SQL("DEFAULT 'US'")])  # 'US' | 'CN'

    class Meta:
        database = DB
        table_name = "prices_daily"
        primary_key = CompositeKey("ticker", "date")


class PriceIntraday(BaseModel):
    """`prices_intraday` —— 事件窗口的小时/分钟线,按需拉取。"""

    ticker = CharField(null=False)
    ts = CharField(null=False)                 # ISO timestamp
    interval = CharField(null=False)           # '5m','15m','1h',...
    open = FloatField(null=True)
    high = FloatField(null=True)
    low = FloatField(null=True)
    close = FloatField(null=True)
    volume = IntegerField(null=True)

    class Meta:
        database = DB
        table_name = "prices_intraday"
        primary_key = CompositeKey("ticker", "ts", "interval")


# ---------------------------------------------------------------------------
# 交易 / 事件
# ---------------------------------------------------------------------------
class Trade(BaseModel):
    """`trades` —— 用户记录的买卖。`total_cost` 派生,不落库。"""

    id = AutoField()                           # INTEGER PRIMARY KEY AUTOINCREMENT
    ts = CharField(null=False)
    ticker = CharField(null=False)
    side = CharField(null=False,
                     constraints=[Check("side IN ('BUY','SELL')")])
    qty = FloatField(null=False)
    price = FloatField(null=False)
    fees = FloatField(null=False, default=0.0,
                      constraints=[SQL("DEFAULT 0")])
    notes = CharField(null=True)
    tags = CharField(null=True)                # 逗号分隔

    class Meta:
        database = DB
        table_name = "trades"
        indexes = ((("ticker", "ts"), False),)  # idx_trades_ticker_ts


class Event(BaseModel):
    """`events` —— 财报 / 分红 / 新闻 / 异动 / 手动笔记。`dedupe_key` 防重复抓取。"""

    id = AutoField()
    ts = CharField(null=False)
    ticker = CharField(null=False)
    kind = CharField(null=False, index=True)   # manual|earnings|dividend|news|anomaly
    title = CharField(null=True)
    body = TextField(null=True)                # body 可能较长,用 TextField
    source_url = CharField(null=True)
    metadata = TextField(null=True)            # JSON blob
    dedupe_key = CharField(null=True, unique=True)

    class Meta:
        database = DB
        table_name = "events"
        indexes = ((("ticker", "ts"), False),)  # idx_events_ticker_ts


# ---------------------------------------------------------------------------
# 关注列表 / 用户 / 公式 / 操作日志 / 回测
# ---------------------------------------------------------------------------
class Ticker(BaseModel):
    """`tickers` —— 自选股关注列表。移除标的=置 active=0,不删行,历史保留。"""

    ticker = CharField(primary_key=True)
    added_at = CharField(null=False)
    active = IntegerField(null=False, default=1,
                          constraints=[SQL("DEFAULT 1")])
    notes = CharField(null=True)
    market = CharField(null=False, default="US",
                       constraints=[SQL("DEFAULT 'US'")])

    class Meta:
        database = DB
        table_name = "tickers"


class User(BaseModel):
    """`users` —— 应用登录账号(PBKDF2-HMAC-SHA256)。"""

    id = AutoField()
    username = CharField(null=False, unique=True)
    pw_hash = CharField(null=False)
    pw_salt = CharField(null=False)
    created_at = CharField(null=False)

    class Meta:
        database = DB
        table_name = "users"


class Formula(BaseModel):
    """`formulas` —— 选股器保存的 MyTT 公式。"""

    id = AutoField()
    name = CharField(null=False, unique=True)
    expr = TextField(null=False)
    description = TextField(null=True)
    created_at = CharField(null=False)

    class Meta:
        database = DB
        table_name = "formulas"


class FetchLog(BaseModel):
    """`fetch_log` —— 每次自动拉取的时间戳。"""

    id = AutoField()
    op = CharField(null=False)                 # 'fetch_daily' | 'pull_earnings' | ...
    ts = CharField(null=False)                 # ISO timestamp
    details = CharField(null=True)

    class Meta:
        database = DB
        table_name = "fetch_log"
        indexes = ((("op", "ts"), False),)


class Backtest(BaseModel):
    """`backtests` —— 一次策略回测的完整可重跑配置 + 关键指标。"""

    id = AutoField()
    created_at = CharField(null=False)
    ticker = CharField(null=False)
    strategy = CharField(null=False)
    params_json = TextField(null=False)
    start_date = CharField(null=True)
    end_date = CharField(null=True)
    init_cash = FloatField(null=False)
    fees = FloatField(null=False)
    total_return = FloatField(null=True)
    annualized_return = FloatField(null=True)
    sharpe = FloatField(null=True)
    max_drawdown = FloatField(null=True)
    win_rate = FloatField(null=True)
    num_trades = IntegerField(null=True)
    avg_trade_pct = FloatField(null=True)
    bh_total_return = FloatField(null=True)
    bh_max_drawdown = FloatField(null=True)
    notes = CharField(null=True)

    class Meta:
        database = DB
        table_name = "backtests"
        indexes = ((("ticker", "created_at"), False),)


# ---------------------------------------------------------------------------
# 注册表(供 db.create_tables 用)
# ---------------------------------------------------------------------------
ALL_MODELS: list[type[BaseModel]] = [
    PriceDaily, PriceIntraday,
    Trade, Event,
    Ticker, User, Formula, FetchLog,
    Backtest,
]


__all__ = [
    "BaseModel", "ALL_MODELS",
    "PriceDaily", "PriceIntraday",
    "Trade", "Event",
    "Ticker", "User", "Formula", "FetchLog",
    "Backtest",
]
