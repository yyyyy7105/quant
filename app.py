"""量化交易与事件日志的 Streamlit 看板。

启动:
    uv run streamlit run app.py
"""

from __future__ import annotations

import importlib
from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

# Streamlit 在脚本重跑间会缓存已导入的模块。若运行中修改了 quant.* 子模块,
# 内存里的旧副本会失效(出现 AttributeError)。每次重跑强制 reload。
import quant.db, quant.mytt, quant.metrics, quant.oplog
import quant.sources.us, quant.sources.cn
import quant.prices, quant.tickers, quant.trades, quant.events
import quant.auth, quant.strategies, quant.backtest, quant.formula
for _m in (
    quant.db, quant.mytt, quant.metrics, quant.oplog,
    quant.sources.us, quant.sources.cn,
    quant.prices, quant.tickers, quant.trades, quant.events,
    quant.auth, quant.strategies, quant.backtest, quant.formula,
):
    importlib.reload(_m)

from quant import auth
from quant import oplog
from quant import tickers as tk
from quant.backtest import (
    delete_backtest, get_backtest, list_backtests, run_backtest,
)
from quant.events import (
    VALID_KINDS, add_event, delete_event, list_events,
    pull_all as pull_all_events, update_event,
)
from quant.formula import (
    delete_formula, get_formula, list_formulas, save_formula, scan,
)
from quant.metrics import boll as boll_fn, kdj as kdj_fn, macd as macd_fn
from quant.prices import fetch_daily, list_tickers, load_daily
from quant.strategies import STRATEGIES
from quant.trades import (
    add_trade, delete_trade, list_trades, positions, update_trade,
)

st.set_page_config(page_title="量化日志", layout="wide")

# ---------------------------------------------------------------------------
# 登录门禁 —— 若尚未注册任何用户则跳过(首次使用体验)
# ---------------------------------------------------------------------------
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

PAGES = ["投资组合", "个股看板", "回测", "选股器", "交易记录", "事件流", "自选股"]

MARKETS = {"美股 (US)": "US", "A股 (CN)": "CN"}

EVENT_STYLE = {
    "earnings": ("★", "#9b59b6"),
    "dividend": ("$", "#27ae60"),
    "news":     ("📰", "#3498db"),
    "anomaly":  ("⚠️", "#e74c3c"),
    "manual":   ("●", "#f39c12"),
}

