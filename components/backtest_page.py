"""回测页面:配置、运行、结果展示、历史回测管理。"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from quant.backtest import (
    delete_backtest, get_backtest, list_backtests, run_backtest,
)
from quant.strategies import STRATEGIES

from .shared import cached_load_daily, market_ticker_set


def render() -> None:
    st.title("回测")
    st.caption("在历史数据上运行策略,与「买入并持有」对比,并保存结果。")
    market = st.session_state.get("market", "US")

    tickers = sorted(market_ticker_set(market))
    if not tickers:
        st.info("该市场暂无标的。请先到「自选股」页面添加。")
        return

    # ---- 配置表单 --------------------------------------------------------
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

        df_for_range = cached_load_daily(ticker)
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
                                     value=0.001, step=0.0005, format="%.4f",
                                     key="bt_fees")
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
        _render_result(result)

    # ---- 已保存的回测 ----------------------------------------------------
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
            view_id = c1.number_input("要查看的回测 ID", min_value=0, step=1,
                                      value=0, key="bt_view_id")
            cview, cdel = c2.columns(2)
            if cview.button("载入回测"):
                cfg = get_backtest(int(view_id))
                if cfg is None:
                    st.warning(f"未找到回测 #{view_id}。")
                else:
                    with st.spinner("正在按存档配置重跑..."):
                        result = run_backtest(
                            ticker=cfg["ticker"], strategy=cfg["strategy"],
                            params=cfg["params"], start=cfg["start"],
                            end=cfg["end"],
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


# ---------------------------------------------------------------------------
# 回测结果渲染
# ---------------------------------------------------------------------------
def _render_result(result) -> None:
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
        mode="lines", name="买入并持有",
        line=dict(color="#95a5a6", width=2, dash="dash"),
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
    _full = cached_load_daily(result.ticker)
    df = _full.loc[
        str(result.equity_curve.index.min().date()):
        str(result.equity_curve.index.max().date())
    ] if not _full.empty else _full
    if not df.empty and not result.trades.empty:
        price_fig = go.Figure()
        price_fig.add_trace(go.Candlestick(
            x=df.index, open=df["open"], high=df["high"],
            low=df["low"], close=df["close"],
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
