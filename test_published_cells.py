"""Tests for the role x side x price derivation from Becker's published output."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

import fees
import published_cells as pc

FIG = Path(__file__).parent / "vendor" / "becker-fig"


@pytest.fixture(scope="module")
def cells():
    return {(c["role"], c["side"], c["price"]): c
            for c in pc.build_cells(pc.maker_table(FIG))}


def test_derivation_matches_the_papers_own_significance_file(cells):
    """The independent check that the complement derivation is right.

    `yes_no_asymmetry_significance.csv` carries the paper's p-values and turns
    out to be the taker-side series. Our taker cells are derived from a
    different file (maker win rates at the complementary price), so agreement
    between them is genuine corroboration rather than a restatement.
    """
    published = {int(r["price"]): float(r["no_ev"])
                 for r in csv.DictReader((FIG / "yes_no_asymmetry_significance.csv").open())}
    assert len(published) == 19          # the file is a subset of price levels

    gaps = []
    for price, pub in published.items():
        derived = cells[("taker", "no", price)]["gross_pct"]
        assert derived == pytest.approx(pub, abs=0.6), price
        gaps.append(abs(derived - pub))
    gaps.sort()
    assert gaps[len(gaps) // 2] < 0.05   # median agreement to 0.01pp


def test_taker_no_is_negative_almost_everywhere_in_the_cheap_tail(cells):
    """The answer to the Stage 1 question: no stable taker-reachable edge."""
    net = [cells[("taker", "no", p)]["net_pct"] for p in range(1, 11)]
    assert sum(v > 0 for v in net) == 1          # only 1c, and only just
    assert net[0] == pytest.approx(6.1, abs=1.0)
    assert sum(net) / len(net) < -25.0
    assert max(net) - min(net) > 60.0            # 71pp spread: noise, not signal


def test_one_cent_no_is_an_isolated_outlier(cells):
    """+6% at 1c sits between -7% and -65% at its neighbours."""
    at = lambda p: cells[("taker", "no", p)]["net_pct"]
    assert at(1) > 0 > at(2) > at(3)
    assert at(1) - at(3) > 60.0


def test_maker_beats_taker_almost_everywhere_in_the_cheap_tail(cells):
    """Role dominates side across the tail -- but not unanimously.

    19 of 20 cheap-tail cells favour the maker. The exception (2c NO) is one
    more reminder that individual cells at this end are noisy.
    """
    losses = [(p, s) for p in range(1, 11) for s in ("yes", "no")
              if cells[("maker", s, p)]["net_pct"] <= cells[("taker", s, p)]["net_pct"]]
    assert losses == [(2, "no")]


def test_published_curves_are_a_near_exact_reflection():
    """Finding 1, on the paper's own numbers rather than synthetic data."""
    mi = pc.check_mirror_identity(FIG)
    assert mi["n"] == 99
    assert mi["median"] < 0.02       # on a series spanning -41%..+23%
    assert mi["max"] < 0.25
    # Not machine-exact, so state it as a near-reflection, not an identity.
    assert mi["within_rounding"] < mi["n"]


def test_headline_pair_is_one_fact_rescaled():
    rows = {int(r["price"]): r for r in pc.load_rows(FIG / "longshot_ev_asymmetry.json")}
    predicted = -rows[99]["yes_return"] * 99
    assert predicted == pytest.approx(rows[1]["no_return"], abs=0.05)
    assert rows[1]["no_return"] == pytest.approx(22.79, abs=0.01)


def test_two_published_files_disagree_on_the_headline_cell():
    """+22.79% (pooled) vs +13.46% (taker) for 1c NO -- a 9.3pp gap."""
    pooled = {int(r["price"]): r for r in pc.load_rows(FIG / "longshot_ev_asymmetry.json")}
    sig = {int(r["price"]): float(r["no_ev"])
           for r in csv.DictReader((FIG / "yes_no_asymmetry_significance.csv").open())}
    assert pooled[1]["no_return"] - sig[1] == pytest.approx(9.33, abs=0.05)


def test_fees_are_applied_with_the_right_role_rate(cells):
    for price in (1, 5, 10):
        taker, maker = cells[("taker", "no", price)], cells[("maker", "no", price)]
        wedge_t = taker["gross_pct"] - taker["net_pct"]
        wedge_m = maker["gross_pct"] - maker["net_pct"]
        assert wedge_t == pytest.approx(100 * fees.fee_fraction_of_stake(price / 100), abs=1e-6)
        assert wedge_m == pytest.approx(wedge_t * 0.25, abs=1e-6)
