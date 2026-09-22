"""Stage 1: replicate the Kalshi cheap-NO edge as a *tradeable* statistic.

Becker's published `ev_yes_vs_no` analysis pools both legs of every trade, so its
YES and NO curves satisfy EV_yes(P) = -EV_no(100-P) exactly -- one fact presented
as two. It is also gross of fees, volume-weighted, and pooled across the whole
2021-2025 sample. None of those are wrong for describing the market, but none of
them answer "would a bot have made money".

This harness recomputes the same underlying quantity with four changes that
decide whether there is anything to execute on:

  1. Legs are split by role (taker / maker) instead of pooled, because only one
     side of a trade is reachable by a given strategy.
  2. Kalshi's fee is subtracted (see fees.py). At 1c it costs ~6.9% of stake.
  3. Results are stratified by year and series, because the paper's own story --
     professional makers arriving -- implies the edge decays.
  4. Confidence intervals are bootstrapped over *events*, not trades. Markets at
     1c cluster hard (every strike on one election, every alt-line in one game),
     so trade counts massively overstate the independent sample size.

Usage:
    python replicate.py --data /path/to/prediction-market-analysis/data/kalshi
    python replicate.py --data ... --max-price 5 --by year
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

import fees

POSITIONS_SQL = """
WITH notional AS (
    -- Becker excludes markets under $100 of notional volume; mirror that so
    -- results stay comparable to the published figures.
    SELECT ticker, SUM(count * yes_price / 100.0) AS usd
    FROM read_parquet('{trades}/*.parquet')
    GROUP BY ticker
),
resolved AS (
    SELECT m.ticker, m.event_ticker, m.result,
           split_part(m.event_ticker, '-', 1) AS series
    FROM read_parquet('{markets}/*.parquet') m
    INNER JOIN notional n ON m.ticker = n.ticker
    WHERE m.status = 'finalized' AND m.result IN ('yes', 'no')
      AND n.usd >= {min_notional}
),
legs AS (
    -- The taker's own position.
    SELECT m.event_ticker, m.series, m.ticker,
           'taker' AS role,
           t.taker_side AS side,
           CASE WHEN t.taker_side = 'yes' THEN t.yes_price ELSE t.no_price END AS price,
           CASE WHEN t.taker_side = m.result THEN 1 ELSE 0 END AS won,
           t.count AS contracts,
           CAST(date_part('year', t.created_time) AS INTEGER) AS yr
    FROM read_parquet('{trades}/*.parquet') t
    INNER JOIN resolved m ON t.ticker = m.ticker

    UNION ALL

    -- The maker sits on the opposite side of the same print.
    SELECT m.event_ticker, m.series, m.ticker,
           'maker' AS role,
           CASE WHEN t.taker_side = 'yes' THEN 'no' ELSE 'yes' END AS side,
           CASE WHEN t.taker_side = 'yes' THEN t.no_price ELSE t.yes_price END AS price,
           CASE WHEN (CASE WHEN t.taker_side = 'yes' THEN 'no' ELSE 'yes' END) = m.result
                THEN 1 ELSE 0 END AS won,
           t.count AS contracts,
           CAST(date_part('year', t.created_time) AS INTEGER) AS yr
    FROM read_parquet('{trades}/*.parquet') t
    INNER JOIN resolved m ON t.ticker = m.ticker
)
SELECT event_ticker, series, yr, role, side, price,
       SUM(contracts)                                   AS contracts,
       SUM(contracts * won)                             AS won_contracts,
       COUNT(DISTINCT ticker)                           AS n_markets,
       COUNT(DISTINCT CASE WHEN won = 1 THEN ticker END) AS n_markets_won
