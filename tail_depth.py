"""Summarize cheap-side book depth on the recorded tail tape.

For each book snapshot of a tail ticker, the cheap side is whichever side's
best bid is <= TAIL. That bid is where a maker would rest a cheap-NO (or
cheap-YES) quote. Per snapshot we record the best cheap bid, the size resting
at it, the depth at or below 10c, the spread to the implied ask, and whether
the best bid sits on a sub-cent tick.

Fills come from the trade tape: a maker NO bid at p fills when a taker buys
YES at 1-p (taker_side == "yes", no_price == p), and symmetrically for YES.
Resting size at a level divided by the hourly maker-fill volume at that level
approximates how long a new quote joining the back of the queue waits.

    python tail_depth.py --tape data/tape
"""

from __future__ import annotations

import argparse
import statistics as st
from collections import defaultdict
from datetime import datetime

from tape import TAIL, read_stream, trade_ts


def f(x) -> float:
    return float(x)


def cheap_side(book: dict, tail: float = TAIL):
    """Return (side, levels, best_opposite_bid) for the cheap side, or None.

    levels are (price, size) floats, best (highest) bid last.
    """
    yes = [(f(p), f(q)) for p, q in book["yes"]]
    no = [(f(p), f(q)) for p, q in book["no"]]
    cands = []
    for side, mine, other in (("no", no, yes), ("yes", yes, no)):
        if mine and mine[-1][0] <= tail:
            cands.append((mine[-1][0], side, mine, other[-1][0] if other else None))
    if not cands:
        return None
    _, side, levels, opp = min(cands)
    return side, levels, opp


def snapshot_stats(book: dict, tail: float = TAIL):
    cs = cheap_side(book, tail)
    if cs is None:
        return None
    side, levels, opp = cs
    best, size = levels[-1]
    return {
        "side": side,
        "best": best,
        "size_at_best": size,
        "depth_tail": sum(q for p, q in levels if p <= tail),
        "notional_tail": sum(p * q for p, q in levels if p <= tail),
        "n_levels": sum(1 for p, _ in levels if p <= tail),
        # implied ask on the cheap side = 1 - best bid on the other side
        "spread": (1 - opp) - best if opp is not None else None,
        "subcent_best": abs(best * 100 - round(best * 100)) > 1e-6,
        "levels": levels,
    }


