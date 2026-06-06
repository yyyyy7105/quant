"""Streamlit dashboard for the quant trade & event journal.

Run with:
    uv run streamlit run app.py
"""

from __future__ import annotations

import importlib
from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Streamlit caches imported modules across script reruns. If a quant.* submodule
# is edited while the server is running, the in-memory copy goes stale (you'd see
# AttributeError for newly added functions). Force-reload them every script run.
import quant.db, quant.metrics, quant.oplog, quant.prices, quant.tickers, quant.trades, quant.events, quant.auth
for _m in (
    quant.db, quant.metrics, quant.oplog, quant.prices,
    quant.tickers, quant.trades, quant.events, quant.auth,
):
    importlib.reload(_m)

from quant import auth
from quant import oplog
from quant import tickers as tk
from quant.events import (
    VALID_KINDS, add_event, delete_event, list_events,
    pull_all as pull_all_events, update_event,
)
from quant.prices import fetch_daily, list_tickers, load_daily
from quant.trades import (
    add_trade, delete_trade, list_trades, positions, update_trade,
)

st.set_page_config(page_title="Quant Journal", layout="wide")

# ---------------------------------------------------------------------------
# Login gate — skip if no users registered yet (first-run experience)
# ---------------------------------------------------------------------------
if auth.has_users() and not st.session_state.get("authenticated"):
    st.title("Login")
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Login", type="primary"):
            if auth.verify(username, password):
                st.session_state["authenticated"] = True
                st.session_state["username"] = username
                st.rerun()
            else:
                st.error("Invalid username or password.")
    st.caption("Register via CLI: `uv run python cli.py user add <username>`")
    st.stop()

PAGES = ["Portfolio", "Ticker view", "Trade log", "Events feed", "Tickers"]

