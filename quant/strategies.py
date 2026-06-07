"""Strategy registry. Each strategy turns a price DataFrame into entry/exit signals.

A strategy is registered in `STRATEGIES` with metadata (display name, description,
parameter spec) plus its signal function. The Streamlit backtest page auto-generates
sliders from the parameter spec, and the CLI passes through `--<param>` flags.

Adding a new strategy = write a function + add an entry to STRATEGIES.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd


@dataclass
class ParamSpec:
    """UI hint for a strategy parameter (min/max/step/default)."""
    default: float | int
    min: float | int
    max: float | int
    step: float | int = 1


@dataclass
class Strategy:
    name: str
    description: str
    params: dict[str, ParamSpec]
    fn: Callable[..., tuple[pd.Series, pd.Series]]  # (entries, exits)


# ---------------------------------------------------------------------------
# Built-in strategies
# ---------------------------------------------------------------------------
def golden_cross(df: pd.DataFrame, short: int = 20, long: int = 50) -> tuple[pd.Series, pd.Series]:
    """Buy when short SMA crosses above long SMA; sell on reverse cross."""
    short, long = int(short), int(long)
    s = df["close"].rolling(short).mean()
    l = df["close"].rolling(long).mean()
    entries = (s > l) & (s.shift(1) <= l.shift(1))
    exits = (s < l) & (s.shift(1) >= l.shift(1))
    return entries.fillna(False), exits.fillna(False)


def rsi_mean_reversion(df: pd.DataFrame, lower: float = 30, upper: float = 70) -> tuple[pd.Series, pd.Series]:
    """Buy when RSI crosses below lower threshold; sell when it crosses above upper."""
    r = df["rsi_14"]
    entries = (r < lower) & (r.shift(1) >= lower)
    exits = (r > upper) & (r.shift(1) <= upper)
    return entries.fillna(False), exits.fillna(False)


def bollinger_breakout(df: pd.DataFrame, window: int = 20, n_std: float = 2.0) -> tuple[pd.Series, pd.Series]:
    """Buy when close breaks above upper band; sell when it breaks below lower band."""
    window = int(window)
    ma = df["close"].rolling(window).mean()
    sd = df["close"].rolling(window).std()
    upper = ma + n_std * sd
    lower = ma - n_std * sd
    entries = (df["close"] > upper) & (df["close"].shift(1) <= upper.shift(1))
    exits = (df["close"] < lower) & (df["close"].shift(1) >= lower.shift(1))
    return entries.fillna(False), exits.fillna(False)


STRATEGIES: dict[str, Strategy] = {
    "golden_cross": Strategy(
        name="Golden Cross (SMA)",
        description="Buy when short SMA crosses above long SMA; sell on reverse cross.",
        params={
            "short": ParamSpec(20, 5, 50, 1),
            "long":  ParamSpec(50, 20, 200, 5),
        },
        fn=golden_cross,
    ),
    "rsi_mean_reversion": Strategy(
        name="RSI Mean Reversion",
        description="Buy when RSI_14 crosses below `lower`; sell when it crosses above `upper`.",
        params={
            "lower": ParamSpec(30, 10, 50, 1),
            "upper": ParamSpec(70, 50, 90, 1),
        },
        fn=rsi_mean_reversion,
    ),
    "bollinger_breakout": Strategy(
        name="Bollinger Breakout",
        description="Buy on upper-band breakout; sell on lower-band breakdown.",
        params={
            "window": ParamSpec(20, 5, 60, 1),
            "n_std":  ParamSpec(2.0, 1.0, 4.0, 0.1),
        },
        fn=bollinger_breakout,
    ),
}