def cent_bucket(p: float) -> int:
    """Price bucket in whole cents, rounding sub-cent ticks up: 0.013 -> 2c.

    A 1.3c bid is ahead of every 1c bid and behind every 2c bid, so it sits in
    the (1c, 2c] bucket.
    """
    return max(1, int(-(-round(p * 10000) // 100)))


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    i = min(len(xs) - 1, max(0, round(q * (len(xs) - 1))))
    return xs[i]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tape", default="data/tape")
    ap.add_argument("--tail", type=float, default=TAIL)
    a = ap.parse_args(argv)

    tail_set: set[str] = set()
    for u in read_stream(a.tape, "universe"):
        tail_set.update(u["tail"])
    meta = {m["ticker"]: m for m in read_stream(a.tape, "markets")}

    per_ticker = defaultdict(list)
    t_min, t_max = float("inf"), 0.0
    n_books = n_empty = n_noncheap = 0
    for b in read_stream(a.tape, "books"):
        if b["ticker"] not in tail_set:
            continue
        n_books += 1
        t_min, t_max = min(t_min, b["_recv"]), max(t_max, b["_recv"])
        if not b["yes"] and not b["no"]:
            n_empty += 1
            continue
        s = snapshot_stats(b, a.tail)
        if s is None:
            n_noncheap += 1
            continue
        s["t"] = b["_recv"]
        per_ticker[b["ticker"]].append(s)

    hours = max((t_max - t_min) / 3600, 1e-9)

    # maker fills on the cheap side, per ticker and cent bucket, over the book window
    fills = defaultdict(float)  # (ticker, side, bucket) -> contracts
    for t in read_stream(a.tape, "trades"):
        # window on execution time: the startup backfill shares a single _recv
        if t["ticker"] not in per_ticker or not (t_min <= trade_ts(t) <= t_max):
            continue
        # taker buys YES -> a maker NO bid at no_price filled, and vice versa
        maker_side = "no" if t["taker_side"] == "yes" else "yes"
        p = f(t["no_price_dollars"] if maker_side == "no" else t["yes_price_dollars"])
        if p <= a.tail:
            fills[(t["ticker"], maker_side, cent_bucket(p))] += f(t["count_fp"])

    print(f"# Tail book depth, {datetime.utcfromtimestamp(t_min):%Y-%m-%d %H:%M}"
          f"–{datetime.utcfromtimestamp(t_max):%H:%M} UTC ({hours:.2f} h)\n")
    n_snap = sum(len(v) for v in per_ticker.values())
    print(f"{len(tail_set)} tickers ever in the tail universe; {len(per_ticker)} with a cheap-side book. "
          f"{n_books} tail snapshots: {n_snap} with a cheap side, {n_empty} empty, "
          f"{n_noncheap} no bid <= {a.tail:.0%} on either side.\n")

    # per-ticker medians, then distribution across tickers
    rows = []
    for tk, ss in per_ticker.items():
        m = meta.get(tk, {})
        close = m.get("close_time")
        life_h = None
        if close:
            life_h = (datetime.fromisoformat(close.replace("Z", "+00:00")).timestamp() - st.median(s["t"] for s in ss)) / 3600
        side = max(("no", "yes"), key=lambda sd: sum(1 for s in ss if s["side"] == sd))
        fb = cent_bucket(st.median(s["best"] for s in ss))
        fill_h = fills[(tk, side, fb)] / hours
        rows.append({
            "ticker": tk, "n": len(ss), "side": side,
            "best": st.median(s["best"] for s in ss),
            "size_at_best": st.median(s["size_at_best"] for s in ss),
            "depth_tail": st.median(s["depth_tail"] for s in ss),
            "notional_tail": st.median(s["notional_tail"] for s in ss),
            "n_levels": st.median(s["n_levels"] for s in ss),
            "spread": st.median([s["spread"] for s in ss if s["spread"] is not None] or [float("nan")]),
            "subcent": sum(s["subcent_best"] for s in ss) / len(ss),
            "fill_h": fill_h,
            "life_h": life_h,
            "series": tk.split("-")[0],
        })

    def dist(key, fmt="{:,.0f}", rs=rows):
        xs = [r[key] for r in rs if r[key] == r[key]]
        return " / ".join(fmt.format(pct(xs, q)) for q in (0.1, 0.5, 0.9))

    for label, rs in (
        ("All tail tickers", rows),
        ("Closing within 24 h", [r for r in rows if r["life_h"] is not None and r["life_h"] <= 24]),
        ("Closing after 24 h", [r for r in rows if r["life_h"] is not None and r["life_h"] > 24]),
    ):
        if not rs:
            continue
        print(f"## {label} ({len(rs)} tickers; p10 / median / p90 across tickers)\n")
        print("| metric | p10 / p50 / p90 |\n|---|---|")
        print(f"| best cheap bid | {dist('best', '{:.4f}', rs)} |")
        print(f"| contracts at best | {dist('size_at_best', rs=rs)} |")
        print(f"| contracts at ≤{a.tail*100:.0f}¢ | {dist('depth_tail', rs=rs)} |")
        print(f"| $ at ≤{a.tail*100:.0f}¢ | {dist('notional_tail', '${:,.0f}', rs)} |")
        print(f"| price levels ≤{a.tail*100:.0f}¢ | {dist('n_levels', '{:.0f}', rs)} |")
        print(f"| spread (cheap side) | {dist('spread', '{:.4f}', rs)} |")
        print(f"| share of snapshots with sub-cent best | {dist('subcent', '{:.0%}', rs)} |")
        print(f"| maker fills/h at best's cent bucket | {dist('fill_h', rs=rs)} |")
        print()

    # pooled by cent bucket: resting size vs fills -> queue wait
    rest = defaultdict(list)  # bucket -> per-ticker median resting contracts
    for tk, ss in per_ticker.items():
        side = max(("no", "yes"), key=lambda sd: sum(1 for s in ss if s["side"] == sd))
        byb = defaultdict(list)
        for s in ss:
            if s["side"] != side:
                continue
            bucket_sz = defaultdict(float)
            for p, q in s["levels"]:
                if p <= a.tail:
                    bucket_sz[cent_bucket(p)] += q
            for c in range(1, int(a.tail * 100) + 1):
                byb[c].append(bucket_sz.get(c, 0.0))
        for c, xs in byb.items():
            rest[c].append((tk, side, st.median(xs)))
    life = {r["ticker"]: r["life_h"] for r in rows}
    groups = (
        ("closing within 24 h", lambda tk: life.get(tk) is not None and life[tk] <= 24),
        ("closing after 24 h", lambda tk: life.get(tk) is not None and life[tk] > 24),
    )
    for label, keep in groups:
        print(f"## Queue by price bucket, {label} (cheap side, summed over tickers)\n")
        print("Resting = sum over tickers of each ticker's median resting contracts in the bucket. "
              "Fills = maker contracts filled per hour in the bucket. Wait = resting / fills, the "
              "time for a quote joining the back of the pooled queue to fill. Top = share of the "
              "bucket's fills from its single largest ticker.\n")
        print("| bucket | tickers w/ size | resting contracts | fills/h | wait (h) | top ticker share |")
        print("|---|---:|---:|---:|---:|---|")
        for c in sorted(rest):
            rs = [(tk, sd, x) for tk, sd, x in rest[c] if keep(tk)]
            tot = sum(x for _, _, x in rs)
            nz = sum(1 for _, _, x in rs if x > 0)
            per = sorted(((fills[(tk, sd, c)], tk) for tk, sd, _ in rs), reverse=True)
            fl = sum(v for v, _ in per) / hours
            wait = f"{tot / fl:,.1f}" if fl > 0 else "∞"
            top = f"{per[0][0] / (fl * hours):.0%} {per[0][1]}" if fl > 0 else "–"
            print(f"| ({c-1}¢, {c}¢] | {nz} | {tot:,.0f} | {fl:,.0f} | {wait} | {top} |")
        print()

    print("## Most-sampled tail tickers\n")
    print("| ticker | snaps | side | best | at best | ≤10¢ contracts | spread | sub-cent | fills/h | closes in (h) |")
    print("|---|---:|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in sorted(rows, key=lambda r: -r["n"])[:15]:
        life = f"{r['life_h']:.1f}" if r["life_h"] is not None else "?"
        print(f"| {r['ticker']} | {r['n']} | {r['side'].upper()} | {r['best']:.4f} | {r['size_at_best']:,.0f} | "
              f"{r['depth_tail']:,.0f} | {r['spread']:.4f} | {r['subcent']:.0%} | {r['fill_h']:,.0f} | {life} |")
    print()
    series = defaultdict(int)
    for r in rows:
        series[r["series"]] += 1
    print("Series mix: " + ", ".join(f"{k} {v}" for k, v in sorted(series.items(), key=lambda kv: -kv[1])[:12]))


if __name__ == "__main__":
    main()
