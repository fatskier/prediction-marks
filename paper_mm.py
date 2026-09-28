"""Paper market maker: quote the cheap side of in-play sports markets, fill on the real tape.

The 4-day tape left one candidate: resting 3-7c bids on the cheap side of
sports markets looked profitable even at the back of the queue. That was
found by looking, so it needs testing on data that did not exist yet, with
the queue modelled properly. This process quotes on paper against the live
exchange and records what it would have been filled.

Each cycle it:

  1. picks up to --max-markets open Sports-category markets whose cheap side
     last traded in the band, skipping any within --min-life seconds of its
     scheduled close;
  2. reads each one's book and, if the cheap side's best bid is in
     [--lo, --hi], joins it with a --size lot. The contracts already resting
     at that price are queued ahead of it. If the best bid moves, it cancels
     and rejoins at the back of the new level; if the bid leaves the band or
     the market is about to close, it cancels;
  3. reads the trade feed. A print where the maker bought our side at our
     price first works through the queue ahead, then fills us. A print at a
     lower price for our side means our level was taken out, so we fill in
     full. If the resting size at our price drops below our queue position,
     the cancellations ahead of us move us up.

Fills pay the maker fee rounded up to the cent on each fill, the conservative
reading of Kalshi's per-order rounding. Positions are held to settlement;
paper_report.py scores them. A --max-position cap per market stops one match
dominating; positions are rebuilt from the fill log at startup so a restart
does not reset it. Only markets in the current universe are quoted.

Nothing here places orders. It uses the public REST API, so the book is seen
every few seconds, not every change; that makes fills approximate.

    python paper_mm.py --out data/paper
"""

from __future__ import annotations

import argparse
import itertools
import signal
import sys
import time
import urllib.error
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fees import order_fee
from tape import ACTIVE, HOSTS, Api, Seen, Sink, read_stream, trade_ts

EPS = 1e-9


def maker_leg(trade: dict) -> tuple[str, float]:
    """(maker side, maker price) for one print: the maker is opposite the taker."""
    side = "no" if trade["taker_side"] == "yes" else "yes"
    return side, float(trade["no_price_dollars"] if side == "no" else trade["yes_price_dollars"])


def cheap_bid(book: dict) -> tuple[str, float, float] | None:
    """(side, best bid, size resting at it) for the side with the lower best bid."""
    best = []
    for side in ("yes", "no"):
        levels = book.get(side) or []
        if levels:
            p, q = levels[-1]
            best.append((float(p), side, float(q)))
    if not best:
        return None
    p, side, q = min(best)
    return side, p, q


def resting_at(book: dict, side: str, price: float) -> float:
    return sum(float(q) for p, q in (book.get(side) or []) if abs(float(p) - price) < EPS)


@dataclass
class Quote:
    """One paper bid resting in the real queue."""

    ticker: str
    side: str
    price: float
    size: float
    queue_ahead: float
    placed: float
    id: int
    filled: float = 0.0

    @property
    def remaining(self) -> float:
        return self.size - self.filled

    def on_level(self, resting: float) -> None:
        """Resting size now at our price: cancellations ahead of us move us up."""
        self.queue_ahead = min(self.queue_ahead, resting)

    def on_trade(self, maker_side: str, maker_price: float, count: float) -> float:
        """Contracts of ours this print fills."""
        if maker_side != self.side or self.remaining <= 0:
            return 0.0
        if maker_price > self.price + EPS:
            return 0.0  # a better bid than ours was hit
        if maker_price < self.price - EPS:
            got = self.remaining  # the taker went through our level, so it was cleared
            self.queue_ahead = 0.0
        else:
            ahead = min(count, self.queue_ahead)
            self.queue_ahead -= ahead
            got = min(count - ahead, self.remaining)
        self.filled += got
        return got


@dataclass
class Market:
    ticker: str
    event: str
    close_ts: float
    quote: Quote | None = None
    prints: deque = field(default_factory=lambda: deque(maxlen=200))


