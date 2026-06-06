"""Back-compat shim: fetches daily prices into SQLite (and optionally to CSV).

Prefer the unified CLI:
    uv run python cli.py fetch [--tickers ...] [--period 5y]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from quant.prices import fetch_daily, load_daily

DEFAULT_TICKERS = ["NOK", "RKLB", "DRAM"]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    p.add_argument("--period", default="5y")
    p.add_argument("--interval", default="1d", help="Currently only '1d' supported via this shim")
    p.add_argument("--outdir", default=None, help="If set, also export CSVs to this dir")
    args = p.parse_args()

    fetch_daily(args.tickers, period=args.period)

    if args.outdir:
        outdir = Path(args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        for ticker in args.tickers:
            df = load_daily(ticker)
            if df.empty:
                continue
            df.to_csv(outdir / f"{ticker}.csv")
            print(f"[OK]   {ticker}: exported to {outdir / f'{ticker}.csv'}")


if __name__ == "__main__":
    main()
