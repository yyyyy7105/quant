"""选股器页面:MyTT 公式筛选 + 已保存公式管理。"""

from __future__ import annotations

import streamlit as st

from quant import tickers as tk
from quant.formula import delete_formula, get_formula, list_formulas, save_formula, scan
from quant.prices import list_tickers

from .shared import cached_load_daily


def render() -> None:
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
    if pick != "(新建)" and (f := get_formula(pick)) is not None:
        default_name, default_expr, default_desc = f.name, f.expr, f.description or ""
    else:
        default_name, default_expr, default_desc = "", "", ""

    name = st.text_input("公式名称", value=default_name)
    expr = st.text_area("公式 (MyTT 语法)", value=default_expr, height=100)
    desc = st.text_input("说明 (可选)", value=default_desc)

    bsave, _ = st.columns([1, 5])
    if bsave.button("保存公式", type="primary"):
        if not name or not expr.strip():
            st.error("名称和公式不能为空。")
        else:
            save_formula(name, expr, desc or None)
            st.success(f"已保存公式「{name}」")
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
                    res = scan(expr, scan_tickers, mode=mode, lookback=lookback,
                               loader=cached_load_daily)
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

    # ---- 已保存公式管理 --------------------------------------------------
    st.markdown("---")
    st.subheader("已保存公式")
    if saved.empty:
        st.caption("尚无已保存的公式。")
    else:
        edit_df = saved[["id", "name", "expr", "description", "created_at"]].copy()
        edit_df.insert(0, "Delete?", False)
        edited = st.data_editor(
            edit_df,
            column_config={
                "Delete?":     st.column_config.CheckboxColumn("删除?", width="small"),
                "id":          st.column_config.NumberColumn("ID", disabled=True, width="small"),
                "name":        st.column_config.TextColumn("名称", disabled=True),
                "expr":        st.column_config.TextColumn("公式", disabled=True),
                "description": st.column_config.TextColumn("说明", disabled=True),
                "created_at":  st.column_config.TextColumn("创建时间", disabled=True),
            },
            width='stretch', hide_index=True, num_rows="fixed", key="formula_editor",
        )
        btn_del, _ = st.columns([1, 5])
        if btn_del.button("删除所选", type="secondary", key="formula_del"):
            to_delete = edited[edited["Delete?"]]["name"].tolist()
            if not to_delete:
                st.warning("未勾选任何待删除公式。")
            else:
                for nm in to_delete:
                    delete_formula(str(nm))
                st.success(f"已删除 {len(to_delete)} 个公式")
                st.rerun()