EVENT_STYLE = {
    "earnings": ("★", "#9b59b6"),
    "dividend": ("$", "#27ae60"),
    "news":     ("📰", "#3498db"),
    "anomaly":  ("⚠️", "#e74c3c"),
    "manual":   ("●", "#f39c12"),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _humanize_age(ts_str: str | None) -> str:
    """'2026-06-05T21:39:30' -> 'just now' / '5 min ago' / '2 h ago' / '3 d ago'."""
    if not ts_str:
        return "never"
    try:
        ts = datetime.fromisoformat(ts_str)
    except ValueError:
        return ts_str
    delta = datetime.now() - ts
    secs = delta.total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400:
        return f"{int(secs // 3600)} h ago"
    return f"{int(secs // 86400)} d ago"


# ---------------------------------------------------------------------------
# Sidebar — navigation, actions, last-updated
# ---------------------------------------------------------------------------
if st.session_state.get("authenticated"):
    _user = st.session_state.get("username", "")
    st.sidebar.caption(f"Logged in as **{_user}**")
    if st.sidebar.button("Logout", type="secondary"):
        st.session_state["authenticated"] = False
        st.session_state.pop("username", None)
        st.rerun()

page = st.sidebar.radio("View", PAGES)

st.sidebar.markdown("---")
st.sidebar.subheader("Refresh data")

if st.sidebar.button("Fetch latest prices", width='stretch'):
    with st.spinner("Fetching prices..."):
        tickers = tk.get_active() or []
        if not tickers:
            st.sidebar.warning("No watched tickers. Add one on the Tickers page.")
        else:
            fetch_daily(tickers)
            st.sidebar.success(f"Fetched {len(tickers)} tickers")
            st.rerun()

if st.sidebar.button("Pull all events", width='stretch'):
    with st.spinner("Pulling earnings / dividends / news / anomalies..."):
        tickers = tk.get_active() or []
        if not tickers:
            st.sidebar.warning("No watched tickers.")
        else:
            res = pull_all_events(tickers)
            total = sum(res.values())
            st.sidebar.success(f"+{total} new events ({res})")
            st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("Last updated")
st.sidebar.caption(f"Now: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

_latest_bar = oplog.latest_bar_date()
_fetch_ts, _ = oplog.last_run("fetch_daily")
if _latest_bar:
    _fetch_age = f" (updated: {_humanize_age(_fetch_ts)})" if _fetch_ts else ""
    st.sidebar.caption(f"**Latest price bar:** {_latest_bar}{_fetch_age}")

_log = oplog.latest_per_op()
if _log.empty:
    st.sidebar.caption("(no operations run yet)")
else:
    label_map = {
        "pull_all":         "All events",
        "pull_earnings":    "Earnings",
        "pull_dividends":   "Dividends",
        "pull_news":        "News",
        "detect_anomalies": "Anomalies",
    }
    for _, row in _log.iterrows():
        if row["op"] in ("fetch_daily", "fetch_intraday"):
            continue  # fetch_daily shown in "Latest price bar"; intraday removed
        label = label_map.get(row["op"], row["op"])
        st.sidebar.caption(f"**{label}**: {_humanize_age(row['last_run'])}")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
def render_portfolio() -> None:
    st.title("Portfolio")
    pos = positions()
    if pos.empty:
        st.info("No open positions. Use the Trade log page to log a trade.")
        return

    total_basis = pos["cost_basis"].sum()
    total_mv = pos["market_value"].fillna(0).sum()
    total_pl = pos["unrealized_pl"].fillna(0).sum()
    c1, c2, c3 = st.columns(3)
    c1.metric("Cost basis", f"${total_basis:,.2f}")
    c2.metric("Market value", f"${total_mv:,.2f}")
    c3.metric(
        "Unrealized P&L",
        f"${total_pl:,.2f}",
        delta=f"{(total_pl / total_basis * 100):.2f}%" if total_basis else None,
    )

    st.dataframe(pos, width='stretch', hide_index=True)

    if total_mv > 0:
        fig = px.pie(
            pos[pos["market_value"] > 0],
            values="market_value", names="ticker",
            title="Allocation by market value",
        )
        st.plotly_chart(fig, width='stretch')


def render_ticker_view() -> None:
    st.title("Ticker view")
    tickers = list_tickers()
    if not tickers:
        st.info("No price data. Click 'Fetch latest prices' in the sidebar.")
        return

    col_a, col_b = st.columns([2, 3])
    ticker = col_a.selectbox("Ticker", tickers)
    overlay = col_b.multiselect(
        "Overlays", ["SMA_20", "SMA_50", "SMA_200", "EMA_20"], default=["SMA_20", "SMA_50"]
    )

    df = load_daily(ticker)
    if df.empty:
        st.warning("No data for this ticker.")
        return

    min_d, max_d = df.index.min().date(), df.index.max().date()
    start, end = st.slider(
        "Date range",
        min_value=min_d, max_value=max_d,
        value=(max(min_d, max_d - pd.Timedelta(days=365).to_pytimedelta()), max_d),
        format="YYYY-MM-DD",
    )
    df = df.loc[str(start):str(end)]

    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name=ticker, showlegend=False,
    ))
    for col in overlay:
        c = col.lower()
        if c in df.columns:
            fig.add_trace(go.Scatter(x=df.index, y=df[c], mode="lines", name=col))

    trades = list_trades(ticker=ticker)
    if not trades.empty:
        trades = trades[(trades["ts"] >= pd.Timestamp(start)) & (trades["ts"] <= pd.Timestamp(end) + pd.Timedelta(days=1))]
        for side, marker_symbol, color in (
            ("BUY", "triangle-up", "#27ae60"),
            ("SELL", "triangle-down", "#c0392b"),
        ):
            sub = trades[trades["side"] == side]
            if sub.empty:
                continue
            fig.add_trace(go.Scatter(
                x=sub["ts"], y=sub["price"], mode="markers",
                marker=dict(symbol=marker_symbol, size=14, color=color, line=dict(width=1, color="white")),
                name=f"{side}",
                hovertext=[
                    f"{r.side} {r.qty} @ {r.price}<br>fees={r.fees}<br>notes={r.notes or ''}<br>tags={r.tags or ''}"
                    for r in sub.itertuples()
                ],
                hoverinfo="text+x",
            ))

    events = list_events(ticker=ticker)
    if not events.empty:
        events = events[(events["ts"] >= pd.Timestamp(start)) & (events["ts"] <= pd.Timestamp(end) + pd.Timedelta(days=1))]
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
                marker=dict(symbol="diamond", size=10, color=color, line=dict(width=1, color="white")),
                name=kind,
                hovertext=[f"<b>{r.kind}</b>: {r.title}<br>{r.body or ''}" for r in sub.itertuples()],
                hoverinfo="text+x",
            ))

    fig.update_layout(
        height=620, xaxis_rangeslider_visible=False,
        margin=dict(l=10, r=10, t=30, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig, width='stretch')

    st.subheader("Events in window")
    if events.empty:
        st.caption("No events in this window.")
    else:
        st.dataframe(
            events[["ts", "kind", "title", "body", "source_url"]],
            column_config={
                "source_url": st.column_config.LinkColumn("Link", display_text="Open"),
            },
            width='stretch', hide_index=True,
        )


def render_trade_log() -> None:
    st.title("Trade log")

    # ---- add-trade form ------------------------------------------------
    with st.expander("➕ Add trade", expanded=False):
        with st.form("add_trade_form", clear_on_submit=True):
            c1, c2, c3, c4 = st.columns(4)
            t_ticker = c1.text_input("Ticker").upper()
            t_side = c2.selectbox("Side", ["BUY", "SELL"])
            t_qty = c3.number_input("Quantity", min_value=0.0, value=0.0, step=1.0)
            t_price = c4.number_input("Price", min_value=0.0, value=0.0, step=0.01, format="%.4f")
            c5, c6 = st.columns(2)
            t_fees = c5.number_input("Fees", min_value=0.0, value=0.0, step=0.01)
            t_tags = c6.text_input("Tags (comma-separated)")
            t_notes = st.text_area("Notes", "")
            if st.form_submit_button("Add trade", type="primary"):
                if not t_ticker or t_qty <= 0 or t_price <= 0:
                    st.error("Ticker, quantity, and price are required.")
                else:
                    add_trade(
                        ticker=t_ticker, side=t_side, qty=t_qty, price=t_price,
                        fees=t_fees, notes=t_notes or None, tags=t_tags or None,
                    )
                    st.success(f"Added {t_side} {t_qty} {t_ticker} @ {t_price}")
                    st.rerun()

    # ---- list / edit / delete -----------------------------------------
    df = list_trades()
    if df.empty:
        st.info("No trades yet. Use the form above.")
        return

    c1, c2 = st.columns(2)
    ticker_f = c1.selectbox("Filter ticker", ["(all)"] + sorted(df["ticker"].unique().tolist()))
    tag_q = c2.text_input("Tag contains", "")

    if ticker_f != "(all)":
        df = df[df["ticker"] == ticker_f]
    if tag_q:
        df = df[df["tags"].fillna("").str.contains(tag_q, case=False)]

    TRADE_EDITABLE = ["ts", "ticker", "side", "qty", "price", "fees", "notes", "tags"]

    # Recompute total_cost from raw fields so it's always derived, never stale
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
            "Delete?": st.column_config.CheckboxColumn("Del?", width="small"),
            "id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
            "ts": st.column_config.DatetimeColumn("Date/time"),
            "total_cost": st.column_config.NumberColumn("Total cost", disabled=True, format="%.2f"),
            "side": st.column_config.SelectboxColumn("Side", options=["BUY", "SELL"]),
        },
        width='stretch', hide_index=True, num_rows="fixed", key="trade_editor",
    )

    btn_del, btn_save, _ = st.columns([1, 1, 5])
    if btn_del.button("Delete selected", type="secondary"):
        to_delete = edited[edited["Delete?"]]["id"].tolist()
        if not to_delete:
            st.warning("No rows checked for deletion.")
        else:
            for rid in to_delete:
                delete_trade(int(rid))
            st.success(f"Deleted {len(to_delete)} trade(s).")
            st.rerun()
    if btn_save.button("Save edits", type="primary"):
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
            st.success(f"Saved {changes} change(s).")
            st.rerun()
        else:
            st.info("No changes detected.")
    st.caption(f"{len(df)} trades")


