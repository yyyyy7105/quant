"""Unified CLI for the quant project.

Day-to-day editing happens in the Streamlit app. The CLI keeps the scriptable
operations: register tickers, fetch data, log trades/events, and run pulls.

Examples:
    uv run python cli.py ticker add NVDA
    uv run python cli.py ticker remove NOK
    uv run python cli.py ticker list

    uv run python cli.py fetch                          # daily prices for watched tickers
    uv run python cli.py fetch-intraday --ticker NOK --start 2026-04-10 --end 2026-04-20

    uv run python cli.py trade add NOK BUY 100 15.41 --notes "test buy" --tags long-term
    uv run python cli.py trade list

    uv run python cli.py event add NOK "CEO resigned" --kind manual --body "..."
    uv run python cli.py event pull-all                 # earnings + dividends + news + anomalies
    uv run python cli.py event list --kind news

    uv run python cli.py positions

Edit or delete a trade/event in the app (Streamlit `data_editor`).
"""

from __future__ import annotations

import argparse
import getpass
import sys

from quant import auth
from quant import events as ev
from quant import prices as px
from quant import tickers as tk
from quant import trades as tr

_FALLBACK_TICKERS = ["NOK", "RKLB", "DRAM"]


def _resolve_tickers(arg: list[str] | None) -> list[str]:
    """Precedence: explicit --tickers > managed active list > built-in fallback."""
    if arg:
        return [t.upper() for t in arg]
    managed = tk.get_active()
    return managed if managed else _FALLBACK_TICKERS


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="quant", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    # ---- user management ------------------------------------------------
    u = sub.add_parser("user", help="Manage app users (register, list, delete)")
    usub = u.add_subparsers(dest="user_cmd", required=True)

    ua = usub.add_parser("add", help="Register a new user (password prompted interactively)")
    ua.add_argument("username")

    usub.add_parser("list", help="List all registered users")

    ud = usub.add_parser("delete", help="Delete a user")
    ud.add_argument("username")

    up = usub.add_parser("passwd", help="Reset a user's password")
    up.add_argument("username")

    # ---- ticker registry ------------------------------------------------
    t = sub.add_parser("ticker", help="Manage the watched ticker list")
    tsub = t.add_subparsers(dest="ticker_cmd", required=True)

    ta = tsub.add_parser("add", help="Add ticker(s) to the watch list and fetch history")
    ta.add_argument("tickers", nargs="+")
    ta.add_argument("--period", default="5y", help="History window for initial fetch")
    ta.add_argument("--notes", default=None)

    tr_ = tsub.add_parser("remove", help="Remove ticker(s) from the watch list (history kept)")
    tr_.add_argument("tickers", nargs="+")

    tsub.add_parser("list", help="Show all watched tickers with price stats")

    # ---- fetch (daily) --------------------------------------------------
    f = sub.add_parser("fetch", help="Refresh daily OHLCV + metrics for watched tickers")
    f.add_argument("--tickers", nargs="+", default=None,
                   help="Override tickers (default: managed list)")
    f.add_argument("--period", default="5y")

    # ---- fetch-intraday -------------------------------------------------
    fi = sub.add_parser("fetch-intraday", help="Pull intraday bars around an event window")
    fi.add_argument("--ticker", required=True)
    fi.add_argument("--start", required=True, help="YYYY-MM-DD")
    fi.add_argument("--end", required=True, help="YYYY-MM-DD")
    fi.add_argument("--interval", default="5m")

    # ---- trade ----------------------------------------------------------
    tr_cmd = sub.add_parser("trade", help="Trade log operations (add/list only — edit in app)")
    tr_sub = tr_cmd.add_subparsers(dest="trade_cmd", required=True)

    ta2 = tr_sub.add_parser("add", help="Record a buy/sell")
    ta2.add_argument("ticker")
    ta2.add_argument("side", choices=["BUY", "SELL", "buy", "sell"])
    ta2.add_argument("qty", type=float)
    ta2.add_argument("price", type=float)
    ta2.add_argument("--ts", default=None, help="ISO timestamp; default = now")
    ta2.add_argument("--fees", type=float, default=0.0)
    ta2.add_argument("--notes", default=None)
    ta2.add_argument("--tags", default=None, help="Comma-separated")

    tl = tr_sub.add_parser("list", help="Print the trade log")
    tl.add_argument("--ticker", default=None)
    tl.add_argument("--since", default=None)

    # ---- positions ------------------------------------------------------
    sub.add_parser("positions", help="Show current holdings + unrealized P&L")

    # ---- event ----------------------------------------------------------
    e = sub.add_parser("event", help="Event log operations (add/list/pull — edit in app)")
    esub = e.add_subparsers(dest="event_cmd", required=True)

    ea = esub.add_parser("add", help="Manually log an event")
    ea.add_argument("ticker")
    ea.add_argument("title")
    ea.add_argument("--kind", default="manual", choices=sorted(ev.VALID_KINDS))
    ea.add_argument("--body", default=None)
    ea.add_argument("--ts", default=None)
    ea.add_argument("--source-url", default=None)

    el = esub.add_parser("list", help="Print events")
    el.add_argument("--ticker", default=None)
    el.add_argument("--kind", default=None)
    el.add_argument("--since", default=None)

    pa = esub.add_parser("pull-all", help="Run earnings + dividends + news + anomaly detection")
    pa.add_argument("--tickers", nargs="+", default=None)
    pa.add_argument("--threshold", type=float, default=2.0, help="Anomaly z-score threshold")

    pe = esub.add_parser("pull-earnings", help="Auto-fetch earnings dates from yfinance")
    pe.add_argument("--tickers", nargs="+", default=None)

    pd_ = esub.add_parser("pull-dividends", help="Auto-fetch dividend payouts")
    pd_.add_argument("--tickers", nargs="+", default=None)

    pn = esub.add_parser("pull-news", help="Auto-fetch recent news headlines")
    pn.add_argument("--tickers", nargs="+", default=None)

    da = esub.add_parser("detect-anomalies", help="Flag large daily moves as events")
    da.add_argument("--tickers", nargs="+", default=None)
    da.add_argument("--threshold", type=float, default=2.0)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # ---- user -----------------------------------------------------------
    if args.cmd == "user":
        if args.user_cmd == "add":
            pw = getpass.getpass(f"Password for {args.username}: ")
            pw2 = getpass.getpass("Confirm password: ")
            if pw != pw2:
                print("[ERR]  Passwords do not match.", file=sys.stderr)
                return 1
            if not pw:
                print("[ERR]  Password cannot be empty.", file=sys.stderr)
                return 1
            ok = auth.register(args.username, pw)
            print(f"[OK]   User '{args.username}' registered" if ok
                  else f"[ERR]  Username '{args.username}' already taken")
            return 0 if ok else 1
        if args.user_cmd == "list":
            users = auth.list_users()
            if not users:
                print("(no users registered)")
            else:
                for u in users:
                    print(f"  {u['username']:20s}  registered {u['created_at']}")
            return 0
        if args.user_cmd == "delete":
            ok = auth.delete_user(args.username)
            print(f"[OK]   User '{args.username}' deleted" if ok
                  else f"[WARN] User '{args.username}' not found")
            return 0
        if args.user_cmd == "passwd":
            pw = getpass.getpass(f"New password for {args.username}: ")
            pw2 = getpass.getpass("Confirm password: ")
            if pw != pw2:
                print("[ERR]  Passwords do not match.", file=sys.stderr)
                return 1
            if not pw:
                print("[ERR]  Password cannot be empty.", file=sys.stderr)
                return 1
            ok = auth.change_password(args.username, pw)
            print(f"[OK]   Password updated" if ok
                  else f"[ERR]  User '{args.username}' not found")
            return 0 if ok else 1

    # ---- ticker ---------------------------------------------------------
    if args.cmd == "ticker":
        if args.ticker_cmd == "add":
            for ticker in args.tickers:
                t = ticker.upper()
                is_new = tk.add(t, notes=args.notes)
                action = "Added" if is_new else "Re-activated"
                print(f"[OK]   {action} {t} — fetching history...")
                px.fetch_daily([t], period=args.period)
            return 0
        if args.ticker_cmd == "remove":
            for ticker in args.tickers:
                t = ticker.upper()
                found = tk.remove(t)
                print(
                    f"[OK]   {t} removed from watch list (history kept)" if found
                    else f"[WARN] {t} not found in watch list"
                )
            return 0
        if args.ticker_cmd == "list":
            df = tk.summary()
            print(df.to_string(index=False) if not df.empty else "(no tickers registered)")
            return 0

    # ---- fetch ----------------------------------------------------------
    if args.cmd == "fetch":
        px.fetch_daily(_resolve_tickers(args.tickers), period=args.period)
        return 0
    if args.cmd == "fetch-intraday":
        px.fetch_intraday(args.ticker, args.start, args.end, args.interval)
        return 0

    # ---- trade ----------------------------------------------------------
    if args.cmd == "trade":
        if args.trade_cmd == "add":
            row_id = tr.add_trade(
                ticker=args.ticker, side=args.side, qty=args.qty, price=args.price,
                ts=args.ts, fees=args.fees, notes=args.notes, tags=args.tags,
            )
            print(f"[OK]   trade #{row_id} added")
            return 0
        if args.trade_cmd == "list":
            df = tr.list_trades(ticker=args.ticker, since=args.since)
            print(df.to_string(index=False) if not df.empty else "(no trades)")
            return 0

    # ---- positions ------------------------------------------------------
    if args.cmd == "positions":
        df = tr.positions()
        print(df.to_string(index=False) if not df.empty else "(no open positions)")
        return 0

    # ---- event ----------------------------------------------------------
    if args.cmd == "event":
        if args.event_cmd == "add":
            row_id = ev.add_event(
                ticker=args.ticker, kind=args.kind, title=args.title,
                body=args.body, ts=args.ts, source_url=args.source_url,
            )
            print(f"[OK]   event #{row_id} added" if row_id else "[SKIP] duplicate event")
            return 0
        if args.event_cmd == "list":
            df = ev.list_events(ticker=args.ticker, kind=args.kind, since=args.since)
            print(df.to_string(index=False) if not df.empty else "(no events)")
            return 0
        if args.event_cmd == "pull-all":
            res = ev.pull_all(_resolve_tickers(args.tickers), anomaly_threshold=args.threshold)
            print(f"[DONE] pull-all: {res}")
            return 0
        if args.event_cmd == "pull-earnings":
            ev.pull_earnings(_resolve_tickers(args.tickers))
            return 0
        if args.event_cmd == "pull-dividends":
            ev.pull_dividends(_resolve_tickers(args.tickers))
            return 0
        if args.event_cmd == "pull-news":
            ev.pull_news(_resolve_tickers(args.tickers))
            return 0
        if args.event_cmd == "detect-anomalies":
            ev.detect_anomalies(_resolve_tickers(args.tickers), threshold=args.threshold)
            return 0

    print(f"unknown command: {args}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
