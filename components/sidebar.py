"""侧边栏:登录门禁、市场切换、页面导航、数据拉取、最近更新。"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from quant import auth, oplog, tickers as tk
from quant.events import pull_all as pull_all_events
from quant.prices import fetch_daily

from .shared import (
    MARKETS, PAGES, PERIOD_DAYS, PERIOD_LABELS, PERIOD_OPTIONS,
    cached_load_daily, humanize_age,
)


def render_login() -> None:
    """登录门禁 —— 若尚未注册任何用户则跳过(首次使用体验)。

    未通过认证时调用 st.stop() 阻断后续渲染。
    """
    if auth.has_users() and not st.session_state.get("authenticated"):
        st.title("登录")
        with st.form("login_form"):
            username = st.text_input("用户名")
            password = st.text_input("密码", type="password")
            if st.form_submit_button("登录", type="primary"):
                if auth.verify(username, password):
                    st.session_state["authenticated"] = True
                    st.session_state["username"] = username
                    st.rerun()
                else:
                    st.error("用户名或密码错误。")
        st.caption("通过 CLI 注册:`uv run python cli.py user add <用户名>`")
        st.stop()


def render() -> str:
    """渲染完整侧边栏,返回用户选中的页面名称。"""

    # ---- 用户信息 / 登出 -------------------------------------------------
    if st.session_state.get("authenticated"):
        _user = st.session_state.get("username", "")
        st.sidebar.caption(f"已登录:**{_user}**")
        if st.sidebar.button("退出登录", type="secondary"):
            st.session_state["authenticated"] = False
            st.session_state.pop("username", None)
            st.rerun()

    # ---- 市场切换 --------------------------------------------------------
    market_label = st.sidebar.radio("市场", list(MARKETS.keys()), horizontal=True)
    market = MARKETS[market_label]
    st.session_state["market"] = market

    # ---- 页面导航 --------------------------------------------------------
    st.sidebar.markdown("---")
    page = st.sidebar.radio("页面", PAGES)

    # ---- 数据拉取 --------------------------------------------------------
    st.sidebar.markdown("---")
    st.sidebar.subheader("刷新数据")

    _render_fetch_prices(market)
    _render_fetch_events(market)

    # ---- 最近更新 --------------------------------------------------------
    st.sidebar.markdown("---")
    _render_recent_updates()

    return page


# ---------------------------------------------------------------------------
# 内部:拉取行情
# ---------------------------------------------------------------------------
def _render_fetch_prices(market: str) -> None:
    price_period = st.sidebar.selectbox(
        "行情更新范围", PERIOD_OPTIONS, index=2,
        format_func=lambda p: PERIOD_LABELS.get(p, p),
        key="fetch_price_period",
        help=("数据源会返回这个时间窗口内的全部日线;由于 (ticker, date) 是主键,"
              "重叠区间会被 INSERT OR REPLACE 覆盖回最新值,不会重复写入。"
              "日常增量更新选 1天/1周即可,初次拉取或长期没更新可选 5年。"),
    )

    _price_slot = st.sidebar.empty()
    if _price_slot.button("拉取最新行情", width='stretch'):
        tickers = tk.get_active(market) or []
        if not tickers:
            st.sidebar.warning("该市场暂无自选股。请到「自选股」页面添加。")
        else:
            _price_slot.progress(0, text="正在拉取行情…")

            def _on_progress(i: int, total: int, ticker: str):
                _price_slot.progress(i / total, text=f"正在拉取行情… {ticker} ({i}/{total})")

            fetch_daily(tickers, period=price_period, market=market,
                        on_progress=_on_progress)
            cached_load_daily.clear()
            st.sidebar.success(
                f"已拉取 {len(tickers)} 只标的 · 窗口 {PERIOD_LABELS[price_period]} "
                "(自动清理 10 年前历史)"
            )
            st.rerun()


# ---------------------------------------------------------------------------
# 内部:拉取事件
# ---------------------------------------------------------------------------
def _render_fetch_events(market: str) -> None:
    event_period = st.sidebar.selectbox(
        "事件扫描范围", PERIOD_OPTIONS, index=4,
        format_func=lambda p: PERIOD_LABELS.get(p, p),
        key="fetch_event_period",
        help=("异动(anomaly)检测只扫描这个窗口内的 K 线;财报 / 分红 / 新闻 "
              "由 yfinance 决定返回什么(不受此选项影响,但 dedupe_key 保证幂等)。"),
    )

    _evt_slot = st.sidebar.empty()
    if market == "CN":
        _evt_slot.button("拉取全部事件", width='stretch', disabled=True)
        st.sidebar.caption("A股暂不支持自动拉取财报 / 分红 / 新闻")
    else:
        if _evt_slot.button("拉取全部事件", width='stretch'):
            tickers = tk.get_active(market) or []
            if not tickers:
                st.sidebar.warning("该市场暂无自选股。")
            else:
                _evt_slot.progress(0, text="正在拉取事件…")

                def _on_progress(step: int, total: int, label: str):
                    _evt_slot.progress(step / total,
                                       text=f"正在拉取事件… {label} ({step}/{total})")

                lookback = PERIOD_DAYS.get(event_period)
                res = pull_all_events(tickers, anomaly_lookback_days=lookback,
                                      on_progress=_on_progress)
                total = sum(res.values())
                st.sidebar.success(
                    f"新增 {total} 条事件 · 异动窗口 {PERIOD_LABELS[event_period]} ({res})"
                )
                st.rerun()


# ---------------------------------------------------------------------------
# 内部:最近更新
# ---------------------------------------------------------------------------
def _render_recent_updates() -> None:
    st.sidebar.subheader("最近更新")
    st.sidebar.caption(f"当前时间:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    latest_bar = oplog.latest_bar_date()
    fetch_ts, _ = oplog.last_run("fetch_daily")
    if latest_bar:
        age = f"(更新于:{humanize_age(fetch_ts)})" if fetch_ts else ""
        st.sidebar.caption(f"**最新行情日期:** {latest_bar} {age}")

    log = oplog.latest_per_op()
    if log.empty:
        st.sidebar.caption("(尚无操作记录)")
    else:
        label_map = {
            "pull_all":         "全部事件",
            "pull_earnings":    "财报",
            "pull_dividends":   "分红",
            "pull_news":        "新闻",
            "detect_anomalies": "异动",
        }
        for _, row in log.iterrows():
            if row["op"] not in label_map:
                continue
            label = label_map[row["op"]]
            st.sidebar.caption(f"**{label}**:{humanize_age(row['last_run'])}")
