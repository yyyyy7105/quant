"""UI 组件包 —— 每个模块对应一个页面,sidebar 为侧边栏。

app.py 只需:
    from components import sidebar, portfolio, ticker_view, ...
    sidebar.render_login()
    page = sidebar.render()
    if page == "投资组合": portfolio.render()
    ...
"""

from . import (  # noqa: F401 — re-export for convenient access
    backtest_page,
    events_feed,
    portfolio,
    screener,
    sidebar,
    ticker_view,
    tickers_page,
    trade_log,
)
