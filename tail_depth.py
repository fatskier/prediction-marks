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

Expiry sweeps are excluded by default. When a market's result is effectively
known, takers buy the winning side at 99c+ and sweep every cheap bid in the
book; that volume is not a queue a maker can profitably join. A fill or book
snapshot is a sweep if it falls within --sweep-min minutes (--sweep-min-sports
for markets in the Sports category) of the market's effective close: the scheduled close_time, or, if the recorder saw trading
stop earlier (status no longer active), the last print before that. On the
tape, 99.8% of <=1c fills in markets open an hour or less land in the final
10 minutes, and none fall between 15 and 60 minutes out, so the result is
insensitive to the window anywhere in 15-60.

In-play sports can be decided well before trading stops: a tennis match that
is 99c with a set to play, a cricket chase that is out of reach. Their full
trade histories show this. Of 15 sports markets that stopped during the
first tape, 13 put all of their 1c fills in the final 15 minutes, but one
tennis match had 67% of them 15-60 minutes out and another 81%; a 60-minute
window covers every 1c fill in 14 of the 15 and 98% of the fifteenth. Hence
the longer --sweep-min-sports. (occurrence_datetime is no use for detecting
play: for ITF tennis it is a placeholder later than close_time.)

A sweep is only recognisable after the fact, so each ticker still trading
at the end of the tape loses its last sweep window; one already seen to stop
is kept whole. Fill rates are per ticker over the span it was sampled, since
tickers rotate through the universe. --keep-sweeps restores the raw numbers.

    python tail_depth.py --tape data/tape