def render_events_feed() -> None:
    st.title("Events feed")

    # ---- add-event form ------------------------------------------------
    with st.expander("➕ Add manual event", expanded=False):
        with st.form("add_event_form", clear_on_submit=True):
            c1, c2 = st.columns([1, 3])
            e_ticker = c1.text_input("Ticker").upper()
            e_kind = c2.selectbox("Kind", sorted(VALID_KINDS), index=sorted(VALID_KINDS).index("manual"))
            e_title = st.text_input("Title")
            e_body = st.text_area("Body", "")
            e_url = st.text_input("Source URL", "")
            if st.form_submit_button("Add event", type="primary"):
                if not e_ticker or not e_title:
                    st.error("Ticker and title are required.")
                else:
                    rid = add_event(
                        ticker=e_ticker, kind=e_kind, title=e_title,
                        body=e_body or None, source_url=e_url or None,
                    )
                    if rid:
                        st.success(f"Added event #{rid}")
                        st.rerun()
                    else:
                        st.warning("Duplicate event (dedupe_key match)")

    # ---- list / edit / delete -----------------------------------------
    df = list_events()
    if df.empty:
        st.info("No events yet. Use the sidebar 'Pull all events' button or add manually above.")
        return

    c1, c2 = st.columns(2)
    kinds = c1.multiselect("Kinds", sorted(VALID_KINDS), default=sorted(VALID_KINDS))
    ticker_f = c2.selectbox("Ticker", ["(all)"] + sorted(df["ticker"].unique().tolist()))

    if kinds:
        df = df[df["kind"].isin(kinds)]
    if ticker_f != "(all)":
        df = df[df["ticker"] == ticker_f]

    EVENT_EDITABLE = ["ticker", "kind", "title", "body", "source_url"]
    display_cols = ["id", "ts", "ticker", "kind", "title", "body", "source_url"]
    edit_df = df[display_cols].copy()
    edit_df.insert(0, "Delete?", False)

    edited = st.data_editor(
        edit_df,
        column_config={
            "Delete?": st.column_config.CheckboxColumn("Del?", width="small"),
            "id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
            "ts": st.column_config.DatetimeColumn("Date/time", disabled=True),
            "kind": st.column_config.SelectboxColumn("Kind", options=sorted(VALID_KINDS)),
        },
        width='stretch', hide_index=True, num_rows="fixed", key="event_editor",
    )

    btn_del, btn_save, _ = st.columns([1, 1, 5])
    if btn_del.button("Delete selected", type="secondary", key="ev_del"):
        to_delete = edited[edited["Delete?"]]["id"].tolist()
        if not to_delete:
            st.warning("No rows checked for deletion.")
        else:
            for rid in to_delete:
                delete_event(int(rid))
            st.success(f"Deleted {len(to_delete)} event(s).")
            st.rerun()
    if btn_save.button("Save edits", type="primary", key="ev_save"):
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
            st.success(f"Saved {changes} change(s).")
            st.rerun()
        else:
            st.info("No changes detected.")
    st.caption(f"{len(df)} events")


