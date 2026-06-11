"""Backtest engine: run a strategy via vectorbt, persist results to `backtests`."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import vectorbt as vbt

from .db import connect
from .models import Backtest
from .prices import load_daily
from .strategies import STRATEGIES


@dataclass
class BacktestResult:
    backtest_id: int
    ticker: str
    strategy: str
    params: dict
    metrics: dict          # serializable
    equity_curve: pd.Series
    bh_curve: pd.Series
    trades: pd.DataFrame
    portfolio: object      # vbt.Portfolio (not serialized)


def run_backtest(
    ticker: str,
    strategy: str,
    params: dict,
    start: str | None = None,
    end: str | None = None,
    init_cash: float = 10000,
    fees: float = 0.001,
    notes: str | None = None,
    persist: bool = True,
) -> BacktestResult:
    """Run a vectorbt backtest and (optionally) persist results."""
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown strategy {strategy!r}. Known: {list(STRATEGIES)}")

    df = load_daily(ticker, start=start, end=end)
    if df.empty:
        raise ValueError(f"No price data for {ticker}")

    spec = STRATEGIES[strategy]
    entries, exits = spec.fn(df, **params)
    close = df["close"]

    pf = vbt.Portfolio.from_signals(
        close, entries, exits,
        init_cash=init_cash, fees=fees, freq="1D",
    )

    metrics = _strategy_metrics(pf)

    # Buy-and-hold benchmark on the same window
    bh_entries = pd.Series(False, index=close.index)
    bh_entries.iloc[0] = True
    bh_exits = pd.Series(False, index=close.index)
    bh_pf = vbt.Portfolio.from_signals(
        close, bh_entries, bh_exits,
        init_cash=init_cash, fees=fees, freq="1D",
    )
    metrics["bh_total_return"] = _safe_float(bh_pf.total_return())
    metrics["bh_max_drawdown"] = _safe_float(abs(bh_pf.max_drawdown()))

    backtest_id = -1
    if persist:
        backtest_id = _persist(
            ticker=ticker, strategy=strategy, params=params,
            start=start, end=end, init_cash=init_cash, fees=fees,
            metrics=metrics, notes=notes,
        )

    return BacktestResult(
        backtest_id=backtest_id,
        ticker=ticker, strategy=strategy, params=params, metrics=metrics,
        equity_curve=pf.value(),
        bh_curve=bh_pf.value(),
        trades=_trades_view(pf),
        portfolio=pf,
    )


def _strategy_metrics(pf) -> dict:
    n_trades = int(pf.trades.count())
    return {
        "total_return":      _safe_float(pf.total_return()),
        "annualized_return": _safe_float(pf.annualized_return()),
        "sharpe":            _safe_float(pf.sharpe_ratio()),
        "max_drawdown":      _safe_float(abs(pf.max_drawdown())),
        "win_rate":          _safe_float(pf.trades.win_rate()) if n_trades > 0 else 0.0,
        "num_trades":        n_trades,
        "avg_trade_pct":     _safe_float(pf.trades.returns.mean()) if n_trades > 0 else 0.0,
    }


def _safe_float(v) -> float:
    """Convert to float, treating NaN/Inf as 0.0 for safe storage and display."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if (math.isnan(x) or math.isinf(x)) else x


def _trades_view(pf) -> pd.DataFrame:
    """Round-trip trades as a flat DataFrame suitable for display."""
    if pf.trades.count() == 0:
        return pd.DataFrame(columns=["entry_date", "exit_date", "return_pct", "pnl", "size", "entry_price", "exit_price"])
    t = pf.trades.records_readable.copy()
    # vectorbt column names vary slightly across versions; normalize lazily.
    rename = {
        "Entry Timestamp": "entry_date",
        "Exit Timestamp":  "exit_date",
        "Return":          "return_pct",
        "PnL":             "pnl",
        "Size":            "size",
        "Avg Entry Price": "entry_price",
        "Avg Exit Price":  "exit_price",
    }
    t = t.rename(columns={k: v for k, v in rename.items() if k in t.columns})
    keep = [c for c in ["entry_date", "exit_date", "return_pct", "pnl", "size", "entry_price", "exit_price"] if c in t.columns]
    return t[keep]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _persist(
    ticker: str, strategy: str, params: dict,
    start: str | None, end: str | None,
    init_cash: float, fees: float,
    metrics: dict, notes: str | None,
) -> int:
    """落库一次回测配置 + 指标。返回新行 id。"""
    bt = Backtest.create(
        created_at=datetime.now().isoformat(timespec="seconds"),
        ticker=ticker.upper(),
        strategy=strategy,
        params_json=json.dumps(params),
        start_date=start, end_date=end,
        init_cash=init_cash, fees=fees,
        total_return=metrics["total_return"],
        annualized_return=metrics["annualized_return"],
        sharpe=metrics["sharpe"],
        max_drawdown=metrics["max_drawdown"],
        win_rate=metrics["win_rate"],
        num_trades=metrics["num_trades"],
        avg_trade_pct=metrics["avg_trade_pct"],
        bh_total_return=metrics["bh_total_return"],
        bh_max_drawdown=metrics["bh_max_drawdown"],
        notes=notes,
    )
    return int(bt.id)


def list_backtests(ticker: str | None = None) -> pd.DataFrame:
    q = "SELECT * FROM backtests"
    params: list = []
    if ticker:
        q += " WHERE ticker = ?"
        params.append(ticker.upper())
    q += " ORDER BY created_at DESC"
    with connect() as conn:
        df = pd.read_sql(q, conn, params=params)
    if not df.empty:
        df["params"] = df["params_json"].apply(json.loads)
    return df


def get_backtest(backtest_id: int) -> dict | None:
    """返回一次回测的配置 dict(够 re-run),不存在则 None。

    内部用 `Backtest`(Peewee model)取值,外部仍保留 dict 形态:CLI/UI 直接
    消费 dict 比 model 更方便(尤其是嵌套的 `metrics`)。
    """
    bt = Backtest.get_or_none(Backtest.id == backtest_id)
    if bt is None:
        return None
    return {
        "id":         bt.id,
        "ticker":     bt.ticker,
        "strategy":   bt.strategy,
        "params":     json.loads(bt.params_json),
        "start":      bt.start_date,
        "end":        bt.end_date,
        "init_cash":  bt.init_cash,
        "fees":       bt.fees,
        "notes":      bt.notes,
        "metrics": {
            "total_return":      bt.total_return,
            "annualized_return": bt.annualized_return,
            "sharpe":            bt.sharpe,
            "max_drawdown":      bt.max_drawdown,
            "win_rate":          bt.win_rate,
            "num_trades":        bt.num_trades,
            "avg_trade_pct":     bt.avg_trade_pct,
            "bh_total_return":   bt.bh_total_return,
            "bh_max_drawdown":   bt.bh_max_drawdown,
        },
    }


def delete_backtest(backtest_id: int) -> bool:
    """按 id 删一条回测。返回是否删了一行。"""
    return Backtest.delete().where(Backtest.id == backtest_id).execute() > 0
