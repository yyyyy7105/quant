"""个股看板页面:K线 + 成交量 + 指标副图 + 交易/事件标记。"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from quant.events import list_events
from quant.metrics import boll as boll_fn, kdj as kdj_fn, macd as macd_fn
from quant.trades import list_trades

from .shared import EVENT_STYLE, KIND_LABELS, cached_load_daily, market_ticker_set


def render() -> None:
    st.title("个股看板")
    market = st.session_state.get("market", "US")

    tickers = sorted(market_ticker_set(market))
    if not tickers:
        st.info("该市场暂无标的。请到「自选股」页面添加,或在侧边栏切换市场。")
        return

    col_a, col_b, col_c = st.columns([2, 3, 3])
    ticker = col_a.selectbox("标的", tickers)
    overlay = col_b.multiselect(
        "主图叠加", ["SMA_20", "SMA_50", "SMA_200", "EMA_20", "BOLL"],
        default=["SMA_20", "SMA_50"],
    )
    panels = col_c.multiselect("指标副图", ["MACD", "RSI", "KDJ"], default=["MACD"])

    full = cached_load_daily(ticker)
    if full.empty:
        st.warning(
            f"标的「{ticker}」暂无行情数据。请到「自选股」页面确认代码无误后,"
            "点击侧边栏「拉取最新行情」重试。"
            + (" (A股代码为6位数字,如 600519。)" if market == "CN"
               else " (美股指数请用 ^IXIC / ^GSPC 等带 ^ 前缀的符号。)")
        )
        return

    min_d, max_d = full.index.min().date(), full.index.max().date()
    start, end = st.slider(
        "日期范围",
        min_value=min_d, max_value=max_d,
        value=(max(min_d, max_d - pd.Timedelta(days=365).to_pytimedelta()), max_d),
        format="YYYY-MM-DD",
    )
    df = full.loc[str(start):str(end)]

    trades = list_trades(ticker=ticker)
    if not trades.empty:
        trades = trades[(trades["ts"] >= pd.Timestamp(start))
                        & (trades["ts"] <= pd.Timestamp(end) + pd.Timedelta(days=1))]

    events = list_events(ticker=ticker)
    if not events.empty:
        events = events[(events["ts"] >= pd.Timestamp(start))
                        & (events["ts"] <= pd.Timestamp(end) + pd.Timedelta(days=1))]

    fig = _build_figure(df, ticker, overlay, panels, trades, events)
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


# ---------------------------------------------------------------------------
# K线 + 成交量 + 指标副图
# ---------------------------------------------------------------------------
def _build_figure(
    df: pd.DataFrame, ticker: str, overlay: list[str],
    panels: list[str], trades: pd.DataFrame, events: pd.DataFrame,
) -> go.Figure:
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
        increasing_line_color="#c0392b", decreasing_line_color="#27ae60",
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

    # 交易标记
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
                    f"{label} {r.qty} @ {r.price}<br>手续费={r.fees}"
                    f"<br>备注={r.notes or ''}<br>标签={r.tags or ''}"
                    for r in sub.itertuples()
                ],
                hoverinfo="text+x",
            ), row=1, col=1)

    # 事件标记
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
                hovertext=[
                    f"<b>{KIND_LABELS.get(r.kind, r.kind)}</b>: {r.title}"
                    f"<br>{r.body or ''}"
                    for r in sub.itertuples()
                ],
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