def render_tickers() -> None:
    st.title("Tickers (watch list)")
    st.caption(
        "Tickers in this list drive 'Fetch latest prices' and 'Pull all events'. "
        "Removing a ticker keeps its history but excludes it from future auto-fetches."
    )

    # ---- add-ticker form ----------------------------------------------
    with st.form("add_ticker_form", clear_on_submit=True):
        c1, c2, c3 = st.columns([1, 1, 1])
        new_ticker = c1.text_input("Symbol", placeholder="e.g. NVDA").upper()
        period = c2.selectbox("Initial history", ["1y", "2y", "5y", "max"], index=2)
        notes = c3.text_input("Notes (optional)")
        if st.form_submit_button("Add & fetch", type="primary"):
            if not new_ticker:
                st.error("Symbol is required.")
            else:
                with st.spinner(f"Fetching {new_ticker}..."):
                    tk.add(new_ticker, notes=notes or None)
                    fetch_daily([new_ticker], period=period)
                st.success(f"Added {new_ticker}")
                st.rerun()

    st.subheader("Current watch list")
    df = tk.summary()
    if df.empty:
        st.info("No tickers yet.")
        return

    st.dataframe(df, width='stretch', hide_index=True)

    # ---- remove --------------------------------------------------------
    active = df[df["active"] == "yes"]["ticker"].tolist()
    remove_ticker_msg = "(Remove one ticker)"
    if active:
        c1, c2 = st.columns([1, 1])
        to_remove = c1.selectbox("Remove ticker", [remove_ticker_msg] + active, label_visibility="collapsed")
        if c2.button("Remove", type="secondary") and to_remove != remove_ticker_msg:
            tk.remove(to_remove)
            st.success(f"Removed {to_remove} (history kept)")
            st.rerun()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
if page == "Portfolio":
    render_portfolio()
elif page == "Ticker view":
    render_ticker_view()
elif page == "Trade log":
    render_trade_log()
elif page == "Events feed":
    render_events_feed()
elif page == "Tickers":
    render_tickers()
