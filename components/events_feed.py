"""事件流页面:手动事件 + 列表 / 编辑 / 删除。"""

from __future__ import annotations

import streamlit as st

from quant.events import (
    VALID_KINDS, add_event, delete_event, list_events, update_event,
)

from .shared import KIND_LABELS, market_ticker_set


def render() -> None:
    st.title("事件流")
    market = st.session_state.get("market", "US")
    st.caption(f"当前市场:**{market}** —— 仅显示该市场下标的的事件。")

    # ---- 新增手动事件 ----------------------------------------------------
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

    # ---- 列表 / 编辑 / 删除 ----------------------------------------------
    df = list_events()
    if df.empty:
        st.info("暂无事件。请用侧边栏「拉取全部事件」或在上方手动添加。")
        return

    allowed = market_ticker_set(market)
    df = df[df["ticker"].isin(allowed)]
    if df.empty:
        st.info(f"当前市场({market})暂无事件。可在侧边栏切换市场查看其它。")
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
