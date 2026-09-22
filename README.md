# Kalshi "optimism tax" — Stage 1 replication

Testing whether Becker's *Microstructure of Wealth Transfer in Prediction Markets*
finding (cheap NO contracts historically outperformed equally cheap YES ones)
survives as something a bot could execute.

**Stage 1 is a kill gate.** Nothing gets built until the edge clears fees, in
recent data, with an honest confidence interval. The findings below came from
reading the published analysis code, not from running it — the dataset is a
36GiB download that has to land on a real machine.

## Finding 1 — the YES and NO curves are one fact, not two

The headline framing is "+23% for 1¢ NO vs −41% for 1¢ YES, a 64-point gap."
Becker's [`ev_yes_vs_no.py`](https://github.com/Jon-Becker/prediction-market-analysis/blob/main/src/analysis/kalshi/ev_yes_vs_no.py)
computes `EV = 100 × win_rate − price` and, as its own docstring says, includes
**both maker and taker sides of all trades**. Its SQL has no `taker_side` filter:
every print contributes its YES leg at `yes_price` *and* its NO leg at
`no_price`.

Since `no_price ≡ 100 − yes_price`, this forces an identity:

```
EV_yes(P) + EV_no(100−P) = 0      for every P
```

Verified to machine precision against the repo's own SQL (`max |residual| =
0.000000000000` across all 99 price levels), and pinned in
`test_pooled_yes_and_no_curves_are_an_exact_reflection`.

Two consequences:

- "1¢ NO is +23%" is the *same statement* as "99¢ YES is −23%". It is not
  independent corroboration of the 1¢ YES result.
- The 64-point gap compares **two different populations of markets**. 1¢ NO is
  the cheap leg of markets trading at 99¢ YES; 1¢ YES is the cheap leg of
  markets trading at 1¢ YES. They share a price tag, not a subject. It is not a
  like-for-like comparison at matched cost basis, and it is not an arbitrage.

The real empirical content is simpler and still interesting: YES was overpriced
at *both* ends — markets at 1¢ YES resolved YES ~0.59% of the time, markets at
99¢ YES resolved YES ~98.77%. That is one directional bias, which is what "NO
beat YES at 69 of 99 price levels" actually describes.

## Finding 2 — fee drag is worst exactly where the edge is claimed

Kalshi charges `ceil_to_cent(0.07 × C × P × (1−P))`, makers 25% of that. Per
contract, as a share of the capital actually at risk:

```
fee / stake = 0.07 × (1 − P)
```

So ~6.93% of stake at 1¢, ~5.6% at 20¢, ~3.5% at 50¢. Held to resolution, it is
charged one way only.

| | |
|---|---:|
| Implied true P(NO) behind the +23% claim | 1.230% |
| Breakeven incl. taker fee | 1.069% |
| Net EV, taker | **+16.1%** |
| Net EV, maker | +21.3% |
| **Margin of safety** | **0.161 pp** |

Still positive. But the entire strategy lives inside 0.16 percentage points of
resolution rate.

## Finding 3 — that margin is far thinner than 72M trades suggests

Monte Carlo, assuming the claimed edge is exactly true:

| independent markets | median net EV | 5th pct | 95th pct | P(profitable) |
|---|---:|---:|---:|---:|
| 200 | −6.9% | −106.9% | +143.1% | 44.3% |
| 1,000 | +13.1% | −36.9% | +73.1% | 68.4% |
| 5,000 | +15.1% | −8.9% | +43.1% | 84.6% |
| 20,000 | +16.1% | +3.6% | +29.1% | 98.3% |

~20,000 **independent** resolved markets at that price level are needed before
the interval clears zero. The published figure is volume-weighted over trades,
and 1¢ markets cluster hard — every strike on one election, every alternate line
in one game. If markets arrive in correlated blocks of 10, the 95% CI on a
20,000-market sample is **[−32%, +64%]**.

This is why the harness bootstraps over `event_ticker` rather than trades.

## What the harness does differently

`replicate.py` recomputes the same quantity with four changes:

1. **Splits legs by role.** Only one side of a print is reachable by a strategy.
2. **Subtracts fees** (`fees.py`).
3. **Stratifies by year and series.** The paper's own story — professional makers
   arriving — implies decay, so the 2025 slice is what you'd earn, not the
   2021–2025 average.
4. **Clusters CIs by event**, and reports market-weighted alongside
   volume-weighted rates.

It also prints `n_events` per bucket, so thin buckets are visible rather than
hidden behind a large trade count.

## Running it

```bash
pip install duckdb pandas numpy pyarrow pytest

# one-time, needs ~100GB free and a machine that can reach s3.jbecker.dev
git clone https://github.com/Jon-Becker/prediction-market-analysis
cd prediction-market-analysis && make setup

python replicate.py --data /path/to/prediction-market-analysis/data/kalshi \
                    --max-price 5 --by year --side no --role taker
```

`--by series` splits by market family — the paper reports a maker/taker gap of
0.17pp in finance vs 2.23pp in sports and >7pp in media/world events, so any
surviving edge should concentrate away from finance. If it doesn't, that is
evidence the effect is noise rather than the behavioural story claimed.

```bash
python -m pytest test_replicate.py -q     # 13 tests, no dataset needed
```

## Go / no-go

Build Stage 2 only if, on **2024–2025 data**, taker-side cheap NO shows a net
return whose event-clustered 95% CI excludes zero, in more than one series.

Anything weaker means the bot would be trading a sampling artifact into a 6.9%
fee — and the two live obstacles Stage 2 would have to solve are still untested:
that the 1¢ tick is the whole edge (paying 2¢ doubles the cost basis and erases
it), and that a large share of resting 1¢ NO offers are in-game markets minutes
from resolution where 1¢ is *correctly* priced.

## Files

| file | |
|---|---|
| `fees.py` | Kalshi fee model, breakeven and net-return helpers |
| `replicate.py` | the harness — event-level aggregation, clustered bootstrap, CLI |
| `synthetic.py` | Kalshi-schema generator with a known injected edge |
| `test_replicate.py` | 13 tests, including the mirror identity |
