"""投资组合页面。"""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from quant.trades import positions


def render() -> None:
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
