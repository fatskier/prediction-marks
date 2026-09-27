"""Score the paper market maker's fills against settled results.

Reads <paper>/*/HH.fills*.jsonl.gz from paper_mm.py, looks up each market's
result (cached in <paper>/../outcomes, shared with fill_outcomes.py), and
reports P&L in dollars and return on stake by cent bucket, with 95% intervals
bootstrapped over events. Fills in markets not yet settled are counted as
open exposure, not scored.

    python paper_report.py --paper data/paper
"""

from __future__ import annotations

import argparse
import os
from collections import defaultdict
from datetime import datetime

from fill_outcomes import Lookup, bootstrap_ci, cent_bucket
from tape import HOSTS, read_stream


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paper", default="data/paper")
    ap.add_argument("--cache", default=None)
    ap.add_argument("--host", choices=HOSTS, default="prod")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--boot", type=int, default=2000)
    a = ap.parse_args(argv)
    cache = a.cache or os.path.join(os.path.dirname(os.path.abspath(a.paper)), "outcomes")

    fills = list(read_stream(a.paper, "fills"))
    orders = list(read_stream(a.paper, "orders"))
    if not fills:
        print(f"No fills yet. {sum(1 for o in orders if o['op'] == 'place'):,} quotes placed so far.")
        return
    look = Lookup(cache, a.host, a.offline)
    look.fetch_markets(sorted({f["ticker"] for f in fills}))

    scored, open_c, open_stake = [], 0.0, 0.0
    for f in fills:
        m = look.markets.get(f["ticker"], {})
        if m.get("result") not in ("yes", "no"):
            open_c += f["count"]
            open_stake += f["count"] * f["price"]
            continue
        scored.append({**f, "win": 1.0 if m["result"] == f["side"] else 0.0, "bucket": cent_bucket(f["price"])})

    t0 = min(f["trade_ts"] for f in fills)
    t1 = max(f["trade_ts"] for f in fills)
    places = sum(1 for o in orders if o["op"] == "place")
    print("# Paper market maker\n")
    print(f"Fills {datetime.utcfromtimestamp(t0):%Y-%m-%d %H:%M}–{datetime.utcfromtimestamp(t1):%Y-%m-%d %H:%M} UTC. "
          f"{places:,} quotes placed, {len(fills):,} fills, {sum(f['count'] for f in fills):,.0f} contracts. "
          f"Open (not yet settled): {open_c:,.0f} contracts, ${open_stake:,.2f} at stake.\n")
    if not scored:
        print("Nothing settled yet.")
        return

    def row(label, xs):
        c = sum(x["count"] for x in xs)
        stake = sum(x["count"] * x["price"] for x in xs)
        fee = sum(x["fee"] for x in xs)
        won = sum(x["count"] * x["win"] for x in xs)
        pnl = won - stake - fee
        lo, hi = bootstrap_ci([(x["event"], x["count"], x["count"] * x["win"], x["count"] * x["price"], x["fee"])
                               for x in xs], a.boot)
        evs = len({x["event"] for x in xs})
        cleared = sum(x["count"] for x in xs if x.get("cleared")) / c
        return (f"| {label} | {c:,.0f} | {evs:,} | {won / c:.2%} | {(stake + fee) / c:.2%} | ${pnl:,.2f} | "
                f"{pnl / stake:+.0%} | [{lo:+.0%}, {hi:+.0%}] | {cleared:.0%} |")

    print("| bucket | contracts | events | win rate | breakeven | P&L | return on stake | 95% CI (events) | filled by a level clear |")
    print("|---|---:|---:|---:|---:|---:|---:|---|---:|")
    by = defaultdict(list)
    for x in scored:
        by[x["bucket"]].append(x)
    for b in sorted(by):
        print(row(f"({b-1}¢, {b}¢]", by[b]))
    print(row("all", scored))


if __name__ == "__main__":
    main()
