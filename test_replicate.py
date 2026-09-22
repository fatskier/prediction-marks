"""Tests for the fee model and the replication harness."""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

import numpy as np
import pytest

import fees
import replicate
import synthetic


# ---------------------------------------------------------------- fee model

def test_fee_fraction_of_stake_closed_form():
    """fee/stake collapses to 0.07*(1-P); it is near-maximal at the cheap tail."""
    for p in (0.01, 0.05, 0.2, 0.5, 0.9):
        assert fees.fee_fraction_of_stake(p) == pytest.approx(0.07 * (1 - p))
    assert fees.fee_fraction_of_stake(0.01) == pytest.approx(0.0693)


def test_maker_fee_is_a_quarter_of_taker():
    assert fees.fee_per_contract(0.3, maker=True) == pytest.approx(
        0.25 * fees.fee_per_contract(0.3)
    )


def test_fee_peaks_at_fifty_cents_per_contract():
    per_ct = [fees.fee_per_contract(p / 100) for p in range(1, 100)]
    assert max(range(len(per_ct)), key=per_ct.__getitem__) + 1 == 50


def test_order_fee_rounds_up_to_the_cent():
    assert fees.order_fee(0.01, 1) == 0.01          # ceiling dominates tiny orders
    assert fees.order_fee(0.01, 500) == pytest.approx(0.35)


def test_headline_claim_net_of_fees():
    """+23% gross at 1c NO is +16.1% net, on a 0.161pp margin of safety."""
    assert fees.net_return_pct(1, 0.0123) == pytest.approx(16.07, abs=0.05)
    assert fees.breakeven_win_rate(0.01) == pytest.approx(0.010693)
    assert (0.0123 - fees.breakeven_win_rate(0.01)) == pytest.approx(0.00161, abs=1e-5)


def test_breakeven_is_lower_for_makers():
    assert fees.breakeven_win_rate(0.01, maker=True) < fees.breakeven_win_rate(0.01)


# ------------------------------------------------- the mirror-image identity

def test_pooled_yes_and_no_curves_are_an_exact_reflection():
    """Becker's pooled EV formulation satisfies EV_yes(P) = -EV_no(100-P).

    Both legs of every print are counted, and no_price == 100 - yes_price, so
    the two curves are one fact reflected -- not independent evidence.
    """
    rng = np.random.default_rng(3)
    yes_price = rng.integers(1, 100, 20_000)
    result_yes = rng.random(20_000) < (yes_price / 100 - 0.004)

    for p in (1, 7, 23, 60, 99):
        # One print at yes_price = p IS a print at no_price = 100 - p. Both
        # curves are fed by this single subset, from opposite ends.
        subset = result_yes[yes_price == p]
        if len(subset) == 0:
            continue
        ev_yes = 100 * subset.mean() - p
        ev_no = 100 * (~subset).mean() - (100 - p)
        assert ev_yes + ev_no == pytest.approx(0.0, abs=1e-9)


# ------------------------------------------------------------- the harness

def _run(**kw):
    kw.setdefault("trades_per_market", 1)
    with tempfile.TemporaryDirectory() as tmp:
        data = synthetic.generate(Path(tmp), **kw)
        events = replicate.load_event_level(data, 1, 10)
        return replicate.summarise(events, [], n_boot=400)


def _cheap_no(summary, role="taker"):
    row = summary[(summary.price == 1) & (summary.side == "no") & (summary.role == role)]
    assert len(row) == 1
    return row.iloc[0]


# A 1c bucket carries ~1% base rates, so a realization only pins the underlying
# edge down with a very large number of independent markets -- 100k here gives a
# standard error of roughly 3.5pp on the net return. That requirement is itself
# the central finding, so the tests are sized to respect it rather than dodge it.
BIG = dict(n_events=100_000, markets_per_event=1)