FROM legs
WHERE price BETWEEN {min_price} AND {max_price}
GROUP BY 1, 2, 3, 4, 5, 6
"""


def load_event_level(
    data_dir: Path, min_price: int, max_price: int, min_notional: float = 100.0
) -> pd.DataFrame:
    """Aggregate every trade leg to (event, year, role, side, price) granularity."""
    markets, trades = data_dir / "markets", data_dir / "trades"
    for d in (markets, trades):
        if not any(d.glob("*.parquet")):
            raise SystemExit(f"no parquet files in {d}")
    sql = POSITIONS_SQL.format(
        markets=markets, trades=trades, min_price=min_price,
        max_price=max_price, min_notional=min_notional,
    )
    return duckdb.connect().execute(sql).df()


def _rates(frame: pd.DataFrame) -> tuple[float, float]:
    """Volume-weighted and market-weighted resolution rates for one bucket."""
    contracts = frame["contracts"].sum()
    markets = frame["n_markets"].sum()
    vw = frame["won_contracts"].sum() / contracts if contracts else np.nan
    mw = frame["n_markets_won"].sum() / markets if markets else np.nan
    return vw, mw


def bootstrap_ci(
    frame: pd.DataFrame, price: int, maker: bool, n_boot: int, rng: np.random.Generator
) -> tuple[float, float]:
    """Cluster bootstrap over events -> CI on net return, % of capital at risk.

    Resampling events rather than trades is the whole point: markets inside one
    event share an outcome shock, so treating trades as independent understates
    the interval by orders of magnitude.
    """
    events = frame["event_ticker"].to_numpy()
    uniq, codes = np.unique(events, return_inverse=True)
    if len(uniq) < 2:
        return np.nan, np.nan

    won = frame["won_contracts"].to_numpy(dtype=float)
    tot = frame["contracts"].to_numpy(dtype=float)
    won_by_ev = np.bincount(codes, weights=won, minlength=len(uniq))
    tot_by_ev = np.bincount(codes, weights=tot, minlength=len(uniq))

    picks = rng.integers(0, len(uniq), size=(n_boot, len(uniq)))
    rates = won_by_ev[picks].sum(axis=1) / np.maximum(tot_by_ev[picks].sum(axis=1), 1e-12)
    returns = np.array([fees.net_return_pct(price, r, maker) for r in rates])
    return float(np.percentile(returns, 2.5)), float(np.percentile(returns, 97.5))


def summarise(
    events: pd.DataFrame, group_by: list[str], n_boot: int, seed: int = 0
) -> pd.DataFrame:
    """Collapse event-level rows into one row per (price, role, side, *group_by)."""
    rng = np.random.default_rng(seed)
    keys = ["price", "role", "side", *group_by]
    rows = []
    for key, frame in events.groupby(keys, sort=True):
        key = key if isinstance(key, tuple) else (key,)
        record = dict(zip(keys, key))
        price, maker = int(record["price"]), record["role"] == "maker"
        vw, mw = _rates(frame)
        lo, hi = bootstrap_ci(frame, price, maker, n_boot, rng)
        record.update(
            contracts=int(frame["contracts"].sum()),
            n_markets=int(frame["n_markets"].sum()),
            n_events=int(frame["event_ticker"].nunique()),
            win_rate_vw=vw,
            win_rate_mw=mw,
            implied=price / 100.0,
            breakeven=fees.breakeven_win_rate(price / 100.0, maker),
            gross_return_pct=100.0 * (100.0 * vw - price) / price,
            net_return_pct=fees.net_return_pct(price, vw, maker),
            net_return_pct_mw=fees.net_return_pct(price, mw, maker),
            ci_lo=lo,
            ci_hi=hi,
            significant=bool(lo == lo and lo > 0),
        )
        rows.append(record)
    return pd.DataFrame(rows).sort_values(keys).reset_index(drop=True)


def format_table(df: pd.DataFrame, group_by: list[str]) -> str:
    """Render the summary the way a go/no-go decision needs to read it."""
    cols = ["price", "role", "side", *group_by, "n_events", "n_markets",
            "gross_return_pct", "net_return_pct", "net_return_pct_mw",
            "ci_lo", "ci_hi", "significant"]
    out = df[cols].copy()
    for c in ("gross_return_pct", "net_return_pct", "net_return_pct_mw", "ci_lo", "ci_hi"):
        out[c] = out[c].map(lambda v: "" if pd.isna(v) else f"{v:+.1f}%")
    return out.to_string(index=False)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", required=True, type=Path,
                   help="kalshi data dir containing markets/ and trades/")
    p.add_argument("--min-price", type=int, default=1)
    p.add_argument("--max-price", type=int, default=10,
                   help="cheap tail only by default; the strategy zone")
    p.add_argument("--by", nargs="*", default=[], choices=["year", "series"],
                   help="extra stratification (year is the decay test)")
    p.add_argument("--role", choices=["taker", "maker", "both"], default="both")
    p.add_argument("--side", choices=["yes", "no", "both"], default="both")
    p.add_argument("--min-events", type=int, default=30,
                   help="suppress buckets too thin to interpret")
    p.add_argument("--min-notional", type=float, default=100.0,
                   help="drop markets below this notional volume, as the paper does")
    p.add_argument("--n-boot", type=int, default=2000)
    p.add_argument("--csv", type=Path, help="also write the full summary here")
    args = p.parse_args(argv)

    events = load_event_level(args.data, args.min_price, args.max_price, args.min_notional)
    if args.role != "both":
        events = events[events.role == args.role]
    if args.side != "both":
        events = events[events.side == args.side]
    if events.empty:
        raise SystemExit("no rows after filtering")

    group_by = [{"year": "yr", "series": "series"}[b] for b in args.by]
    summary = summarise(events, group_by, args.n_boot)
    shown = summary[summary.n_events >= args.min_events]

    print(f"\nKalshi cheap-tail replication  ({args.min_price}-{args.max_price}c, "
          f"fees applied, CIs clustered by event, "
          f"markets >= ${args.min_notional:g} notional)\n")
    if shown.empty:
        print(f"every bucket had < {args.min_events} independent events -- "
              "nothing here is interpretable.")
    else:
        print(format_table(shown, group_by))
        live = shown[(shown.side == "no") & shown.significant]
        print(f"\n{len(live)} of {len(shown)} buckets clear fees with a 95% CI "
              "excluding zero on the NO side.")
        if live.empty:
            print("=> No tradeable edge survives. Stage 1 fails; do not build the bot.")

    if args.csv:
        summary.to_csv(args.csv, index=False)
        print(f"\nfull summary -> {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
