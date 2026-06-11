"""量化交易与事件日志的 Streamlit 看板。

启动:
    uv run streamlit run app.py
"""

from __future__ import annotations

import importlib
import sys as _sys

import streamlit as st

# ---------------------------------------------------------------------------
# 热重载:Streamlit 在脚本重跑间会缓存已导入的模块。若运行中修改了 quant.* 子模块,
# 内存里的旧副本会失效(出现 AttributeError)。每次重跑强制 reload。
# 顺序很重要:被依赖的模块先 reload。`models` 顶层 `from .db import DB`,
# 所以 `db` 必须先于 `models` 重新执行。
# ---------------------------------------------------------------------------
import quant.db, quant.models, quant.mytt, quant.metrics, quant.oplog
import quant.sources.us, quant.sources.cn
import quant.prices, quant.tickers, quant.trades, quant.events
import quant.auth, quant.strategies, quant.backtest, quant.formula

for _m in (
    quant.db, quant.models, quant.mytt, quant.metrics, quant.oplog,
    quant.sources.us, quant.sources.cn,
    quant.prices, quant.tickers, quant.trades, quant.events,
    quant.auth, quant.strategies, quant.backtest, quant.formula,
):
    try:
        importlib.reload(_m)
    except Exception as _e:  # noqa: BLE001
        print(f"[reload-warn] {_m.__name__}: {type(_e).__name__}: {_e}",
              file=_sys.stderr)

# 组件也需要 reload(开发期改 components/*.py 时同理)
import components.shared, components.sidebar
import components.portfolio, components.ticker_view, components.backtest_page
import components.screener, components.trade_log
import components.events_feed, components.tickers_page

for _m in (
    components.shared, components.sidebar,
    components.portfolio, components.ticker_view, components.backtest_page,
    components.screener, components.trade_log,
    components.events_feed, components.tickers_page,
):
    try:
        importlib.reload(_m)
    except Exception as _e:  # noqa: BLE001
        print(f"[reload-warn] {_m.__name__}: {type(_e).__name__}: {_e}",
              file=_sys.stderr)

from components import (
    backtest_page, events_feed, portfolio, screener,
    sidebar, ticker_view, tickers_page, trade_log,
)

# ---------------------------------------------------------------------------
# 页面配置 + 登录门禁
# ---------------------------------------------------------------------------
st.set_page_config(page_title="量化日志", layout="wide")

sidebar.render_login()       # 未认证时 st.stop(),不往下走

# ---------------------------------------------------------------------------
# 侧边栏(市场切换、导航、数据拉取、最近更新)
# ---------------------------------------------------------------------------
page = sidebar.render()

# ---------------------------------------------------------------------------
# 页面路由
# ---------------------------------------------------------------------------
if page == "投资组合":
    portfolio.render()
elif page == "个股看板":
    ticker_view.render()
elif page == "回测":
    backtest_page.render()
elif page == "选股器":
    screener.render()
elif page == "交易记录":
    trade_log.render()
elif page == "事件流":
    events_feed.render()
elif page == "自选股":
    tickers_page.render()
