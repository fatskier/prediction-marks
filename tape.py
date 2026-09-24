"""Record a Kalshi order-book and trade tape for the market-making question.

Stage 1 closed the taker version of "buy cheap NO". The open question is the
maker version: does resting a cheap-tail quote earn the maker edge in
Becker's aggregates once queue position and adverse selection are paid for?
Answering that needs the book, not just prints, so this records two streams
from the public (unauthenticated) REST API:

  trades  every print on the exchange, from /markets/trades polled by min_ts
  books   full-depth snapshots of a rotating universe of tickers

The universe is chosen from the trade tape itself. Scanning /markets does not
work: there are more than 30k open markets. Each refresh ranks tickers by
recent print count and keeps the most active tail-priced ones (last YES print
<= TAIL or >= 1-TAIL), plus a few mid-priced controls.

Ranking by activity alone fills the universe with markets on their way to
settlement: in the first hour 80% of tail book snapshots fell in the expiry
sweep windows that tail_depth.py throws away. So a ticker is only eligible
while it is outside its own sweep window (SWEEP_MIN before close,
SWEEP_MIN_SPORTS for Sports-category events, which can be decided in play)
and has not been seen to stop trading.

Output is gzipped JSONL, one file per stream per UTC hour:

  <out>/YYYY-MM-DD/HH.trades.jsonl.gz
  <out>/YYYY-MM-DD/HH.books.jsonl.gz
  <out>/YYYY-MM-DD/HH.markets.jsonl.gz   (metadata, once per ticker per run)
  <out>/YYYY-MM-DD/HH.universe.jsonl.gz  (each universe refresh)
  <out>/YYYY-MM-DD/HH.status.jsonl.gz    (market status changes, polled each refresh)
  <out>/YYYY-MM-DD/HH.events.jsonl.gz    (event metadata incl. category, once per event)

Status is polled so analysis can find when trading actually stopped. Many
markets close early (a tennis match ends), and their resolution sweep happens
then, long before the scheduled close_time.

If the API stays unreachable (every request given up on for --max-failures
consecutive trade polls), tape.py exits with code 3 rather than retrying
forever. In a cloud session the outbound proxy's port can change when the
container restarts, and a recorder that outlives the restart keeps the stale
port; only a process started from a fresh shell can reach the network again.
run_tape.sh stops on exit code 3 for the same reason.

A killed process leaves its last file without a gzip trailer, so a restart
never appends to an existing file: it writes HH.<stream>.1.jsonl.gz, .2, and
so on. Read the tape with read_stream(), which tolerates the missing trailer.

Prices and sizes are kept as the API's decimal strings so nothing is lost to
float rounding; tails can have sub-cent ticks (price_level_structure).

    python tape.py --out data/tape
    python tape.py --host demo --out data/tape-demo
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

HOSTS = {
    "prod": "https://api.elections.kalshi.com/trade-api/v2",
    "demo": "https://demo-api.kalshi.co/trade-api/v2",
}
TAIL = 0.10
ACTIVE = ("active", "open")
FINAL = ("finalized", "settled")
# minutes before effective close treated as an expiry sweep; tail_depth.py documents the evidence
SWEEP_MIN = 15
SWEEP_MIN_SPORTS = 60


class Api:
    """Minimal thread-safe GET client with a global request-rate cap and 429 backoff."""

    def __init__(self, base: str, rate: float):
        self.base = base
        self.min_gap = 1.0 / rate
        self.last = 0.0
        self.lock = threading.Lock()
        self.calls = 0
        self.errors = 0

    def get(self, path: str, **params) -> dict:
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        url = f"{self.base}{path}" + (f"?{qs}" if qs else "")
        for attempt in range(6):
            with self.lock:
                slot = max(time.monotonic(), self.last + self.min_gap)
                self.last = slot
                self.calls += 1
            if slot > time.monotonic():
                time.sleep(slot - time.monotonic())
            try:
                with urllib.request.urlopen(url, timeout=20) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                with self.lock:
                    self.errors += 1
                if e.code == 404:
                    raise
                if e.code != 429 and e.code < 500:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
                with self.lock:
                    self.errors += 1
            time.sleep(min(2 ** attempt, 30))
        raise RuntimeError(f"giving up on {url}")


class Sink:
    """Appends JSONL records to hourly gzip files, one per stream."""

    def __init__(self, root: str):
        self.root = root
        self.files: dict[str, tuple[str, gzip.GzipFile]] = {}

    def write(self, stream: str, records: list[dict]) -> None:
        if not records:
            return
        hour = datetime.now(timezone.utc).strftime("%Y-%m-%d/%H")
        cur = self.files.get(stream)
        if cur is None or cur[0] != hour:
            if cur:
                cur[1].close()
            path = os.path.join(self.root, f"{hour}.{stream}.jsonl.gz")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            part = 0
            while os.path.exists(path):
                part += 1
                path = os.path.join(self.root, f"{hour}.{stream}.{part}.jsonl.gz")
            cur = (hour, gzip.open(path, "wt", encoding="utf-8"))
            self.files[stream] = cur
        f = cur[1]
        for r in records:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
        f.flush()

    def close(self) -> None:
        for _, f in self.files.values():
            f.close()
        self.files.clear()


def read_stream(root: str, stream: str):
    """Yield every record of one stream, in file order, across all parts.

    A file whose writer was killed ends without a gzip trailer; everything
    flushed before the kill is still yielded.
    """
    import glob
    import re

    def key(p):
        m = re.search(rf"(\d{{4}}-\d{{2}}-\d{{2}})/(\d{{2}})\.{stream}(?:\.(\d+))?\.jsonl\.gz$", p)
        return (m.group(1), m.group(2), int(m.group(3) or 0)) if m else (p, "", 0)

    for path in sorted(glob.glob(os.path.join(root, "*", f"*.{stream}*.jsonl.gz")), key=key):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                for line in f:
                    if line.endswith("\n"):
                        yield json.loads(line)
        except EOFError:
            pass


class Seen:
    """Bounded set of recently seen trade ids."""

    def __init__(self, cap: int = 200_000):
        self.cap = cap
        self.d: OrderedDict[str, None] = OrderedDict()

    def add(self, key: str) -> bool:
        """Return True if key is new."""
        if key in self.d:
            return False
        self.d[key] = None
        if len(self.d) > self.cap:
            self.d.popitem(last=False)
        return True


def trade_ts(t: dict) -> float:
    return datetime.fromisoformat(t["created_time"].replace("Z", "+00:00")).timestamp()


def is_tail(yes_price: float, tail: float = TAIL) -> bool:
    return yes_price <= tail or yes_price >= 1 - tail


def select_universe(
    recent: list[tuple[float, str, float]],
    now: float,
    window: float,
    n_tail: int,
    n_ctrl: int,
    tail: float = TAIL,
    eligible=lambda ticker: True,
) -> tuple[list[str], list[str]]:
    """Rank tickers by print count in the window, split by last YES price.

    `recent` holds (ts, ticker, yes_price) in arrival order. `eligible` is
    consulted only for tickers that would otherwise be taken, so it can be an
    expensive lookup. Returns (tail_tickers, control_tickers), each
    most-active first.
    """
    counts: Counter[str] = Counter()
    last: dict[str, tuple[float, float]] = {}
    for ts, ticker, px in recent:
        if ts < now - window:
            continue
        counts[ticker] += 1
        if ticker not in last or ts >= last[ticker][0]:
            last[ticker] = (ts, px)
    tails, ctrls = [], []
    for ticker, _ in counts.most_common():
        bucket = tails if is_tail(last[ticker][1], tail) else ctrls
        cap = n_tail if bucket is tails else n_ctrl
        if len(bucket) < cap and eligible(ticker):
            bucket.append(ticker)
        if len(tails) >= n_tail and len(ctrls) >= n_ctrl:
            break
    return tails, ctrls


class Recorder:
    def __init__(self, args):
        self.args = args
        self.api = Api(HOSTS[args.host], args.rate)
        self.sink = Sink(args.out)
        self.seen = Seen()
        self.recent: deque[tuple[float, str, float]] = deque()
        self.universe: list[str] = []
        self.close_ts: dict[str, float | None] = {}
        self.event_of: dict[str, str | None] = {}
        # ticker -> last recorded status; seeded from earlier runs' universes in the same tape
        self.status: dict[str, str | None] = {}
        for u in read_stream(args.out, "universe"):
            for tk in u["tail"] + u["control"]:
                self.status.setdefault(tk, None)
        # event metadata (category decides the sweep window); fetch any earlier runs missed
        self.category = {e["event_ticker"]: e.get("category") for e in read_stream(args.out, "events")}
        self.events_done = set(self.category)
        self.events_todo = {m["event_ticker"] for m in read_stream(args.out, "markets")
                            if m.get("event_ticker")} - self.events_done
        self.cursor_ts = time.time() - args.window
        self.stop = False
        self.n_trades = 0
        self.n_books = 0
        self.failures = 0
        self.exit_code = 0

    def poll_trades(self) -> None:
        """Fetch all prints since cursor_ts (minus overlap), dedupe, record."""
        min_ts = int(self.cursor_ts) - 5
        cursor, fresh = None, []
        for _ in range(self.args.max_pages):
            d = self.api.get("/markets/trades", limit=1000, min_ts=min_ts, cursor=cursor)
            for t in d.get("trades", []):
                if self.seen.add(t["trade_id"]):
                    fresh.append(t)
            cursor = d.get("cursor")
            if not cursor or not d.get("trades"):
                break
        else:
            print(f"warning: trade poll hit {self.args.max_pages} pages; oldest prints since "
                  f"{min_ts} may be missing", file=sys.stderr, flush=True)
        now = time.time()
        for t in fresh:
            t["_recv"] = now
        fresh.sort(key=trade_ts)
        self.sink.write("trades", fresh)
        self.n_trades += len(fresh)
        for t in fresh:
            ts = trade_ts(t)
            self.recent.append((ts, t["ticker"], float(t["yes_price_dollars"])))
            self.cursor_ts = max(self.cursor_ts, ts)
        while self.recent and self.recent[0][0] < now - self.args.window:
            self.recent.popleft()

    def event_category(self, ev: str | None) -> str | None:
        if ev and ev not in self.events_done:
            try:
                e = self.api.get(f"/events/{ev}").get("event", {})
            except (urllib.error.HTTPError, RuntimeError):
                return None
            e.pop("markets", None)
            e["_recv"] = time.time()
            self.sink.write("events", [e])
            self.category[ev] = e.get("category")
            self.events_done.add(ev)
            self.events_todo.discard(ev)
        return self.category.get(ev)

    def eligible(self, ticker: str) -> bool:
        """Still trading and outside its sweep window. Metadata is fetched and recorded once.

        Expiring markets print at the extremes on their way to settlement and
        dominate a ranking by activity, so without this most book requests go
        to sweeps that the analysis discards.
        """
        if self.status.get(ticker) not in (None, *ACTIVE):
            return False
        if ticker not in self.close_ts:
            try:
                m = self.api.get(f"/markets/{ticker}").get("market", {})
            except (urllib.error.HTTPError, RuntimeError):
                self.close_ts[ticker] = None
                return False
            m["_recv"] = time.time()
            self.sink.write("markets", [m])
            self.event_of[ticker] = m.get("event_ticker")
            ok = m.get("status") in ("active", "open") and m.get("close_time")
            self.close_ts[ticker] = (
                datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")).timestamp() if ok else None
            )
        close = self.close_ts[ticker]
        if close is None:
            return False
        sports = self.event_category(self.event_of.get(ticker)) == "Sports"
        window = (self.args.sweep_min_sports if sports else self.args.sweep_min) * 60
        return close > time.time() + window

    def refresh_universe(self) -> None:
        tails, ctrls = select_universe(
            list(self.recent), time.time(), self.args.window, self.args.n_tail, self.args.n_ctrl,
            eligible=self.eligible,
        )
        self.universe = tails + ctrls
        self.sink.write("universe", [{"_recv": time.time(), "tail": tails, "control": ctrls}])
        for tk in self.universe:
            self.status.setdefault(tk, None)
        self.poll_status()
        self.fetch_events()

    def fetch_events(self) -> None:
        """Fetch categories for events seen in earlier runs of the same tape."""
        for ev in sorted(self.events_todo):
            self.event_category(ev)

    def poll_status(self) -> None:
        """Record status changes for every ticker ever tracked, until it is final."""
        live = [tk for tk, st in self.status.items() if st not in FINAL]
        for i in range(0, len(live), 100):
            chunk = live[i:i + 100]
            try:
                d = self.api.get("/markets", tickers=",".join(chunk), limit=len(chunk))
            except (urllib.error.HTTPError, RuntimeError):
                continue
            now = time.time()
            recs = []
            for m in d.get("markets", []):
                tk = m["ticker"]
                if m.get("status") != self.status.get(tk):
                    self.status[tk] = m.get("status")
                    recs.append({"_recv": now, "ticker": tk, "status": m.get("status"),
                                 "result": m.get("result"), "close_time": m.get("close_time")})
            self.sink.write("status", recs)

    def snapshot(self, ticker: str) -> dict | None:
        t0 = time.time()
        try:
            d = self.api.get(f"/markets/{ticker}/orderbook", depth=0)
        except (urllib.error.HTTPError, RuntimeError):
            return None
        ob = d.get("orderbook_fp") or d.get("orderbook") or {}
        rec = {"_recv": time.time(), "_sent": t0, "ticker": ticker,
               "yes": ob.get("yes_dollars") or ob.get("yes") or [],
               "no": ob.get("no_dollars") or ob.get("no") or []}
        return rec

    def run(self) -> None:
        a = self.args
        deadline = time.time() + a.duration if a.duration else float("inf")
        self.poll_trades()
        self.refresh_universe()
        next_trades = time.time() + a.trade_every
        next_universe = time.time() + a.universe_every
        next_log = time.time() + 60
        i = 0
        pool = ThreadPoolExecutor(a.workers)
        while not self.stop and time.time() < deadline:
            try:
                now = time.time()
                if now >= next_trades:
                    self.poll_trades()
                    next_trades = now + a.trade_every
                if now >= next_universe:
                    self.refresh_universe()
                    next_universe = now + a.universe_every
                if self.universe:
                    batch = [self.universe[(i + k) % len(self.universe)] for k in range(a.workers * 2)]
                    i += len(batch)
                    recs = [r for r in pool.map(self.snapshot, batch) if r]
                    recs.sort(key=lambda r: r["_recv"])
                    self.sink.write("books", recs)
                    self.n_books += len(recs)
                else:
                    time.sleep(1)
                if now >= next_log:
                    print(f"{datetime.now(timezone.utc):%H:%M:%S} trades={self.n_trades} "
                          f"books={self.n_books} universe={len(self.universe)} "
                          f"calls={self.api.calls} errors={self.api.errors}", flush=True)
                    next_log = now + 60
                self.failures = 0
            except RuntimeError as e:
                self.failures += 1
                print(f"error: {e}", file=sys.stderr, flush=True)
                if self.failures >= a.max_failures:
                    print(f"{self.failures} consecutive failures; network unreachable, exiting 3",
                          file=sys.stderr, flush=True)
                    self.exit_code = 3
                    break
                time.sleep(10)
        pool.shutdown()
        self.sink.close()


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", choices=HOSTS, default="prod")
    p.add_argument("--out", default="data/tape")
    p.add_argument("--rate", type=float, default=8.0, help="max requests/second (basic tier allows 20 reads/s)")
    p.add_argument("--workers", type=int, default=6, help="concurrent book requests (still capped by --rate)")
    p.add_argument("--n-tail", type=int, default=40)
    p.add_argument("--n-ctrl", type=int, default=10)
    p.add_argument("--window", type=float, default=1800, help="seconds of prints used to rank the universe")
    p.add_argument("--sweep-min", type=float, default=SWEEP_MIN,
                   help="skip markets within this many minutes of close")
    p.add_argument("--sweep-min-sports", type=float, default=SWEEP_MIN_SPORTS,
                   help="the same for Sports-category markets")
    p.add_argument("--max-failures", type=int, default=10,
                   help="exit with code 3 after this many consecutive failed trade polls")
    p.add_argument("--max-pages", type=int, default=200, help="page cap per trade poll")
    p.add_argument("--trade-every", type=float, default=5)
    p.add_argument("--universe-every", type=float, default=60)
    p.add_argument("--duration", type=float, default=0, help="seconds to run; 0 = until killed")
    rec = Recorder(p.parse_args(argv))

    def _stop(*_):
        rec.stop = True
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    rec.run()
    sys.exit(rec.exit_code)


if __name__ == "__main__":
    main()
