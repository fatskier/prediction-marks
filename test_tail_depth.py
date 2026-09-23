from tail_depth import cent_bucket, effective_close, in_sweep, snapshot_stats


def test_effective_close_is_scheduled_without_a_stop():
    assert effective_close(1000.0, [(10.0, "active")], [5.0, 20.0]) == 1000.0


def test_effective_close_is_last_print_before_first_stop():
    # seen inactive at 300; trading actually stopped at the 250 print
    events = [(100.0, "active"), (300.0, "inactive"), (400.0, "finalized")]
    assert effective_close(5000.0, events, [50.0, 250.0, 320.0]) == 250.0
    assert effective_close(5000.0, [(300.0, "closed")], []) == 300.0


def test_effective_close_never_after_scheduled():
    assert effective_close(200.0, [(300.0, "closed")], [250.0]) == 200.0


def test_in_sweep_window():
    assert in_sweep(950.0, 1000.0, 60) and in_sweep(1100.0, 1000.0, 60)
    assert not in_sweep(900.0, 1000.0, 60) and not in_sweep(0.0, None, 60)


def test_cent_bucket_rounds_subcent_up():
    assert [cent_bucket(p) for p in (0.001, 0.01, 0.013, 0.02, 0.0999)] == [1, 1, 2, 2, 10]


def test_cheap_side_picks_the_cheap_bid():
    s = snapshot_stats({"yes": [["0.9800", "5"], ["0.9860", "114"]], "no": [["0.0100", "900"], ["0.0130", "11"]]})
    assert s["side"] == "no" and s["best"] == 0.013 and s["size_at_best"] == 11
    assert s["depth_tail"] == 911 and s["subcent_best"]
    assert abs(s["spread"] - 0.001) < 1e-9  # implied NO ask 1 - 0.986 = 0.014
