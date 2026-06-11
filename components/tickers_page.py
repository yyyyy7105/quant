"""自选股(关注列表)页面:添加 / 移除标的。"""

from __future__ import annotations

import streamlit as st

from quant import tickers as tk
from quant.prices import fetch_daily

from .shared import cached_load_daily


def render() -> None:
    st.title("自选股(关注列表)")
    market = st.session_state.get("market", "US")
    st.caption(
        f"当前市场:**{market}**。列表中的标的驱动「拉取最新行情」和「拉取全部事件」。"
        "移除标的会保留历史数据,但不再自动拉取。"
    )

    # ---- 新增标的 --------------------------------------------------------
    with st.form("add_ticker_form", clear_on_submit=True):
        c1, c2, c3 = st.columns([1, 1, 1])
        placeholder = "如 600519" if market == "CN" else "如 NVDA"
        new_ticker = c1.text_input("代码", placeholder=placeholder).upper()
        period = c2.selectbox("初始历史", ["1y", "2y", "5y", "10y"], index=2)
        notes = c3.text_input("备注 (可选)")
        if st.form_submit_button(f"添加并拉取 ({market})", type="primary"):
            if not new_ticker:
                st.error("代码为必填项。")
            else:
                _add_bar = st.progress(0, text=f"正在拉取 {new_ticker} ({market})...")

                def _add_progress(i: int, total: int, t: str):
                    _add_bar.progress(i / total, text=f"正在拉取 {t} ({market})…")

                tk.add(new_ticker, notes=notes or None, market=market)
                fetch_daily([new_ticker], period=period, market=market,
                            on_progress=_add_progress)
                _add_bar.empty()
                cached_load_daily.clear()
                st.success(f"已添加 {new_ticker} ({market})")
                st.rerun()

    st.subheader(f"当前关注列表({market})")
    df = tk.summary()
    if not df.empty:
        df = df[df["market"] == market]

    show_inactive = st.checkbox("显示已停用的标的", value=False)
    if not show_inactive and not df.empty:
        df = df[df["active"] == "yes"]
    if df.empty:
        st.info(f"当前市场({market})暂无活跃标的。可在上方添加,"
                "或勾选「显示已停用的标的」查看历史。")
        return

    # ---- 列表 / 勾选移除 ------------------------------------------------
    edit_df = df.copy()
    edit_df.insert(0, "Remove?", False)
    edited = st.data_editor(
        edit_df,
        column_config={
            "Remove?":    st.column_config.CheckboxColumn("移除?", width="small"),
            "ticker":     st.column_config.TextColumn("标的", disabled=True),
            "market":     st.column_config.TextColumn("市场", disabled=True),
            "active":     st.column_config.TextColumn("活跃", disabled=True),
            "added_at":   st.column_config.TextColumn("添加时间", disabled=True),
            "notes":      st.column_config.TextColumn("备注", disabled=True),
            "price_rows": st.column_config.NumberColumn("行数", disabled=True),
            "earliest":   st.column_config.TextColumn("最早", disabled=True),
            "latest":     st.column_config.TextColumn("最近", disabled=True),
            "last_close": st.column_config.NumberColumn("最新收盘", disabled=True,
                                                        format="%.2f"),
        },
        width='stretch', hide_index=True, num_rows="fixed", key="ticker_editor",
    )

    btn_del, _ = st.columns([1, 5])
    if btn_del.button("移除所选", type="secondary", key="ticker_del"):
        to_remove = edited[
            edited["Remove?"] & (edited["active"] == "yes")
        ]["ticker"].tolist()
        if not to_remove:
            st.warning("未勾选可移除的活跃标的(已停用的会自动跳过)。")
        else:
            for t in to_remove:
                tk.remove(str(t))
            st.success(f"已移除 {len(to_remove)} 只标的(历史保留,不再自动拉取)")
            st.rerun()
