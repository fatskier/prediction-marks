"""Do cheap maker fills win? Score every cheap-side maker fill on the tape.

The maker edge in Becker's aggregates is 1.57% resolution at a 1c cost basis,
against 1% implied: a maker who rests a 1c bid and gets filled is paid more
often than the price says. The book tape showed the queue is short enough to
get filled. This asks whether the fills a maker would actually get, on today's
exchange, are the good ones.

For every print on the trade tape, the maker is the side opposite the taker:
a taker buying YES at 1-p fills a maker's NO bid at p. Every maker fill at a
cost basis <= --tail is kept, across all markets, not only the ones whose
books were sampled. Once the market settles, the fill wins if the result is
the maker's side. Net return on stake per contract is

    (win - price - maker_fee(price)) / price

with the unrounded maker fee from fees.py. Kalshi rounds fees up to the cent
per order, which at 1c can cost a small order far more; the unrounded fee is
the most favourable case for the maker.

Expiry sweeps are split out, not dropped. Once a market settles the API
reports the moment trading actually stopped as close_time (a tennis match
that ended early shows its real end), so a fill is a sweep if it came within
--sweep-min minutes of that, --sweep-min-sports for Sports-category series.
Fills in markets not yet settled cannot be scored; the report says how much of
the volume that leaves out, since settled-by-now skews to short-dated markets.

Intervals are bootstrapped over events: fills in one event (every strike on
one BTC hour, both sides of one match) resolve together.

    python fill_outcomes.py --tape data/tape          # fetches results, cached
    python fill_outcomes.py --tape data/tape --offline
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime

from fees import fee_per_contract
from tape import HOSTS, SWEEP_MIN, SWEEP_MIN_SPORTS, TAIL, read_stream, trade_ts

CRICKET_SERIES = ("KXODIMATCH", "KXT20MATCH", "KXTESTMATCH", "KXWODIMATCH", "KXWT20MATCH")


def iso_ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def cent_bucket(p: float) -> int:
    """Whole-cent bucket, sub-cent ticks rounded up: 0.013 -> 2, 0.001 -> 1."""
    return max(1, int(-(-round(p * 10000) // 100)))


def maker_leg(trade: dict) -> tuple[str, float]:
    """(maker side, maker cost basis in dollars) for one print."""
    side = "no" if trade["taker_side"] == "yes" else "yes"
    return side, float(trade["no_price_dollars"] if side == "no" else trade["yes_price_dollars"])


def series_of(ticker: str) -> str:
    return ticker.split("-")[0]


def group_of(ticker: str, category: str | None) -> str:
    if ticker.startswith("KXMVE"):
        return "combo"
    if category == "Sports":
        return "sports"
    if category in ("Crypto",):
        return "crypto"
    return "other"


def net_return(win: float, price: float) -> float:
    """Net return on stake for one contract bought as maker at price."""
    return (win - price - fee_per_contract(price, maker=True)) / price


# --- result and category lookups, cached under <tape>/../outcomes --------------------------------

class Lookup:
    def __init__(self, cache_dir: str, host: str, offline: bool, rate: float = 3.0):
        self.dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.base = HOSTS[host]
        self.offline = offline
        self.gap = 1.0 / rate
        self.last = 0.0
        self.markets = self._load("markets.jsonl", "ticker")
        self.series = self._load("series.jsonl", "ticker")

    def _load(self, name: str, key: str) -> dict:
        path = os.path.join(self.dir, name)
        out = {}
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    if line.endswith("\n"):
                        r = json.loads(line)
                        out[r[key]] = r
        return out

    def _save(self, name: str, recs: list[dict]) -> None:
        with open(os.path.join(self.dir, name), "a") as f:
            for r in recs:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")

    def _get(self, path: str, **q) -> dict:
        url = self.base + path + ("?" + urllib.parse.urlencode(q) if q else "")
        for i in range(6):
            wait = self.last + self.gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return {}
                if e.code != 429 and e.code < 500:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                pass
            time.sleep(min(2 ** i, 30))
        raise RuntimeError(f"giving up on {url}")

    def fetch_markets(self, tickers) -> None:
        """Fetch any ticker not cached as settled. Settled records never change."""
        todo = [t for t in tickers if self.markets.get(t, {}).get("result") not in ("yes", "no")]
        if self.offline or not todo:
            return
        print(f"fetching results for {len(todo):,} markets…", flush=True)
        for i in range(0, len(todo), 100):
            chunk = todo[i:i + 100]
            d = self._get("/markets", tickers=",".join(chunk), limit=len(chunk))
            keep = ("ticker", "event_ticker", "status", "result", "close_time", "open_time")
            recs = [{k: m.get(k) for k in keep} for m in d.get("markets", [])]
            for r in recs:
                self.markets[r["ticker"]] = r
            # only settled records are worth caching; the rest are re-asked next run
            self._save("markets.jsonl", [r for r in recs if r["result"] in ("yes", "no")])

    def category(self, series: str) -> str | None:
        if series not in self.series and not self.offline:
            s = self._get(f"/series/{series}").get("series", {})
            rec = {"ticker": series, "category": s.get("category")}
            self.series[series] = rec
            self._save("series.jsonl", [rec])
        return self.series.get(series, {}).get("category")


# --- scoring --------------------------------------------------------------------------------------

def bootstrap_ci(rows: list[tuple[str, float, float, float]], n_boot: int, seed: int = 0):
    """95% CI on contract-weighted net return, resampling events.

    rows: (event, contracts, contracts that won, price*contracts, fee*contracts).
    """
    if not rows:
        return float("nan"), float("nan")
    by_ev = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for ev, c, w, pc, fc in rows:
        e = by_ev[ev]
        e[0] += c; e[1] += w; e[2] += pc; e[3] += fc
    evs = list(by_ev.values())
    rng = random.Random(seed)
    stats = []
    for _ in range(n_boot):
        c = w = pc = fc = 0.0
        for _ in evs:
            e = evs[rng.randrange(len(evs))]
            c += e[0]; w += e[1]; pc += e[2]; fc += e[3]
        if pc > 0:
            stats.append((w - pc - fc) / pc)
    stats.sort()
    if not stats:
        return float("nan"), float("nan")
    return stats[int(0.025 * len(stats))], stats[int(0.975 * len(stats)) - 1]


def summarize(fills, label: str, n_boot: int) -> list[str]:
    """fills: per-(market, side, bucket) totals: ticker, event, bucket, count, win, pc, fc.

    pc and fc are price*contracts and maker fee*contracts summed over the fills.
    """
    out = [f"### {label}\n",
           "| bucket | fills (contracts) | markets | events | avg price | win rate | breakeven | "
           "net return | 95% CI (events) | largest event |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---|---|"]
    by_b = defaultdict(list)
    for x in fills:
        by_b[x["bucket"]].append(x)
    for b in sorted(by_b):
        xs = by_b[b]
        c = sum(x["count"] for x in xs)
        w = sum(x["count"] * x["win"] for x in xs)
        pc = sum(x["pc"] for x in xs)
        fc = sum(x["fc"] for x in xs)
        avg_p = pc / c
        rows = [(x["event"], x["count"], x["count"] * x["win"], x["pc"], x["fc"]) for x in xs]
        lo, hi = bootstrap_ci(rows, n_boot)
        ev_c = defaultdict(float)
        for x in xs:
            ev_c[x["event"]] += x["count"]
        top_ev, top_c = max(ev_c.items(), key=lambda kv: kv[1])
        out.append(
            f"| ({b-1}¢, {b}¢] | {c:,.0f} | {len({x['ticker'] for x in xs}):,} | {len(ev_c):,} | "
            f"{avg_p*100:.2f}¢ | {w/c:.2%} | {(avg_p + fc/c):.2%} | {(w - pc - fc)/pc:+.0%} | "
            f"[{lo:+.0%}, {hi:+.0%}] | {top_c/c:.0%} {top_ev} |")
    out.append("")
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tape", default="data/tape")
    ap.add_argument("--cache", default=None, help="result cache dir (default: <tape>/../outcomes)")
    ap.add_argument("--host", choices=HOSTS, default="prod")
    ap.add_argument("--offline", action="store_true", help="use cached results only")
    ap.add_argument("--tail", type=float, default=TAIL)
    ap.add_argument("--sweep-min", type=float, default=SWEEP_MIN)
    ap.add_argument("--sweep-min-sports", type=float, default=SWEEP_MIN_SPORTS)
    ap.add_argument("--sweep-min-cricket", type=float, default=None,
                    help="separate window for one-day/T20 cricket series (default: the sports window)")
    ap.add_argument("--boot", type=int, default=1000)
    a = ap.parse_args(argv)
    cache = a.cache or os.path.join(os.path.dirname(os.path.abspath(a.tape)), "outcomes")

    # Two streaming passes, so memory stays flat however long the tape gets.
    # 1. which markets have cheap maker fills
    tickers: set[str] = set()
    t_lo, t_hi = float("inf"), 0.0
    for t in read_stream(a.tape, "trades"):
        side, p = maker_leg(t)
        ts = trade_ts(t)
        t_lo, t_hi = min(t_lo, ts), max(t_hi, ts)
        if 0 < p <= a.tail:
            tickers.add(t["ticker"])

    # 2. results and categories
    look = Lookup(cache, a.host, a.offline)
    look.fetch_markets(sorted(tickers))

    def window(tk: str) -> float:
        s = series_of(tk)
        if a.sweep_min_cricket is not None and s in CRICKET_SERIES:
            return a.sweep_min_cricket * 60
        return (a.sweep_min_sports if look.category(s) == "Sports" else a.sweep_min) * 60

    close_of = {tk: iso_ts(m["close_time"]) for tk, m in look.markets.items()
                if m.get("result") in ("yes", "no") and m.get("close_time")}
    win_of = {tk: look.markets[tk]["result"] for tk in close_of}
    win_dur = {tk: window(tk) for tk in close_of}

    # 3. score: totals per (market, maker side, bucket, sweep?)
    agg = defaultdict(lambda: [0.0, 0.0, 0.0])
    unsettled_c = total_c = 0.0
    for t in read_stream(a.tape, "trades"):
        side, p = maker_leg(t)
        if not 0 < p <= a.tail:
            continue
        tk, c = t["ticker"], float(t["count_fp"])
        total_c += c
        if tk not in close_of:
            unsettled_c += c
            continue
        sweep = trade_ts(t) >= close_of[tk] - win_dur[tk]
        e = agg[(tk, side, cent_bucket(p), sweep)]
        e[0] += c
        e[1] += c * p
        e[2] += c * fee_per_contract(p, maker=True)

    kept, swept = [], []
    for (tk, side, b, sweep), (c, pc, fc) in agg.items():
        x = {"ticker": tk, "event": look.markets[tk].get("event_ticker") or tk, "bucket": b,
             "count": c, "pc": pc, "fc": fc, "win": 1.0 if win_of[tk] == side else 0.0,
             "group": group_of(tk, look.category(series_of(tk)))}
        (swept if sweep else kept).append(x)

    print(f"# Cheap maker fills: do they win?\n")
    print(f"Trade tape {datetime.utcfromtimestamp(t_lo):%Y-%m-%d %H:%M}–"
          f"{datetime.utcfromtimestamp(t_hi):%Y-%m-%d %H:%M} UTC. {total_c:,.0f} maker contracts at "
          f"≤{a.tail*100:.0f}¢; {unsettled_c:,.0f} ({unsettled_c/total_c:.0%}) are in markets not yet "
          f"settled and are left out. Of the rest, {sum(x['count'] for x in swept):,.0f} are sweeps "
          f"(within {a.sweep_min:g} min of the actual close, {a.sweep_min_sports:g} for sports"
          + (f", {a.sweep_min_cricket:g} for cricket" if a.sweep_min_cricket is not None else "")
          + f") and {sum(x['count'] for x in kept):,.0f} are not.\n")
    print("Net return is on stake, after the unrounded maker fee. Breakeven is the win rate needed "
          "to cover price plus fee. Becker's 2021–25 aggregate for makers at 1¢: 1.57% win.\n")

    lines = summarize(kept, "Non-sweep fills, all markets", a.boot)
    for g in ("sports", "crypto", "other", "combo"):
        xs = [x for x in kept if x["group"] == g]
        if xs:
            lines += summarize(xs, f"Non-sweep fills, {g}", a.boot)
    lines += summarize(swept, "Sweep fills (for contrast)", a.boot)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
