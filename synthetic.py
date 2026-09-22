"""Generate Kalshi-schema parquet with a known, injected edge.

The real dataset is a 36GiB download and cannot live in every environment, so
the harness is verified against data whose true answer we control. A market
priced at `yes_price` P cents is made to resolve YES with probability
(P - bias_pp)/100, i.e. YES is overpriced by `bias_pp` percentage points at
every price level -- the "optimism tax" in its purest form.

At P = 99 that makes the 1c NO side resolve at (1 + bias_pp)/100, so
bias_pp = 0.23 reproduces the headline +23% gross claim exactly, and
bias_pp = 0.0693 sits precisely at the taker-fee breakeven.

Markets inside an event share an outcome shock, controlled by `rho`, because
that correlation is what destroys the effective sample size in the real data.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def generate(
    out_dir: Path,
    n_events: int = 4000,
    markets_per_event: int = 5,
    bias_pp: float = 0.23,
    rho: float = 0.0,
    prices: tuple[int, ...] = (99,),
    trades_per_market: int = 3,
    year_span: tuple[int, int] = (2021, 2025),
    seed: int = 0,
) -> Path:
    """Write markets/ and trades/ parquet under `out_dir`; return that dir."""
    rng = np.random.default_rng(seed)
    n = n_events * markets_per_event

    event_ticker = np.repeat([f"EV{e:06d}" for e in range(n_events)], markets_per_event)
    ticker = np.array([f"{ev}-M{i % markets_per_event}" for i, ev in enumerate(event_ticker)])
    yes_price = rng.choice(prices, size=n)

    # Outcome draw. Each market either adopts its event's shared uniform (with
    # probability rho) or draws its own. A mixture of two uniforms is still
    # uniform, so this induces intra-event correlation while leaving the
    # marginal calibration of every market exactly intact -- blending the two
    # draws arithmetically would not, since the sum of uniforms is triangular.
    event_shock = np.repeat(rng.random(n_events), markets_per_event)
    idio = rng.random(n)
    u = np.where(rng.random(n) < rho, event_shock, idio)

    p_yes = np.clip((yes_price - bias_pp) / 100.0, 1e-6, 1 - 1e-6)
    result = np.where(u < p_yes, "yes", "no")

    markets = pd.DataFrame({
        "ticker": ticker,
        "event_ticker": event_ticker,
        "market_type": "binary",
        "title": ticker,
        "status": "finalized",
        "result": result,
        "volume": trades_per_market * 100,
    })

    reps = trades_per_market
    lo, hi = year_span
    years = rng.integers(lo, hi + 1, size=n * reps)
    trades = pd.DataFrame({
        "trade_id": [f"T{i}" for i in range(n * reps)],
        "ticker": np.repeat(ticker, reps),
        "count": rng.integers(1, 200, size=n * reps),
        "yes_price": np.repeat(yes_price, reps),
        "no_price": 100 - np.repeat(yes_price, reps),
        # Takers lean YES at the cheap end -- the behavioural premise under test.
        "taker_side": rng.choice(["yes", "no"], size=n * reps, p=[0.6, 0.4]),
        "created_time": pd.to_datetime([f"{y}-06-15" for y in years]),
    })

    (out_dir / "markets").mkdir(parents=True, exist_ok=True)
    (out_dir / "trades").mkdir(parents=True, exist_ok=True)
    markets.to_parquet(out_dir / "markets" / "m.parquet")
    trades.to_parquet(out_dir / "trades" / "t.parquet")
    return out_dir