"""

from __future__ import annotations

import argparse
import bisect
import statistics as st
from collections import defaultdict
from datetime import datetime

from tape import ACTIVE, TAIL, read_stream, trade_ts


def iso_ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def effective_close(scheduled: float, status_events: list[tuple[float, str]], prints: list[float]) -> float:
    """When trading actually stopped.

    status_events are (recv_ts, status) in time order; prints are sorted print
    times. If the market was first seen not active at T, trading stopped at the
    last print at or before T (or at T with no prints), unless the scheduled
    close came first.
    """
    stop = next((ts for ts, stt in status_events if stt and stt not in ACTIVE), None)
    if stop is None:
        return scheduled
    i = bisect.bisect_right(prints, stop)
    if i:
        stop = prints[i - 1]
    return min(scheduled, stop)


def in_sweep(t: float, close_eff: float | None, window: float) -> bool:
    return close_eff is not None and t >= close_eff - window


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
    ap.add_argument("--sweep-min", type=float, default=15, help="minutes before effective close treated as a sweep")
    ap.add_argument("--sweep-min-sports", type=float, default=60,
                    help="sweep window for Sports-category markets, which can be decided in play")
    ap.add_argument("--min-span", type=float, default=5, help="minutes a ticker must be sampled for its fill rate to count")
    ap.add_argument("--keep-sweeps", action="store_true", help="do not exclude expiry sweeps")
    a = ap.parse_args(argv)

    tail_set: set[str] = set()
    for u in read_stream(a.tape, "universe"):
        tail_set.update(u["tail"])
    meta = {m["ticker"]: m for m in read_stream(a.tape, "markets")}
    category = {e["event_ticker"]: e.get("category") for e in read_stream(a.tape, "events")}
    status = defaultdict(list)
    for r in read_stream(a.tape, "status"):
        status[r["ticker"]].append((r["_recv"], r["status"]))

    def sports(tk: str) -> bool:
        return category.get(meta.get(tk, {}).get("event_ticker")) == "Sports"

    def window(tk: str) -> float:
        return (a.sweep_min_sports if sports(tk) else a.sweep_min) * 60

    trades = [t for t in read_stream(a.tape, "trades") if t["ticker"] in tail_set]
    for t in trades:
        t["_ts"] = trade_ts(t)
    prints = defaultdict(list)
    for t in trades:
        prints[t["ticker"]].append(t["_ts"])
    close_eff: dict[str, float | None] = {}
    for tk in tail_set:
        m = meta.get(tk)
        if not m or not m.get("close_time"):
            close_eff[tk] = None
            continue
        close_eff[tk] = effective_close(iso_ts(m["close_time"]), sorted(status[tk]), sorted(prints[tk]))
    excluded = (lambda tk, t: False) if a.keep_sweeps else (lambda tk, t: in_sweep(t, close_eff[tk], window(tk)))

    books = [b for b in read_stream(a.tape, "books") if b["ticker"] in tail_set]
    t_min = min((b["_recv"] for b in books), default=0.0)
    t_max = max((b["_recv"] for b in books), default=0.0)

    def t_end(tk: str) -> float:
        """Last time at which tk's data can be classified."""
        ce = close_eff.get(tk)
        if a.keep_sweeps or (ce is not None and ce <= t_max):
            return t_max
        return t_max - window(tk)

    per_ticker = defaultdict(list)
    n_books = n_empty = n_noncheap = n_sweep_books = n_censored = 0
    for b in books:
        if b["_recv"] > t_end(b["ticker"]):
            n_censored += 1
            continue
        n_books += 1
        if excluded(b["ticker"], b["_recv"]):
            n_sweep_books += 1
            continue
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
    # each ticker's sampled span; fill rates need a few minutes of it
    span = {tk: (ss[0]["t"], ss[-1]["t"]) for tk, ss in per_ticker.items()}
    span_h = {tk: (hi - lo) / 3600 for tk, (lo, hi) in span.items()}
    rated = {tk for tk, h in span_h.items() if h * 60 >= a.min_span}

    # maker fills on the cheap side, per ticker and cent bucket, over that ticker's span
    fills = defaultdict(float)  # (ticker, side, bucket) -> contracts
    swept = defaultdict(float)  # bucket -> contracts excluded as sweeps
    kept = defaultdict(float)   # bucket -> contracts not excluded, same window as swept
    for t in trades:
        tk = t["ticker"]
        # window on execution time: the startup backfill shares a single _recv
        if not (t_min <= t["_ts"] <= t_end(tk)):
            continue
        # taker buys YES -> a maker NO bid at no_price filled, and vice versa
        maker_side = "no" if t["taker_side"] == "yes" else "yes"
        p = f(t["no_price_dollars"] if maker_side == "no" else t["yes_price_dollars"])
        if p > a.tail:
            continue
        c = cent_bucket(p)
        if excluded(tk, t["_ts"]):
            swept[c] += f(t["count_fp"])
            continue
        kept[c] += f(t["count_fp"])
        if tk in rated and span[tk][0] <= t["_ts"] <= span[tk][1]:
            fills[(tk, maker_side, c)] += f(t["count_fp"])

    def rate(tk: str, side: str, c: int) -> float:
        return fills[(tk, side, c)] / span_h[tk] if tk in rated else float("nan")

    print(f"# Tail book depth, {datetime.utcfromtimestamp(t_min):%Y-%m-%d %H:%M}"
          f"–{datetime.utcfromtimestamp(t_max):%H:%M} UTC ({hours:.2f} h)\n")
    n_snap = sum(len(v) for v in per_ticker.values())
    n_sports = sum(1 for tk in per_ticker if sports(tk))
    print(f"{len(tail_set)} tickers ever in the tail universe; {len(per_ticker)} with a cheap-side book "
          f"({n_sports} sports), {len(rated)} sampled >= {a.min_span:g} min so their fill rates count. "
          f"{n_books} tail snapshots: {n_snap} with a cheap side, {n_empty} empty, "
          f"{n_noncheap} no bid <= {a.tail:.0%} on either side, {n_sweep_books} in a sweep window; "
          f"{n_censored} more too recent to classify.\n")
    if a.keep_sweeps:
        print("Expiry sweeps INCLUDED (--keep-sweeps).\n")
    else:
        tot = sum(swept.values()) + sum(kept.values())
        if tot:
            print(f"Expiry sweeps excluded: fills and snapshots within {a.sweep_min:g} min of effective close "
                  f"({a.sweep_min_sports:g} min for sports). That removed {sum(swept.values()):,.0f} of "
                  f"{tot:,.0f} cheap-side maker contracts ({sum(swept.values()) / tot:.0%}), and "
                  f"{swept[1]:,.0f} of {swept[1] + kept[1]:,.0f} at (0¢, 1¢].\n")

    # per-ticker medians, then distribution across tickers
    rows = []
    for tk, ss in per_ticker.items():
        m = meta.get(tk, {})
        close = m.get("close_time")
        life_h = None
        if close:
            life_h = (iso_ts(close) - st.median(s["t"] for s in ss)) / 3600
        side = max(("no", "yes"), key=lambda sd: sum(1 for s in ss if s["side"] == sd))
        fb = cent_bucket(st.median(s["best"] for s in ss))
        fill_h = rate(tk, side, fb)
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
            "sports": sports(tk),
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
        ("sports", sports),
        ("non-sports", lambda tk: not sports(tk)),
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
            # only tickers sampled long enough to have a fill rate
            rs = [(tk, sd, x) for tk, sd, x in rest[c] if keep(tk) and tk in rated]
            tot = sum(x for _, _, x in rs)
            nz = sum(1 for _, _, x in rs if x > 0)
            per = sorted(((rate(tk, sd, c), tk) for tk, sd, _ in rs), reverse=True)
            fl = sum(v for v, _ in per)
            wait = f"{tot / fl:,.1f}" if fl > 0 else "∞"
            top = f"{per[0][0] / fl:.0%} {per[0][1]}" if fl > 0 else "–"
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