def test_harness_recovers_an_injected_edge():
    """With the claimed 0.23pp bias and ample independent events, 1c NO is +16%."""
    row = _cheap_no(_run(bias_pp=0.23, rho=0.0, **BIG))
    assert row.net_return_pct == pytest.approx(16.1, abs=8.0)
    assert row.gross_return_pct == pytest.approx(23.0, abs=8.0)
    # The fee wedge is exactly 0.07*(1-P) of stake, whatever the realization.
    assert row.gross_return_pct - row.net_return_pct == pytest.approx(6.93, abs=0.01)


def test_edge_below_the_fee_threshold_is_rejected():
    """A gross-positive edge under the 0.0693pp fee hurdle must come out net-negative.

    Fed in directly rather than sampled: at a 1c base rate, a true -3.9% net
    return sits about 0.8 sampling standard errors from zero even with 100k
    simulated markets, so a drawn sample cannot assert the sign. That fragility
    is the headline finding, not something to engineer around in a test.
    """
    import pandas as pd
    # 1.03% realized resolution rate vs a 1.0693% breakeven.
    n_ev = 600
    frame = pd.DataFrame({
        "event_ticker": [f"E{i}" for i in range(n_ev)],
        "series": "S", "yr": 2025, "role": "taker", "side": "no", "price": 1,
        "contracts": 10_000.0,
        "won_contracts": [100.0 if i % 2 else 106.0 for i in range(n_ev)],
        "n_markets": 10_000, "n_markets_won": [100 if i % 2 else 106 for i in range(n_ev)],
    })
    row = replicate.summarise(frame, [], n_boot=400).iloc[0]

    assert row.win_rate_vw == pytest.approx(0.0103)
    assert row.gross_return_pct == pytest.approx(3.0, abs=0.01)   # looks like +3%
    assert row.net_return_pct == pytest.approx(-3.93, abs=0.01)   # is actually -3.9%
    assert not row.significant


def test_no_edge_means_no_significance():
    """A perfectly calibrated market loses exactly the fee: -6.93% of stake."""
    row = _cheap_no(_run(bias_pp=0.0, rho=0.0, **BIG))
    assert row.net_return_pct == pytest.approx(-6.93, abs=8.0)
    assert not row.significant


def test_event_correlation_widens_the_interval():
    """Clustering is the point: correlated markets must not count as independent."""
    common = dict(n_events=3000, markets_per_event=20, bias_pp=0.23)
    indep = _cheap_no(_run(rho=0.0, **common))
    clustered = _cheap_no(_run(rho=0.95, **common))

    assert (clustered.ci_hi - clustered.ci_lo) > 1.5 * (indep.ci_hi - indep.ci_lo)
    # Correlation must widen the interval without biasing the point estimate.
    assert clustered.win_rate_vw == pytest.approx(indep.win_rate_vw, abs=0.004)


def test_roles_are_opposite_sides_of_the_same_print():
    """At 1c NO, the taker rows are the `taker_side='no'` prints and the maker
    rows are the `taker_side='yes'` prints -- disjoint populations, which is
    exactly what pooling them (as the published analysis does) obscures."""
    summary = _run(n_events=20_000, markets_per_event=1, bias_pp=0.23)
    taker, maker = _cheap_no(summary, "taker"), _cheap_no(summary, "maker")
    assert taker.contracts > 0 and maker.contracts > 0
    # Generator puts 60% of takers on YES, so the maker side of 1c NO is larger.
    assert maker.contracts > taker.contracts
    # Same markets underneath, so the resolution rate must agree; only fees differ.
    assert maker.win_rate_vw == pytest.approx(taker.win_rate_vw, abs=0.004)
    assert maker.net_return_pct > taker.net_return_pct


def test_bootstrap_needs_more_than_one_event():
    import pandas as pd
    frame = pd.DataFrame({
        "event_ticker": ["E1"], "contracts": [100.0], "won_contracts": [2.0],
    })
    lo, hi = replicate.bootstrap_ci(frame, 1, False, 100, np.random.default_rng(0))
    assert math.isnan(lo) and math.isnan(hi)