class PaperMM:
    def __init__(self, args):
        self.a = args
        self.api = Api(HOSTS[args.host], args.rate)
        self.sink = Sink(args.out)
        self.seen = Seen()
        self.cursor_ts = time.time() - 120
        self.markets: dict[str, Market] = {}  # the quoting universe only
        # contracts held per market, rebuilt from the fill log so a restart keeps the cap
        self.positions: dict[str, float] = defaultdict(float)
        for f in read_stream(args.out, "fills"):
            self.positions[f["ticker"]] += f["count"]
        self.meta: dict[str, dict | None] = {}
        self.category: dict[str, str | None] = {}
        self.recent: deque = deque()  # (ts, ticker, maker side, maker price) of band-ish prints
        self.ids = itertools.count(1)
        self.stop = False
        self.exit_code = 0
        self.failures = 0
        self.n_fills = 0.0

    # --- helpers ------------------------------------------------------------------------------

    def log(self, stream: str, **rec) -> None:
        rec["_recv"] = time.time()
        self.sink.write(stream, [rec])

    def is_sports(self, ticker: str) -> Market | None:
        """Market record if the ticker is an open Sports market, else None. Cached."""
        if ticker not in self.meta:
            try:
                m = self.api.get(f"/markets/{ticker}").get("market", {})
            except (urllib.error.HTTPError, RuntimeError):
                m = {}
            self.meta[ticker] = m or None
            ev = m.get("event_ticker")
            if ev and ev not in self.category:
                try:
                    self.category[ev] = self.api.get(f"/events/{ev}").get("event", {}).get("category")
                except (urllib.error.HTTPError, RuntimeError):
                    self.category[ev] = None
        m = self.meta[ticker]
        if not m or self.category.get(m.get("event_ticker")) != "Sports" or m.get("status") not in ACTIVE:
            return None
        close = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")).timestamp()
        return Market(ticker, m["event_ticker"], close)

    def cancel(self, mk: Market, why: str) -> None:
        q = mk.quote
        if q:
            self.log("orders", op="cancel", id=q.id, ticker=q.ticker, why=why, filled=q.filled)
            mk.quote = None

    # --- the three loops -----------------------------------------------------------------------

    def poll_trades(self) -> None:
        min_ts, cursor, fresh = int(self.cursor_ts) - 5, None, []
        for _ in range(self.a.max_pages):
            d = self.api.get("/markets/trades", limit=1000, min_ts=min_ts, cursor=cursor)
            fresh += [t for t in d.get("trades", []) if self.seen.add(t["trade_id"])]
            cursor = d.get("cursor")
            if not cursor or not d.get("trades"):
                break
        fresh.sort(key=trade_ts)
        for t in fresh:
            ts = trade_ts(t)
            self.cursor_ts = max(self.cursor_ts, ts)
            side, p = maker_leg(t)
            if self.a.lo - 0.02 <= p <= self.a.hi + 0.02:
                self.recent.append((ts, t["ticker"], side, p))
            mk = self.markets.get(t["ticker"])
            q = mk.quote if mk else None
            if not q or ts <= q.placed:
                continue
            got = q.on_trade(side, p, float(t["count_fp"]))
            if got > 0:
                fee = order_fee(q.price, got, maker=True)
                self.positions[q.ticker] += got
                self.n_fills += got
                self.log("fills", id=q.id, ticker=q.ticker, event=mk.event, side=q.side, price=q.price,
                         count=got, fee=fee, trade_id=t["trade_id"], trade_ts=ts,
                         cleared=p < q.price - EPS)
                if q.remaining <= EPS:
                    self.log("orders", op="done", id=q.id, ticker=q.ticker, filled=q.filled)
                    mk.quote = None
        horizon = time.time() - self.a.window
        while self.recent and self.recent[0][0] < horizon:
            self.recent.popleft()

    def refresh_markets(self) -> None:
        """Rank tickers by recent in-band prints; keep the busiest open sports markets."""
        counts: dict[str, int] = {}
        for _, tk, _, p in self.recent:
            if self.a.lo - EPS <= p <= self.a.hi + EPS:
                counts[tk] = counts.get(tk, 0) + 1
        now = time.time()
        keep: dict[str, Market] = {}
        for tk, _ in sorted(counts.items(), key=lambda kv: -kv[1]):
            if len(keep) >= self.a.max_markets:
                break
            mk = self.markets.get(tk) or self.is_sports(tk)
            if mk and mk.close_ts > now + self.a.min_life:
                keep[tk] = mk
        for tk, mk in self.markets.items():
            if tk not in keep and mk.quote:
                self.cancel(mk, "left universe")
        self.markets = keep
        self.log("universe", tickers=sorted(keep))

    def requote(self, mk: Market) -> None:
        try:
            ob = self.api.get(f"/markets/{mk.ticker}/orderbook", depth=0)
        except (urllib.error.HTTPError, RuntimeError):
            return
        raw = ob.get("orderbook_fp") or {}
        book = {"yes": raw.get("yes_dollars") or [], "no": raw.get("no_dollars") or []}
        if time.time() > mk.close_ts - self.a.min_life:
            self.cancel(mk, "near close")
            return
        cb = cheap_bid(book)
        if cb is None:
            self.cancel(mk, "empty book")
            return
        side, best, size = cb
        q = mk.quote
        if q and q.side == side and abs(q.price - best) < EPS:
            q.on_level(resting_at(book, side, best))
            return
        in_band = self.a.lo - EPS <= best <= self.a.hi + EPS
        if q:
            self.cancel(mk, "price moved" if in_band else "left band")
        held = self.positions[mk.ticker]
        if not in_band or held >= self.a.max_position:
            return
        mk.quote = Quote(mk.ticker, side, best, min(self.a.size, self.a.max_position - held),
                         queue_ahead=size, placed=time.time(), id=next(self.ids))
        self.log("orders", op="place", id=mk.quote.id, ticker=mk.ticker, event=mk.event, side=side,
                 price=best, size=mk.quote.size, queue_ahead=size)

    # --- main ----------------------------------------------------------------------------------

    def run(self) -> None:
        a = self.a
        next_trades = next_refresh = next_log = 0.0
        rr = 0
        while not self.stop:
            try:
                now = time.time()
                if now >= next_trades:
                    self.poll_trades()
                    next_trades = now + a.trade_every
                if now >= next_refresh:
                    self.refresh_markets()
                    next_refresh = now + a.refresh_every
                tks = list(self.markets)
                if tks:
                    self.requote(self.markets[tks[rr % len(tks)]])
                    rr += 1
                else:
                    time.sleep(1)
                if now >= next_log:
                    live = sum(1 for m in self.markets.values() if m.quote)
                    print(f"{datetime.now(timezone.utc):%H:%M:%S} markets={len(self.markets)} "
                          f"quotes={live} filled={self.n_fills:,.0f} calls={self.api.calls} "
                          f"errors={self.api.errors}", flush=True)
                    next_log = now + 60
                self.failures = 0
            except RuntimeError as e:
                self.failures += 1
                print(f"error: {e}", file=sys.stderr, flush=True)
                if self.failures >= a.max_failures:
                    print("network unreachable, exiting 3", file=sys.stderr, flush=True)
                    self.exit_code = 3
                    break
                time.sleep(10)
        for mk in self.markets.values():
            self.cancel(mk, "shutdown")
        self.sink.close()


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", choices=HOSTS, default="prod")
    p.add_argument("--out", default="data/paper")
    p.add_argument("--rate", type=float, default=4.0, help="requests/s (the recorder uses 8 of the 20 allowed)")
    p.add_argument("--lo", type=float, default=0.03, help="lowest cheap-side bid to quote")
    p.add_argument("--hi", type=float, default=0.07, help="highest cheap-side bid to quote")
    p.add_argument("--size", type=float, default=10, help="contracts per quote")
    p.add_argument("--max-position", type=float, default=100, help="contracts per market")
    p.add_argument("--max-markets", type=int, default=30)
    p.add_argument("--min-life", type=float, default=300, help="stop quoting this many seconds before scheduled close")
    p.add_argument("--window", type=float, default=1800, help="seconds of prints used to pick markets")
    p.add_argument("--trade-every", type=float, default=3)
    p.add_argument("--refresh-every", type=float, default=60)
    p.add_argument("--max-pages", type=int, default=200)
    p.add_argument("--max-failures", type=int, default=10)
    mm = PaperMM(p.parse_args(argv))

    def _stop(*_):
        mm.stop = True
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    mm.run()
    sys.exit(mm.exit_code)


if __name__ == "__main__":
    main()
