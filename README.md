# Kalshi "optimism tax"

Testing whether Becker's *Microstructure of Wealth Transfer in Prediction Markets*
(2026-01-18) supports an executable cheap-tail strategy, before any trading bot
is built.

- **Stage 1 — taker ("buy cheap NO"): no.** Below.
- **Stage 2 — maker (rest cheap bids and collect the maker edge): no edge
  established yet.** In-play sports is the only open candidate. Crypto and
  combo makers at 1¢ are clearly adversely selected. See
  [Stage 2](#stage-2--the-maker-question-an-order-book-tape).

## Stage 1 result: no

The role × side × price cell has been computed. **Taker-reachable cheap NO is
negative at 9 of the 10 price levels from 1¢ to 10¢**, net of fees:

```
 1c:+6%   2c:-7%   3c:-65%   4c:-40%   5c:-40%
 6c:-36%  7c:-42%  8c:-33%   9c:-32%  10c:-25%
```

Mean −31.5%, spread 71pp. The single positive level sits between −7% and −65% at
its immediate neighbours — an isolated point in a noisy series, not a signal.
Stage 1 fails. Do not build the bot.

The full table, net of Kalshi fees:

| price | side | taker win | taker net | maker win | maker net |
|---|---|---:|---:|---:|---:|
| 1¢ | NO | 1.13% | **+6.1%** | 1.41% | +39.3% |
| 1¢ | YES | 0.49% | −57.9% | 0.72% | −29.7% |
| 2¢ | NO | 1.99% | −7.4% | 1.71% | −16.2% |
| 3¢ | NO | 1.26% | −64.8% | 3.10% | +1.6% |
| 5¢ | NO | 3.34% | −39.9% | 3.93% | −23.1% |
| 10¢ | NO | 8.10% | −25.3% | 8.83% | −13.3% |

Makers beat takers in 19 of 20 cheap-tail cells. Role dominates side, as
Finding 2 predicted.

### How it was computed

The 36GiB trade dataset is unreachable from this environment (org egress policy
blocks `s3.jbecker.dev`; the Kalshi API has since been opened and is what
Stage 2 records from), but the paper publishes the
computed output behind every figure in
[Jon-Becker/research](https://github.com/Jon-Becker/research), MIT licensed. Two
of those series are enough.

`maker_win_rate_by_direction.json` gives maker win rate by side and cost basis.
The taker side then follows exactly, with no estimation: every print has one
maker leg and one taker leg on opposite sides at complementary prices, so

```
taker_winrate(NO,  p) = 1 − maker_winrate(YES, 100−p)
taker_winrate(YES, p) = 1 − maker_winrate(NO,  100−p)
```

**Independent validation.** The paper also ships
`yes_no_asymmetry_significance.csv`, which carries its p-values and turns out to
be the taker-side series. Our taker cells come from a *different* file, so the
agreement is corroboration rather than restatement: across all 19 shared price
levels the median gap is **0.01pp** and the worst is 0.46pp.

```bash
python published_cells.py --fig vendor/becker-fig
```

### Caveats that keep this from being final

- **Pooled 2021–2025.** These aggregates blend the pre-2024 regime (takers
  winning) with the post-election one. See Finding 4.
- **No clustering possible.** The published series give no market or event
  counts, so the intervals in Finding 5 cannot be computed from them. Given the
  71pp spread across adjacent price levels, honest intervals would be wide
  enough to contain nearly every cell.
- Overturning this verdict needs the full dataset and `replicate.py`, which does
  the by-year and event-clustered work these aggregates cannot support.

### An inconsistency worth knowing about

Two of the paper's own output files disagree about the flagship statistic. For
1¢ NO, `longshot_ev_asymmetry.json` (the pooled series, and the source of the
published "+23%") reports **+22.79%**, while `yes_no_asymmetry_significance.csv`
reports **+13.46%** — a 9.3pp gap on the same cell, with the p-values attached to
the lower one. A derived taker blend also fails to reconcile with
`mispricing_by_price.json` (−27.8% vs −57.5% at 1¢). Anyone building on these
numbers should reconcile them against the raw data first.

## Finding 1 — the YES and NO curves are one fact, not two

The headline framing is "+23% for 1¢ NO vs −41% for 1¢ YES, a 64-point gap."

The paper defines Cost Basis so that "a NO trade at 5 cents" means the NO leg of
a print, and Becker's
[`ev_yes_vs_no.py`](https://github.com/Jon-Becker/prediction-market-analysis/blob/main/src/analysis/kalshi/ev_yes_vs_no.py)
computes `EV = 100 × win_rate − price` with no `taker_side` filter — every print
contributes its YES leg at `yes_price` *and* its NO leg at `no_price`. Since
`no_price ≡ 100 − yes_price`, that forces an identity:

```
cents:   EV_yes(P) + EV_no(100−P) = 0
returns: EV_no(100−P) = −EV_yes(P) × P/(100−P)
```

Verified to machine precision against the repo's own SQL, and pinned in
`test_pooled_yes_and_no_curves_are_an_exact_reflection`.

Running the two headline numbers backwards through it recovers the underlying
win rates exactly:

| yes_price | W(yes) | EV_yes | EV_no |
|---|---:|---:|---:|
| 1¢ | 0.59% | **−41%** | +0.41% |
| 99¢ | 98.77% | −0.23% | **+23%** |

So the two cited numbers come from **two different price points and two
different populations of markets**. "1¢ NO is +23%" is the same fact as "99¢ YES
is −0.23%". The 64-point gap is not a like-for-like comparison at matched cost
basis, and it is not an arbitrage.

The real content is one directional bias: YES was overpriced at *both* ends. That
is what "NO beat YES at 69 of 99 price levels" describes.

## Finding 2 — role, not side, is where the money is

The paper reports the decomposition that actually matters (§ *Decomposing Returns
by Role*): at 1¢, **takers win 0.43%** against 1% implied, while **makers on the
same contracts win 1.57%**.

| at 1¢ cost basis | win rate | gross | net of fees |
|---|---:|---:|---:|
| Taker | 0.43% | −57.0% | **−63.9%** |
| Maker | 1.57% | +57.0% | **+55.3%** |

That is a ~114pp role gap, against the 64pp YES/NO gap in the headline. It means
a bot that **crosses the spread to buy NO at 1¢ loses roughly 64% of stake,
regardless of which side it takes.**

The paper is explicit that direction is not the driver: makers buying NO beat
makers buying YES by only 0.47pp, with Cohen's d ≈ 0.02–0.03, and it concludes
"makers do not profit by knowing which way to bet." The tail edge is a liquidity
provision premium, not a NO premium.

One caveat that keeps Stage 1 alive: the 0.43% taker figure pools YES and NO at
1¢ cost basis, and the paper shows takers at 1–10¢ are disproportionately buying
YES (41–47% of YES volume vs 20–24% for makers). So a taker buying *specifically*
NO might do better than −57%. **The paper never publishes the role × side × price
cross-tab.** That cell is the whole remaining question, and it is what
`replicate.py` computes.

## Finding 3 — all published figures are gross of fees

The paper defines excess return as "gross of platform fees." Kalshi charges
`ceil_to_cent(0.07 × C × P × (1−P))`, makers 25% of that. Per contract, as a
share of capital at risk, this reduces to:

```
fee / stake = 0.07 × (1 − P)
```

~6.93% of stake at 1¢, ~5.6% at 20¢, ~3.5% at 50¢; charged one way only when held
to resolution. At the tail the fee is roughly the size of the entire pooled
effect, which is why it is applied to every number in the table above.

## Finding 4 — the sample spans two opposite regimes

The paper reports that from launch through 2023, **takers earned +2.0% and makers
−2.0%** — the reverse of the headline finding. The sign flipped in 2024 Q2 and
widened after the election, as volume drew professional makers in.

A full-sample 2021–2025 average therefore blends two opposing regimes. Only the
post-2024 slice describes the market a bot would trade in today, and the paper's
own causal story — professional liquidity providers arriving — implies continued
compression. This is why `--by year` is not optional.

## Finding 5 — the tail's margin is thinner than 72M trades suggests

Monte Carlo, assuming the pooled +23% figure is exactly true (1.230% resolution
rate against a 1.069% fee-inclusive breakeven — a 0.161pp margin):

| independent markets | median net EV | 5th pct | 95th pct | P(profitable) |
|---|---:|---:|---:|---:|
| 1,000 | +13.1% | −36.9% | +73.1% | 68.4% |
| 5,000 | +15.1% | −8.9% | +43.1% | 84.6% |
| 20,000 | +16.1% | +3.6% | +29.1% | 98.3% |

~20,000 **independent** resolved markets are needed before the interval clears
zero. The published figures are volume-weighted over trades, and 1¢ markets
cluster hard — every strike on one election, every alternate line in one game. At
correlated blocks of 10, the CI on a 20,000-market sample is **[−32%, +64%]**.

The paper does not report clustered intervals; its CIs (e.g. [−1.13%, −1.11%])
treat trades as independent. This is why the harness bootstraps over
`event_ticker`.

## What the harness does differently

`replicate.py` recomputes the same quantity with four changes:

1. **Splits legs by role *and* side** — the cross-tab the paper never publishes,
   and the only view in which a strategy is well defined.
2. **Subtracts fees** (`fees.py`).
3. **Stratifies by year and series**, to separate the pre- and post-2024 regimes.
4. **Clusters CIs by event**, and reports market-weighted alongside
   volume-weighted rates.

It prints `n_events` per bucket, so thin buckets are visible rather than hidden
behind a large trade count.

## Running it

```bash
pip install duckdb pandas numpy pyarrow pytest

# one-time; needs ~100GB free and a machine that can reach s3.jbecker.dev
git clone https://github.com/Jon-Becker/prediction-market-analysis
cd prediction-market-analysis && make setup

python replicate.py --data /path/to/prediction-market-analysis/data/kalshi \
                    --max-price 5 --by year --side no --role taker
```

The harness applies the paper's $100 minimum notional filter by default
(`--min-notional`, set to 0 to disable).

```bash
python -m pytest test_replicate.py -q     # 15 tests, no dataset needed
```

## Go / no-go

Answered above: **no-go.** The original question ("does cheap NO beat cheap YES")
compares two different market populations and is not actionable. The narrower
question — is there a taker-reachable cell that clears fees — now has an answer,
and it is negative at 9 of 10 cheap price levels.

Capturing the optimism tax means *making* markets, not betting NO, with adverse
selection, inventory and uptime risk that the published aggregates cannot price.
That is Stage 2. Two obstacles flagged here are now confirmed on the live tape:
most cheap-side fills are expiry sweeps of markets whose result is already known
(92% of cheap-side maker volume in the first hour), and some markets now quote
in sub-cent ticks, so the 1¢ level is no longer always the bottom of the book.

## Stage 2 — the maker question: an order-book tape

Becker's maker edge at 1¢ (+55% net of fees) is an average over fills that
happened. A new maker also has to wait behind the queue already resting at 1¢,
and the fills it does get may be the bad ones. Neither can be read off trade
aggregates, so Stage 2 records the book.

### What is recorded

`tape.py` polls Kalshi's public REST API (no API key needed):

- **trades:** every print on the exchange, about 30–90 per second.
- **books:** full-depth snapshots of 40 cheap-tail tickers (last print ≤10¢ or
  ≥90¢) and 10 mid-priced controls, each refreshed about every 6 seconds. The set
  is re-ranked every minute by recent print count.
- **markets, events, status:** metadata, event category, and every status
  change, so the analysis can tell when a market actually stopped trading.

There are more than 30,000 open markets, so the tickers are chosen from the
trade tape rather than by scanning the market list. A ticker is only eligible
while it is outside its own sweep window (below) and hasn't been seen to stop.
Before that rule, about 80% of tail book snapshots went to markets about to
expire. Output is gzipped JSONL per UTC hour under `data/tape/` (gitignored,
about 25 MB/hour). A restarted recorder writes a new numbered part instead of
appending to a file a killed process left open.

```bash
./run_tape.sh                       # records to data/tape/, restarts tape.py if it exits
python tail_depth.py                # cheap-side depth and 1¢ queue wait, sweeps excluded
python tail_depth.py --keep-sweeps  # the raw numbers
```

### Expiry sweeps

When a result is effectively known, takers buy the winning side at 99¢ and fill
every cheap bid in the book. That volume is not a queue a maker can profitably
join, so `tail_depth.py` excludes fills and book snapshots within 15 minutes of a
market's effective close, or 60 minutes for sports. The effective close is the
scheduled close, or the last print before the recorder saw trading stop,
whichever is earlier.

- **Non-sports:** in markets open an hour or less, 99.8% of ≤1¢ fills landed in
  the last 10 minutes, and none fell 15–60 minutes out.
- **Sports:** these can be decided in play. In the full trade histories of 15
  sports markets that finished during the tape, 13 had all their 1¢ fills in the
  last 15 minutes. Two tennis matches had 67% and 81% of theirs 15–60 minutes
  out, and 60 minutes covers every 1¢ fill in 14 of the 15.
- **Unresolved:** a sweep only becomes recognisable once trading stops. A ticker
  still trading at the end of the tape therefore loses its last window from the
  analysis. A match that is decided but still trading, like a one-day cricket
  match at 93–99¢ for two hours, is not caught until it stops, and 60 minutes may
  be too short for cricket.

### Overnight tape (2026-09-23 09:12 to 09-24 07:30 UTC)

The tape covers 22.3 hours of book snapshots, minus three gaps (listed under
Limits). Kalshi's regular Thursday closure, 07:00–09:00 UTC, ends the window.
2,067 tickers passed through the tail universe. After sweeps are excluded, 539
have usable books (257 of them sports), and 394 were recorded long enough to
give a fill rate.

**Sweeps are most of the volume.** The filter removed 79% of cheap-side maker
volume, and 85% at 1¢ (9.5M of 11.2M contracts). With a 15-minute window for
sports it would remove 48% and 67%. Choosing tickers outside their sweep window
cut the share of snapshots spent on sweeps from 82% to 55%. Almost all of the
rest (95%) are sports markets that ended early, which the recorder can't see
coming.

**Depth** (median across the 539 tickers, with p10–p90 in brackets):

| | all | closing within 24 h (252) | closing later (287) |
|---|---|---|---|
| best cheap-side bid | 6¢ | 4¢ | 7¢ |
| contracts at that bid | 897 (32–11k) | 730 | 2,112 |
| depth at ≤10¢ | $328 (p90 $3.1k) | $184 | $594 |
| price levels at ≤10¢ | 5 | 4 | 6 |
| spread | 1¢, one tick (p90 4¢) | 1¢ | 1¢ |

Sub-cent ticks are rare at the best bid. The exceptions are resting walls: one
long-dated market with ~894k contracts at 0.1¢, and several multivariate combo
markets quoting at 0.01–0.4¢.

**The 1¢ queue** (wait = resting contracts ÷ maker fills per hour):

| group | tickers | wait | largest single ticker's share of fills |
|---|---:|---:|---|
| closing within 24 h | 200 | 1.8 h | 7% |
| closing after 24 h | 189 | 19.3 h | 38%, a cricket match |
| sports | 177 | 4.9 h | 46%, the same cricket match |
| non-sports | 212 | 24.8 h | 27%, a multivariate combo |

- **Closing within 24 hours** is the only well-spread estimate. It is mostly
  hourly BTC range markets: about 1.8 hours at 1¢ and 1.0 hour at 2¢. With
  sweeps kept, it reads 1.2 hours, with 43% of fills from one expiring
  15-minute BTC market.
- **Sports is overstated.** The 60-minute window is too short for cricket. The
  India under-19 one-day match stopped trading at 10:27 and settled YES, but
  194k of its contracts traded at 94–99¢ one to three hours before the stop,
  against 14k inside the last hour.
- **Non-sports at 1¢** is dominated by resting sub-cent walls in long-dated
  markets and multivariate combos. Those are parlay-style markets that probably
  belong in a separate group.
- **From 2¢ to 10¢** the wait is under about 3.6 hours in every group, and
  under 2 hours in most.

**What this doesn't show.** A short queue says a quote would get filled, not
that the fill is worth having. Whether non-sweep 1¢ fills win often enough to
beat the fee is the adverse-selection question. The status stream records each
market's result, so that is the next thing to measure.

### Do cheap maker fills win?

`fill_outcomes.py` scores every maker fill at ≤10¢ on the trade tape: 134M
contracts across all markets, not only the sampled books. Once a market
settles, the fill wins if the result matches the maker's side. Net return on
stake is after the unrounded maker fee. Intervals are 95% confidence intervals,
resampled by event. For settled markets the API reports the moment trading
actually stopped as `close_time`, so sweeps are timed from the real end,
including matches that ended early. 6% of the volume is in markets not yet
settled and is left out.

1¢ fills (price ≤1¢), with sweeps split out at 15 minutes before close (60 for
sports), 2026-09-23 08:40 to 09-24 09:52 UTC:

| | win rate | needed to break even | net return | 95% CI | events |
|---|---:|---:|---:|---|---:|
| sweeps, all markets | 0.11% | 0.51% | −79% | [−89%, −66%] | 3,910 |
| crypto (hourly BTC/ETH), 15–60 min before close | 0.08% | 1.02% | **−94%** | [−102%, −79%] | 172 |
| multivariate combos (sub-cent) | 0.04% | 0.61% | **−95%** | [−102%, −81%] | 944 |
| other (index, approval, weather) | 1.31% | 1.02% | +29% | [−94%, +141%] | 169 |
| sports | 2.49% | 1.01% | +148% | [−56%, +599%] | 403 |

For comparison, Becker's 2021–25 figure for makers at 1¢ is a 1.57% win rate.

- **Crypto and combos are adversely selected.** 1¢ makers in hourly crypto
  markets win 0.08% against a 1.02% breakeven. With a 60-minute window only 6
  crypto events remain outside it, so this result is about quoting in the last
  hour of an hourly market. Combo makers at sub-cent prices almost never win.
- **Sports is the only positive point estimate, and it is in-play.** With a
  180-minute sports window, only 41 sports events are left outside it: nearly
  every cheap sports fill happens during the match. At 1¢ the interval includes
  zero. Only a few cells elsewhere clear zero (sports 8¢ [+55%, +519%]; sports
  2¢ with a 180-minute cricket window [+7%, +624%]), and across 10 price levels,
  5 groups and 4 window settings a few such cells are expected by chance.
- **So there is no established 1¢ maker edge in any group.** One day gives
  about 400 sports events. A result that clears zero at 1¢ would take roughly a
  week of tape if the true edge is as large as the point estimate.

What this still doesn't capture:

- **Queue position.** These are every maker fill, so the win rate is the
  average across all queue positions. A new quote joins the back of the queue,
  and it only fills when a large order clears the whole level, which is when
  the taker is most likely to be informed. The last place in the queue is
  probably worse than this average. Measuring that needs order-by-order data.
- **Fee rounding.** Kalshi rounds the fee up to the cent per order. On a
  10-contract order at 1¢ that is $0.01 on $0.10 of stake, 10% rather than
  0.02%. The table uses the unrounded fee.
- **Which markets have settled.** Scoring needs a result, so long-dated markets
  are under-represented.

### Limits

- **Snapshots, not order-by-order changes.** A snapshot every ~6 seconds shows
  depth at each price and where the book stood around each print. It can't track
  a single order's place in the queue. That needs the authenticated WebSocket
  `orderbook_delta` feed and an API key.
- **The tape lives in an ephemeral cloud container.** A container recycle can
  kill the recorder, or leave it running with a stale proxy port so every request
  fails. `tape.py` exits with code 3 after about 10 minutes of failures, and
  `run_tape.sh` then stops so a process started from a fresh shell can take
  over. Gaps so far (UTC): 09-23 09:18–09:22 and 10:30–12:27, 09-24 01:22–02:34,
  and a few seconds at each deliberate restart; `data/gaps.log` has the causes.
  Kalshi's own Thursday closure, 07:00–09:00 UTC, has no trading to record.
  For a durable multi-day tape, run `./run_tape.sh` on a machine you control.

## Files

| file | |
|---|---|
| `fees.py` | Kalshi fee model, breakeven and net-return helpers |
| `replicate.py` | the harness — event-level aggregation, clustered bootstrap, CLI |
| `synthetic.py` | Kalshi-schema generator with a known injected edge |
| `published_cells.py` | derives role × side × price from the paper's published output |
| `vendor/becker-fig/` | the five MIT-licensed source series, vendored for reproducibility |
| `test_replicate.py` | 15 tests, including the mirror identity and the paper's role split |
| `test_published_cells.py` | 8 tests for the derivation and its validation |
| `tape.py` | Stage 2 recorder: trade tape, book snapshots, status, metadata |
| `run_tape.sh` | keeps `tape.py` running; restarts it if it exits |
| `tail_depth.py` | cheap-side depth, spreads and 1¢ queue wait over the tape, sweeps excluded |
| `test_tape.py` | 9 tests: ticker selection, the sweep-window rule, restart-safe output, exit on outage |
| `test_tail_depth.py` | 7 tests: effective close, sweep windows by category, cheap-side stats |
| `fill_outcomes.py` | scores cheap maker fills against settled results, by group, with event-clustered CIs |
| `test_fill_outcomes.py` | 6 tests: maker leg, net return and breakeven, groups, event bootstrap |
