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


def _tape(tmp_path, category):
    """One tail market sampled 60 min, trading stopped at t=3600, 1c fills 30 and 90 min before."""
    import time
    from datetime import datetime, timezone
    from tape import Sink

    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")
    t0 = time.time() - 4 * 3600
    tk = "KXTEST-1"
    sink = Sink(str(tmp_path))
    sink.write("universe", [{"_recv": t0, "tail": [tk], "control": []}])
    sink.write("markets", [{"ticker": tk, "event_ticker": "KXTEST", "close_time": iso(t0 + 86400 * 10),
                            "status": "active", "_recv": t0}])
    sink.write("events", [{"event_ticker": "KXTEST", "category": category, "_recv": t0}])
    sink.write("status", [{"_recv": t0 + 3700, "ticker": tk, "status": "closed"}])
    book = {"yes": [["0.9800", "10"]], "no": [["0.0100", "1000"]]}
    sink.write("books", [{"_recv": t0 + m * 60, "_sent": t0 + m * 60, "ticker": tk, **book}
                         for m in range(0, 121, 5)])
    fill = lambda m, n: {"trade_id": f"{m}", "ticker": tk, "created_time": iso(t0 + 3600 - m * 60),
                         "taker_side": "yes", "yes_price_dollars": "0.9900", "no_price_dollars": "0.0100",
                         "count_fp": f"{n}", "_recv": t0 + 3600}
    sink.write("trades", [fill(90, 100), fill(30, 10), fill(0, 1)])  # last print = effective close
    sink.close()
    return str(tmp_path)


def _removed(capsys, tape, *extra):
    from tail_depth import main
    main(["--tape", tape, *extra])
    line = next(l for l in capsys.readouterr().out.splitlines() if l.startswith("Expiry sweeps excluded"))
    return line.split("That removed ")[1].split(" of ")[0]


def test_sports_markets_get_the_longer_sweep_window(tmp_path, capsys):
    # 15-min window catches only the final print; 60-min also catches the fill 30 min out
    assert _removed(capsys, _tape(tmp_path / "a", "Economics")) == "1"
    assert _removed(capsys, _tape(tmp_path / "b", "Sports")) == "11"
    assert _removed(capsys, _tape(tmp_path / "c", "Sports"), "--sweep-min-sports", "15") == "1"
