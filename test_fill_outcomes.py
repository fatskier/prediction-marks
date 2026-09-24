from fill_outcomes import bootstrap_ci, cent_bucket, group_of, maker_leg, net_return


def test_maker_is_the_side_opposite_the_taker():
    t = {"taker_side": "yes", "yes_price_dollars": "0.9900", "no_price_dollars": "0.0100"}
    assert maker_leg(t) == ("no", 0.01)
    t = {"taker_side": "no", "yes_price_dollars": "0.0300", "no_price_dollars": "0.9700"}
    assert maker_leg(t) == ("yes", 0.03)


def test_net_return_at_1c():
    # losing costs the stake plus fee; winning pays 99c on 1c
    assert net_return(0.0, 0.01) < -1.0
    assert abs(net_return(1.0, 0.01) - (0.99 - 0.25 * 0.07 * 0.01 * 0.99) / 0.01) < 1e-9


def test_breakeven_at_1c_is_just_above_1pct():
    # a 1c maker needs to win slightly more than 1% to clear the fee
    assert net_return(0.0101, 0.01) < 0 < net_return(0.0102, 0.01)


def test_groups():
    assert group_of("KXMVECROSSCATEGORY-S1-X", "Sports") == "combo"
    assert group_of("KXODIMATCH-1-IND", "Sports") == "sports"
    assert group_of("KXBTCD-1-T1", "Crypto") == "crypto"
    assert group_of("KXRAIN-1-LEX", "Climate and Weather") == "other"


def test_cent_bucket():
    assert [cent_bucket(p) for p in (0.0001, 0.01, 0.0101, 0.10)] == [1, 1, 2, 10]


def test_bootstrap_resamples_events_not_fills():
    # one event holds every win: resampling events must be able to drop it
    rows = [("E1", 100, 100, 1.0, 0.0)] + [(f"E{i}", 100, 0, 1.0, 0.0) for i in range(2, 101)]
    lo, hi = bootstrap_ci(rows, n_boot=500)
    assert lo < hi and lo <= -1.0 + 1e-9
