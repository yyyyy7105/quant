"""交易记录页面:新增 / 编辑 / 删除交易。"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from quant.trades import add_trade, delete_trade, list_trades, update_trade


def render() -> None:
    st.title("交易记录")

    # ---- 新增交易 --------------------------------------------------------
    with st.expander("➕ 新增交易", expanded=False):
        with st.form("add_trade_form", clear_on_submit=True):
            c1, c2, c3, c4 = st.columns(4)
            t_ticker = c1.text_input("标的").upper()
            t_side = c2.selectbox("方向", ["BUY", "SELL"])
            t_qty = c3.number_input("数量", min_value=0.0, value=0.0, step=1.0)
            t_price = c4.number_input("价格", min_value=0.0, value=0.0,
                                      step=0.01, format="%.4f")
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
                        ts=t_ts, fees=t_fees, notes=t_notes or None,
                        tags=t_tags or None,
                    )
                    st.success(f"已添加 {t_side} {t_qty} {t_ticker} @ {t_price}")
                    st.rerun()

    # ---- 列表 / 编辑 / 删除 ----------------------------------------------
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
            "total_cost": st.column_config.NumberColumn("总成本", disabled=True,
                                                        format="%.2f"),
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