# 概览/事件等界面中文标签
KIND_LABELS = {
    "earnings": "财报", "dividend": "分红", "news": "新闻",
    "anomaly": "异动", "manual": "手动",
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _humanize_age(ts_str: str | None) -> str:
    """'2026-06-05T21:39:30' -> '刚刚' / '5 分钟前' / '2 小时前' / '3 天前'。"""
    if not ts_str:
        return "从未"
    try:
        ts = datetime.fromisoformat(ts_str)
    except ValueError:
        return ts_str
    secs = (datetime.now() - ts).total_seconds()
    if secs < 60:
        return "刚刚"
    if secs < 3600:
        return f"{int(secs // 60)} 分钟前"
    if secs < 86400:
        return f"{int(secs // 3600)} 小时前"
    return f"{int(secs // 86400)} 天前"


# ---------------------------------------------------------------------------
# 侧边栏 —— 市场切换、导航、操作、最近更新
# ---------------------------------------------------------------------------
if st.session_state.get("authenticated"):
    _user = st.session_state.get("username", "")
    st.sidebar.caption(f"已登录:**{_user}**")
    if st.sidebar.button("退出登录", type="secondary"):
        st.session_state["authenticated"] = False
        st.session_state.pop("username", None)
        st.rerun()

market_label = st.sidebar.radio("市场", list(MARKETS.keys()), horizontal=True)
market = MARKETS[market_label]
st.session_state["market"] = market

st.sidebar.markdown("---")
page = st.sidebar.radio("页面", PAGES)

st.sidebar.markdown("---")
st.sidebar.subheader("刷新数据")

if st.sidebar.button("拉取最新行情", width='stretch'):
    with st.spinner("正在拉取行情..."):
        tickers = tk.get_active(market) or []
        if not tickers:
            st.sidebar.warning("该市场暂无自选股。请到「自选股」页面添加。")
        else:
            fetch_daily(tickers, market=market)
            st.sidebar.success(f"已拉取 {len(tickers)} 只标的")
            st.rerun()

if market == "CN":
    st.sidebar.button("拉取全部事件", width='stretch', disabled=True)
    st.sidebar.caption("A股暂不支持自动拉取财报 / 分红 / 新闻")
else:
    if st.sidebar.button("拉取全部事件", width='stretch'):
        with st.spinner("正在拉取财报 / 分红 / 新闻 / 异动..."):
            tickers = tk.get_active(market) or []
            if not tickers:
                st.sidebar.warning("该市场暂无自选股。")
            else:
                res = pull_all_events(tickers)
                total = sum(res.values())
                st.sidebar.success(f"新增 {total} 条事件 ({res})")
                st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("最近更新")
st.sidebar.caption(f"当前时间:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

_latest_bar = oplog.latest_bar_date()
_fetch_ts, _ = oplog.last_run("fetch_daily")
if _latest_bar:
    _fetch_age = f"(更新于:{_humanize_age(_fetch_ts)})" if _fetch_ts else ""
    st.sidebar.caption(f"**最新行情日期:** {_latest_bar} {_fetch_age}")

_log = oplog.latest_per_op()
if _log.empty:
    st.sidebar.caption("(尚无操作记录)")
else:
    label_map = {
        "pull_all":         "全部事件",
        "pull_earnings":    "财报",
        "pull_dividends":   "分红",
        "pull_news":        "新闻",
        "detect_anomalies": "异动",
    }
    for _, row in _log.iterrows():
        if row["op"] in ("fetch_daily", "fetch_intraday"):
            continue  # fetch_daily 已在「最新行情日期」展示;intraday 已移除
        label = label_map.get(row["op"], row["op"])
        st.sidebar.caption(f"**{label}**:{_humanize_age(row['last_run'])}")


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
def render_portfolio() -> None:
    st.title("投资组合")
    pos = positions()
    if pos.empty:
        st.info("暂无持仓。请到「交易记录」页面记录交易。")
        return

    total_basis = pos["cost_basis"].sum()
    total_mv = pos["market_value"].fillna(0).sum()
    total_pl = pos["unrealized_pl"].fillna(0).sum()
    c1, c2, c3 = st.columns(3)
    c1.metric("成本", f"{total_basis:,.2f}")
    c2.metric("市值", f"{total_mv:,.2f}")
    c3.metric(
        "浮动盈亏",
        f"{total_pl:,.2f}",
        delta=f"{(total_pl / total_basis * 100):.2f}%" if total_basis else None,
    )

    st.dataframe(pos, width='stretch', hide_index=True)

    if total_mv > 0:
        fig = px.pie(
            pos[pos["market_value"] > 0],
            values="market_value", names="ticker",
            title="按市值的持仓占比",
        )
        st.plotly_chart(fig, width='stretch')


def _build_ticker_figure(df: pd.DataFrame, ticker: str, overlay: list[str],
                         panels: list[str], trades: pd.DataFrame,
                         events: pd.DataFrame, start, end) -> go.Figure:
    """K线 + 成交量 + 可选指标副图(MACD/RSI/KDJ),BOLL 作为主图叠加。"""
    n_panel = len(panels)
    rows = 2 + n_panel
    titles = [ticker, "成交量"] + [p for p in panels]

    weights = [3.0, 1.0] + [1.4] * n_panel
    total_w = sum(weights)
    row_heights = [w / total_w for w in weights]

    fig = make_subplots(
        rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.03,
        row_heights=row_heights, subplot_titles=titles,
    )

    # --- 第1行:K线 + 均线 + BOLL + 交易/事件标记 ---
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name=ticker, showlegend=False,
        increasing_line_color="#c0392b", decreasing_line_color="#27ae60",  # A股习惯:红涨绿跌
    ), row=1, col=1)

    for col in overlay:
        if col == "BOLL":
            b = boll_fn(df["close"])
            for key, label, dash in (("upper", "BOLL上轨", "dot"),
                                      ("mid", "BOLL中轨", "dash"),
                                      ("lower", "BOLL下轨", "dot")):
                fig.add_trace(go.Scatter(
                    x=df.index, y=b[key], mode="lines", name=label,
                    line=dict(width=1, dash=dash, color="#8e44ad"),
                ), row=1, col=1)
            continue
        c = col.lower()
        if c in df.columns:
            fig.add_trace(go.Scatter(x=df.index, y=df[c], mode="lines", name=col),
                          row=1, col=1)

    if not trades.empty:
        for side, label, marker_symbol, color in (
            ("BUY", "买入", "triangle-up", "#27ae60"),
            ("SELL", "卖出", "triangle-down", "#c0392b"),
        ):
            sub = trades[trades["side"] == side]
            if sub.empty:
                continue
            fig.add_trace(go.Scatter(
                x=sub["ts"], y=sub["price"], mode="markers",
                marker=dict(symbol=marker_symbol, size=14, color=color,
                            line=dict(width=1, color="white")),
                name=label,
                hovertext=[
                    f"{label} {r.qty} @ {r.price}<br>手续费={r.fees}<br>备注={r.notes or ''}<br>标签={r.tags or ''}"
                    for r in sub.itertuples()
                ],
                hoverinfo="text+x",
            ), row=1, col=1)

    if not events.empty:
        close_by_date = df["close"]
        for kind, (_glyph, color) in EVENT_STYLE.items():
            sub = events[events["kind"] == kind]
            if sub.empty:
                continue
            ys = []
            for ts in sub["ts"]:
                key = ts.normalize()
                if key in close_by_date.index:
                    ys.append(close_by_date.loc[key])
                else:
                    prior = close_by_date.loc[:key]
                    ys.append(prior.iloc[-1] if not prior.empty else None)
            fig.add_trace(go.Scatter(
                x=sub["ts"], y=ys, mode="markers",
                marker=dict(symbol="diamond", size=10, color=color,
                            line=dict(width=1, color="white")),
                name=KIND_LABELS.get(kind, kind),
                hovertext=[f"<b>{KIND_LABELS.get(r.kind, r.kind)}</b>: {r.title}<br>{r.body or ''}"
                           for r in sub.itertuples()],
                hoverinfo="text+x",
            ), row=1, col=1)

    # --- 第2行:成交量 ---
    vol_colors = ["#c0392b" if c >= o else "#27ae60"
                  for c, o in zip(df["close"], df["open"])]
    fig.add_trace(go.Bar(
        x=df.index, y=df["volume"], name="成交量",
        marker_color=vol_colors, showlegend=False,
    ), row=2, col=1)

    # --- 指标副图 ---
    r = 3
    for p in panels:
        if p == "MACD":
            m = macd_fn(df["close"])
            hist_colors = ["#c0392b" if v >= 0 else "#27ae60" for v in m["hist"]]
            fig.add_trace(go.Bar(x=df.index, y=m["hist"], name="MACD柱",
                                 marker_color=hist_colors, showlegend=False), row=r, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=m["dif"], mode="lines", name="DIF",
                                     line=dict(width=1, color="#2980b9")), row=r, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=m["dea"], mode="lines", name="DEA",
                                     line=dict(width=1, color="#e67e22")), row=r, col=1)
        elif p == "RSI":
            fig.add_trace(go.Scatter(x=df.index, y=df["rsi_14"], mode="lines", name="RSI14",
                                     line=dict(width=1, color="#8e44ad")), row=r, col=1)
            fig.add_hline(y=70, line_dash="dash", line_color="gray", row=r, col=1)
            fig.add_hline(y=30, line_dash="dash", line_color="gray", row=r, col=1)
        elif p == "KDJ":
            k = kdj_fn(df["high"], df["low"], df["close"])
            fig.add_trace(go.Scatter(x=df.index, y=k["k"], mode="lines", name="K",
                                     line=dict(width=1, color="#2980b9")), row=r, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=k["d"], mode="lines", name="D",
                                     line=dict(width=1, color="#e67e22")), row=r, col=1)
            fig.add_trace(go.Scatter(x=df.index, y=k["j"], mode="lines", name="J",
                                     line=dict(width=1, color="#16a085")), row=r, col=1)
        r += 1

    fig.update_layout(
        height=300 + 150 * rows,
        xaxis_rangeslider_visible=False,
        margin=dict(l=10, r=10, t=30, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        bargap=0,
    )
    return fig


def render_ticker_view() -> None:
    st.title("个股看板")
    market = st.session_state.get("market", "US")
    tickers = list_tickers(market)
    if not tickers:
        st.info("该市场暂无行情数据。请点击侧边栏「拉取最新行情」或到「自选股」添加。")
        return

    col_a, col_b, col_c = st.columns([2, 3, 3])
    ticker = col_a.selectbox("标的", tickers)
    overlay = col_b.multiselect(
        "主图叠加", ["SMA_20", "SMA_50", "SMA_200", "EMA_20", "BOLL"],
        default=["SMA_20", "SMA_50"],
    )
    panels = col_c.multiselect("指标副图", ["MACD", "RSI", "KDJ"], default=["MACD"])

    df = load_daily(ticker)
    if df.empty:
        st.warning("该标的暂无数据。")
        return

    min_d, max_d = df.index.min().date(), df.index.max().date()
    start, end = st.slider(
        "日期范围",
        min_value=min_d, max_value=max_d,
        value=(max(min_d, max_d - pd.Timedelta(days=365).to_pytimedelta()), max_d),
        format="YYYY-MM-DD",
    )
    df = df.loc[str(start):str(end)]

    trades = list_trades(ticker=ticker)
    if not trades.empty:
        trades = trades[(trades["ts"] >= pd.Timestamp(start))
                        & (trades["ts"] <= pd.Timestamp(end) + pd.Timedelta(days=1))]

    events = list_events(ticker=ticker)
    if not events.empty:
        events = events[(events["ts"] >= pd.Timestamp(start))
                        & (events["ts"] <= pd.Timestamp(end) + pd.Timedelta(days=1))]

    fig = _build_ticker_figure(df, ticker, overlay, panels, trades, events, start, end)
    st.plotly_chart(fig, width='stretch')

    st.subheader("窗口内事件")
    if events.empty:
        st.caption("该窗口内暂无事件。")
    else:
        ev_view = events[["ts", "kind", "title", "body", "source_url"]].copy()
        ev_view["kind"] = ev_view["kind"].map(lambda k: KIND_LABELS.get(k, k))
        st.dataframe(
            ev_view,
            column_config={
                "ts": st.column_config.DatetimeColumn("时间"),
                "kind": st.column_config.TextColumn("类型"),
                "title": st.column_config.TextColumn("标题"),
                "body": st.column_config.TextColumn("内容"),
                "source_url": st.column_config.LinkColumn("链接", display_text="打开"),
            },
            width='stretch', hide_index=True,
        )


def render_screener() -> None:
    st.title("选股器")
    st.caption("用 MyTT 公式筛选自选股。这是**筛选器,不是回测** —— 只判断条件当前是否成立。")
    market = st.session_state.get("market", "US")

    with st.expander("可用变量与函数", expanded=False):
        st.markdown(
            """
            **价格序列**:`CLOSE`/`C`、`OPEN`/`O`、`HIGH`/`H`、`LOW`/`L`、`VOL`/`V`

            **函数**:`MA, EMA, SMA, REF, CROSS, COUNT, HHV, LLV, SUM, STD,
            RSI, ABS, MAX, MIN, IF, EVERY, EXIST`

            **示例**:
            - 5日线上穿20日线且RSI未超买:`CROSS(MA(CLOSE,5), MA(CLOSE,20)) & (RSI(CLOSE,14) < 70)`
            - 创60日新高:`CLOSE >= HHV(CLOSE, 60)`
            - 放量:`VOL > MA(VOL, 20) * 2`
            """
        )

    saved = list_formulas()
    names = saved["name"].tolist() if not saved.empty else []

    pick = st.selectbox("已存公式", ["(新建)"] + names)
    if pick != "(新建)":
        row = get_formula(pick) or {}
        default_name, default_expr, default_desc = (
            row.get("name", ""), row.get("expr", ""), row.get("description") or "",
        )
    else:
        default_name, default_expr, default_desc = "", "", ""

    name = st.text_input("公式名称", value=default_name)
    expr = st.text_area("公式 (MyTT 语法)", value=default_expr, height=100)
    desc = st.text_input("说明 (可选)", value=default_desc)

    bsave, bdel, _ = st.columns([1, 1, 4])
    if bsave.button("保存公式", type="primary"):
        if not name or not expr.strip():
            st.error("名称和公式不能为空。")
        else:
            save_formula(name, expr, desc or None)
            st.success(f"已保存公式「{name}」")
            st.rerun()
    if bdel.button("删除公式", type="secondary") and pick != "(新建)":
        delete_formula(pick)
        st.success(f"已删除公式「{pick}」")
        st.rerun()

    st.markdown("---")
    st.subheader("运行筛选")
    c1, c2, c3 = st.columns([2, 1, 2])
    mode_label = c1.radio("命中范围", ["最新一根K线", "最近N根内"], horizontal=True)
    mode = "latest" if mode_label == "最新一根K线" else "recent"
    lookback = int(c2.number_input("N", min_value=1, value=5, step=1)) if mode == "recent" else 1
    scope_label = c3.radio("筛选范围", ["当前市场自选股", "当前市场全部数据"], horizontal=True)

    if st.button("开始筛选", type="primary"):
        if not expr.strip():
            st.error("请先输入公式。")
        else:
            if scope_label == "当前市场自选股":
                scan_tickers = tk.get_active(market)
            else:
                scan_tickers = list_tickers(market)
            if not scan_tickers:
                st.warning("该市场暂无可筛选的标的。")
            else:
                with st.spinner(f"正在筛选 {len(scan_tickers)} 只标的..."):
                    res = scan(expr, scan_tickers, mode=mode, lookback=lookback)
                hits = res[res["status"] == "命中"]
                errs = res[res["status"].str.startswith("错误")]
                st.success(f"命中 {len(hits)} / {len(scan_tickers)} 只标的")
                if not res.empty:
                    st.dataframe(
                        res.rename(columns={
                            "ticker": "标的", "last_close": "最新收盘",
                            "match_date": "命中日期", "status": "状态",
                        }),
                        width='stretch', hide_index=True,
                    )
                if not errs.empty:
                    st.caption(f"{len(errs)} 只标的公式出错(见上表「状态」列)。")


def render_trade_log() -> None:
    st.title("交易记录")

    # ---- 新增交易 ------------------------------------------------------
    with st.expander("➕ 新增交易", expanded=False):
        with st.form("add_trade_form", clear_on_submit=True):
            c1, c2, c3, c4 = st.columns(4)
            t_ticker = c1.text_input("标的").upper()
            t_side = c2.selectbox("方向", ["BUY", "SELL"])
            t_qty = c3.number_input("数量", min_value=0.0, value=0.0, step=1.0)
            t_price = c4.number_input("价格", min_value=0.0, value=0.0, step=0.01, format="%.4f")
            c5, c6 = st.columns(2)
            t_fees = c5.number_input("手续费", min_value=0.0, value=0.0, step=0.01)
            t_tags = c6.text_input("标签 (逗号分隔)")
            _now = datetime.now()
            c7, c8 = st.columns(2)
            t_date = c7.date_input("日期", value=_now.date())
            t_time = c8.time_input("时间", value=_now.time().replace(microsecond=0))
            t_notes = st.text_area("备注", "")
            if st.form_submit_button("添加交易", type="primary"):
                if not t_ticker or t_qty <= 0 or t_price <= 0:
                    st.error("标的、数量、价格为必填项。")
                else:
                    t_ts = datetime.combine(t_date, t_time)
                    add_trade(
                        ticker=t_ticker, side=t_side, qty=t_qty, price=t_price,
                        ts=t_ts, fees=t_fees, notes=t_notes or None, tags=t_tags or None,
                    )
                    st.success(f"已添加 {t_side} {t_qty} {t_ticker} @ {t_price}")
                    st.rerun()

    # ---- 列表 / 编辑 / 删除 -------------------------------------------
    df = list_trades()
    if df.empty:
        st.info("暂无交易。请用上方表单添加。")
        return

    c1, c2 = st.columns(2)
    ticker_f = c1.selectbox("按标的过滤", ["(全部)"] + sorted(df["ticker"].unique().tolist()))
    tag_q = c2.text_input("标签包含", "")

    if ticker_f != "(全部)":
        df = df[df["ticker"] == ticker_f]
    if tag_q:
        df = df[df["tags"].fillna("").str.contains(tag_q, case=False)]

    TRADE_EDITABLE = ["ts", "ticker", "side", "qty", "price", "fees", "notes", "tags"]

    # total_cost 始终由原始字段推导,绝不落库,避免过期
    df["total_cost"] = df.apply(
        lambda r: round(r.qty * r.price + r.fees, 2) if r.side == "BUY"
        else round(r.qty * r.price - r.fees, 2),
        axis=1,
    )

    edit_df = df.copy()
    edit_df.insert(0, "Delete?", False)

    edited = st.data_editor(
        edit_df,
        column_config={
            "Delete?": st.column_config.CheckboxColumn("删除?", width="small"),
            "id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
            "ts": st.column_config.DatetimeColumn("日期/时间"),
            "ticker": st.column_config.TextColumn("标的"),
            "side": st.column_config.SelectboxColumn("方向", options=["BUY", "SELL"]),
            "qty": st.column_config.NumberColumn("数量"),
            "price": st.column_config.NumberColumn("价格", format="%.4f"),
            "fees": st.column_config.NumberColumn("手续费"),
            "notes": st.column_config.TextColumn("备注"),
            "tags": st.column_config.TextColumn("标签"),
            "total_cost": st.column_config.NumberColumn("总成本", disabled=True, format="%.2f"),
        },
        width='stretch', hide_index=True, num_rows="fixed", key="trade_editor",
    )

    btn_del, btn_save, _ = st.columns([1, 1, 5])
    if btn_del.button("删除所选", type="secondary"):
        to_delete = edited[edited["Delete?"]]["id"].tolist()
        if not to_delete:
            st.warning("未勾选任何待删除行。")
        else:
            for rid in to_delete:
                delete_trade(int(rid))
            st.success(f"已删除 {len(to_delete)} 条交易。")
            st.rerun()
    if btn_save.button("保存修改", type="primary"):
        changes = 0
        for _, row in edited.iterrows():
            orig_rows = df[df["id"] == row["id"]]
            if orig_rows.empty:
                continue
            orig = orig_rows.iloc[0]
            dirty = {c: row[c] for c in TRADE_EDITABLE if str(row[c]) != str(orig[c])}
            if dirty:
                update_trade(int(row["id"]), **dirty)
                changes += 1
        if changes:
            st.success(f"已保存 {changes} 处修改。")
            st.rerun()
        else:
            st.info("未检测到修改。")
    st.caption(f"共 {len(df)} 条交易")


def render_events_feed() -> None:
    st.title("事件流")

    # ---- 新增手动事件 -------------------------------------------------
    with st.expander("➕ 新增手动事件", expanded=False):
        with st.form("add_event_form", clear_on_submit=True):
            c1, c2 = st.columns([1, 3])
            e_ticker = c1.text_input("标的").upper()
            e_kind = c2.selectbox(
                "类型", sorted(VALID_KINDS),
                index=sorted(VALID_KINDS).index("manual"),
                format_func=lambda k: KIND_LABELS.get(k, k),
            )
            e_title = st.text_input("标题")
            e_body = st.text_area("内容", "")
            e_url = st.text_input("来源链接", "")
            if st.form_submit_button("添加事件", type="primary"):
                if not e_ticker or not e_title:
                    st.error("标的和标题为必填项。")
                else:
                    rid = add_event(
                        ticker=e_ticker, kind=e_kind, title=e_title,
                        body=e_body or None, source_url=e_url or None,
                    )
                    if rid:
                        st.success(f"已添加事件 #{rid}")
                        st.rerun()
                    else:
                        st.warning("重复事件(dedupe_key 命中)")

    # ---- 列表 / 编辑 / 删除 -------------------------------------------
    df = list_events()
    if df.empty:
        st.info("暂无事件。请用侧边栏「拉取全部事件」或在上方手动添加。")
        return

    c1, c2 = st.columns(2)
    kinds = c1.multiselect("类型", sorted(VALID_KINDS), default=sorted(VALID_KINDS),
                           format_func=lambda k: KIND_LABELS.get(k, k))
    ticker_f = c2.selectbox("标的", ["(全部)"] + sorted(df["ticker"].unique().tolist()))

    if kinds:
        df = df[df["kind"].isin(kinds)]
    if ticker_f != "(全部)":
        df = df[df["ticker"] == ticker_f]

    EVENT_EDITABLE = ["ticker", "kind", "title", "body", "source_url"]
    display_cols = ["id", "ts", "ticker", "kind", "title", "body", "source_url"]
    edit_df = df[display_cols].copy()
    edit_df.insert(0, "Delete?", False)

    edited = st.data_editor(
        edit_df,
        column_config={
            "Delete?": st.column_config.CheckboxColumn("删除?", width="small"),
            "id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
            "ts": st.column_config.DatetimeColumn("日期/时间", disabled=True),
            "ticker": st.column_config.TextColumn("标的"),
            "kind": st.column_config.SelectboxColumn("类型", options=sorted(VALID_KINDS)),
            "title": st.column_config.TextColumn("标题"),
            "body": st.column_config.TextColumn("内容"),
            "source_url": st.column_config.LinkColumn("链接", display_text="打开"),
        },
        width='stretch', hide_index=True, num_rows="fixed", key="event_editor",
    )

    btn_del, btn_save, _ = st.columns([1, 1, 5])
    if btn_del.button("删除所选", type="secondary", key="ev_del"):
        to_delete = edited[edited["Delete?"]]["id"].tolist()
        if not to_delete:
            st.warning("未勾选任何待删除行。")
        else:
            for rid in to_delete:
                delete_event(int(rid))
            st.success(f"已删除 {len(to_delete)} 条事件。")
            st.rerun()
    if btn_save.button("保存修改", type="primary", key="ev_save"):
        changes = 0
        for _, row in edited.iterrows():
            orig_rows = df[df["id"] == row["id"]]
            if orig_rows.empty:
                continue
            orig = orig_rows.iloc[0]
            dirty = {c: row[c] for c in EVENT_EDITABLE if str(row[c]) != str(orig[c])}
            if dirty:
                update_event(int(row["id"]), **dirty)
                changes += 1
        if changes:
            st.success(f"已保存 {changes} 处修改。")
            st.rerun()
        else:
            st.info("未检测到修改。")
    st.caption(f"共 {len(df)} 条事件")


def render_tickers() -> None:
    st.title("自选股(关注列表)")
    market = st.session_state.get("market", "US")
    st.caption(
        f"当前市场:**{market}**。列表中的标的驱动「拉取最新行情」和「拉取全部事件」。"
        "移除标的会保留历史数据,但不再自动拉取。"
    )

    # ---- 新增标的 ------------------------------------------------------
    with st.form("add_ticker_form", clear_on_submit=True):
        c1, c2, c3 = st.columns([1, 1, 1])
        placeholder = "如 600519" if market == "CN" else "如 NVDA"
        new_ticker = c1.text_input("代码", placeholder=placeholder).upper()
        period = c2.selectbox("初始历史", ["1y", "2y", "5y", "max"], index=2)
        notes = c3.text_input("备注 (可选)")
        if st.form_submit_button(f"添加并拉取 ({market})", type="primary"):
            if not new_ticker:
                st.error("代码为必填项。")
            else:
                with st.spinner(f"正在拉取 {new_ticker} ({market})..."):
                    tk.add(new_ticker, notes=notes or None, market=market)
                    fetch_daily([new_ticker], period=period, market=market)
                st.success(f"已添加 {new_ticker} ({market})")
                st.rerun()

    st.subheader("当前关注列表")
    df = tk.summary()
    if df.empty:
        st.info("暂无标的。")
        return

    st.dataframe(df, width='stretch', hide_index=True)

    # ---- 移除 ----------------------------------------------------------
    active = df[df["active"] == "yes"]["ticker"].tolist()
    remove_ticker_msg = "(选择要移除的标的)"
    if active:
        c1, c2 = st.columns([1, 1])
        to_remove = c1.selectbox("移除标的", [remove_ticker_msg] + active,
                                 label_visibility="collapsed")
        if c2.button("移除", type="secondary") and to_remove != remove_ticker_msg:
            tk.remove(to_remove)
            st.success(f"已移除 {to_remove}(历史保留)")
            st.rerun()


def render_backtest() -> None:
    st.title("回测")
    st.caption("在历史数据上运行策略,与「买入并持有」对比,并保存结果。")
    market = st.session_state.get("market", "US")

    tickers = list_tickers(market)
    if not tickers:
        st.info("该市场暂无行情数据。请先到「自选股」页面添加。")
        return

    # ---- 配置表单 ------------------------------------------------------
    cfg_col, params_col = st.columns([1, 1])
    with cfg_col:
        st.subheader("配置")
        ticker = st.selectbox("标的", tickers, key="bt_ticker")
        strategy_key = st.selectbox(
            "策略",
            list(STRATEGIES.keys()),
            format_func=lambda k: STRATEGIES[k].name,
            key="bt_strategy",
        )
        spec = STRATEGIES[strategy_key]
        st.caption(spec.description)

        df_for_range = load_daily(ticker)
        if df_for_range.empty:
            st.warning("该标的暂无数据。")
            return
        min_d, max_d = df_for_range.index.min().date(), df_for_range.index.max().date()
        start, end = st.slider(
            "日期范围", min_value=min_d, max_value=max_d,
            value=(min_d, max_d), format="YYYY-MM-DD", key="bt_range",
        )
        cash_col, fees_col = st.columns(2)
        init_cash = cash_col.number_input("初始资金", min_value=100.0, value=10000.0,
                                          step=100.0, key="bt_cash")
        fees = fees_col.number_input("手续费率 (小数)", min_value=0.0, max_value=0.05,
                                     value=0.001, step=0.0005, format="%.4f", key="bt_fees")
        notes = st.text_input("备注 (可选)", key="bt_notes")

    with params_col:
        st.subheader("参数")
        params: dict = {}
        for pname, pspec in spec.params.items():
            params[pname] = st.slider(
                pname, min_value=float(pspec.min), max_value=float(pspec.max),
                value=float(pspec.default), step=float(pspec.step),
                key=f"bt_param_{strategy_key}_{pname}",
            )
            if isinstance(pspec.default, int) and isinstance(pspec.step, int):
                params[pname] = int(params[pname])

    run_btn = st.button("运行回测", type="primary")

    if run_btn:
        try:
            with st.spinner("正在回测..."):
                result = run_backtest(
                    ticker=ticker, strategy=strategy_key, params=params,
                    start=str(start), end=str(end),
                    init_cash=init_cash, fees=fees,
                    notes=notes or None,
                )
            st.session_state["bt_last_result"] = result
            st.success(f"回测 #{result.backtest_id} 已保存。")
        except Exception as e:
            st.error(f"回测失败:{e}")

    result = st.session_state.get("bt_last_result")
    if result is not None:
        _render_backtest_result(result)

    # ---- 已保存的回测 -------------------------------------------------
    st.markdown("---")
    with st.expander("已保存的回测", expanded=False):
        runs = list_backtests(ticker)
        if runs.empty:
            st.caption("该标的暂无已保存的回测。")
        else:
            show_cols = [
                "id", "created_at", "strategy", "params",
                "total_return", "sharpe", "max_drawdown",
                "win_rate", "num_trades", "bh_total_return", "notes",
            ]
            st.dataframe(runs[show_cols], width='stretch', hide_index=True)
            c1, c2 = st.columns([1, 1])
            view_id = c1.number_input("要查看的回测 ID", min_value=0, step=1, value=0,
                                      key="bt_view_id")
            cview, cdel = c2.columns(2)
            if cview.button("载入回测"):
                cfg = get_backtest(int(view_id))
                if cfg is None:
                    st.warning(f"未找到回测 #{view_id}。")
                else:
                    with st.spinner("正在按存档配置重跑..."):
                        result = run_backtest(
                            ticker=cfg["ticker"], strategy=cfg["strategy"],
                            params=cfg["params"], start=cfg["start"], end=cfg["end"],
                            init_cash=cfg["init_cash"], fees=cfg["fees"],
                            notes=cfg["notes"], persist=False,
                        )
                    st.session_state["bt_last_result"] = result
                    st.rerun()
            if cdel.button("删除回测", type="secondary"):
                if delete_backtest(int(view_id)):
                    st.success(f"已删除回测 #{view_id}")
                    st.rerun()
                else:
                    st.warning(f"未找到回测 #{view_id}。")


def _render_backtest_result(result) -> None:
    st.markdown("---")
    st.subheader(f"结果 — {result.ticker} / {STRATEGIES[result.strategy].name}")
    st.caption(f"参数:{result.params}")

    m = result.metrics
    cols = st.columns(5)
    cols[0].metric(
        "总收益", f"{m['total_return']*100:.2f}%",
        delta=f"{(m['total_return']-m['bh_total_return'])*100:.2f}% vs 买入持有",
    )
    cols[1].metric("夏普", f"{m['sharpe']:.2f}")
    cols[2].metric("最大回撤", f"{m['max_drawdown']*100:.2f}%")
    cols[3].metric("胜率", f"{m['win_rate']*100:.2f}%")
    cols[4].metric("交易次数", f"{int(m['num_trades'])}")

    # 净值曲线 vs 买入持有
    eq_fig = go.Figure()
    eq_fig.add_trace(go.Scatter(
        x=result.equity_curve.index, y=result.equity_curve.values,
        mode="lines", name="策略", line=dict(color="#3498db", width=2),
    ))
    eq_fig.add_trace(go.Scatter(
        x=result.bh_curve.index, y=result.bh_curve.values,
        mode="lines", name="买入并持有", line=dict(color="#95a5a6", width=2, dash="dash"),
    ))
    eq_fig.update_layout(
        title="净值曲线", height=380,
        margin=dict(l=10, r=10, t=40, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(eq_fig, width='stretch')

    # 回撤
    peak = result.equity_curve.cummax()
    dd = (result.equity_curve - peak) / peak
    dd_fig = go.Figure()
    dd_fig.add_trace(go.Scatter(
        x=dd.index, y=dd.values * 100, mode="lines",
        fill="tozeroy", line=dict(color="#e74c3c"), name="回撤 %",
    ))
    dd_fig.update_layout(
        title="回撤 (%)", height=260,
        margin=dict(l=10, r=10, t=40, b=10),
        yaxis=dict(ticksuffix="%"),
    )
    st.plotly_chart(dd_fig, width='stretch')

    # 价格图 + 买卖点
    df = load_daily(result.ticker,
                    start=str(result.equity_curve.index.min().date()),
                    end=str(result.equity_curve.index.max().date()))
    if not df.empty and not result.trades.empty:
        price_fig = go.Figure()
        price_fig.add_trace(go.Candlestick(
            x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
            name=result.ticker, showlegend=False,
            increasing_line_color="#c0392b", decreasing_line_color="#27ae60",
        ))
        t = result.trades
        if "entry_date" in t.columns:
            price_fig.add_trace(go.Scatter(
                x=t["entry_date"], y=t.get("entry_price"),
                mode="markers", name="买入",
                marker=dict(symbol="triangle-up", size=12, color="#27ae60",
                            line=dict(width=1, color="white")),
            ))
        if "exit_date" in t.columns:
            price_fig.add_trace(go.Scatter(
                x=t["exit_date"], y=t.get("exit_price"),
                mode="markers", name="卖出",
                marker=dict(symbol="triangle-down", size=12, color="#c0392b",
                            line=dict(width=1, color="white")),
            ))
        price_fig.update_layout(
            title="价格与买卖点", height=460,
            xaxis_rangeslider_visible=False,
            margin=dict(l=10, r=10, t=40, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )
        st.plotly_chart(price_fig, width='stretch')

    # 成交明细
    st.subheader("成交明细")
    if result.trades.empty:
        st.caption("无成交。")
    else:
        st.dataframe(result.trades, width='stretch', hide_index=True)


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
if page == "投资组合":
    render_portfolio()
elif page == "个股看板":
    render_ticker_view()
elif page == "回测":
    render_backtest()
elif page == "选股器":
    render_screener()
elif page == "交易记录":
    render_trade_log()
elif page == "事件流":
    render_events_feed()
elif page == "自选股":
    render_tickers()
