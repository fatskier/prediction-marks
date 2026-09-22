# Kalshi "optimism tax" — Stage 1 replication

Testing whether Becker's *Microstructure of Wealth Transfer in Prediction Markets*
(2026-01-18) supports an executable "buy cheap NO" strategy, before any trading
bot is built.

## Result: no

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
blocks `s3.jbecker.dev` and all Kalshi hosts), but the paper publishes the
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

Capturing the optimism tax means *making* markets, not betting NO: a different
project, with adverse selection, inventory and uptime risk, worth deciding on its
own merits rather than as a continuation of this one. Two obstacles would still
apply: the 1¢ tick is the whole edge (paying 2¢ doubles cost basis), and a large
share of resting 1¢ offers are in-game markets minutes from resolution where 1¢
is correctly priced.

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
