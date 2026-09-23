from tape import Seen, Sink, is_tail, read_stream, select_universe


def test_is_tail_both_ends():
    assert is_tail(0.01) and is_tail(0.10) and is_tail(0.95)
    assert not is_tail(0.11) and not is_tail(0.50) and not is_tail(0.89)


def test_select_universe_ranks_and_splits_by_last_price():
    now = 1000.0
    recent = (
        [(now - 10, "A", 0.03)] * 5
        + [(now - 10, "B", 0.50)] * 4
        + [(now - 10, "C", 0.97)] * 3
        + [(now - 5000, "D", 0.02)] * 9  # outside window
        + [(now - 20, "E", 0.40), (now - 1, "E", 0.05)]  # last print is tail
    )
    tails, ctrls = select_universe(recent, now, window=1800, n_tail=2, n_ctrl=5)
    assert tails == ["A", "C"]
    assert ctrls == ["B"]
    tails, _ = select_universe(recent, now, window=1800, n_tail=5, n_ctrl=5)
    assert tails == ["A", "C", "E"]


def test_select_universe_skips_ineligible_and_backfills():
    now = 1000.0
    recent = [(now, "A", 0.02)] * 3 + [(now, "B", 0.03)] * 2 + [(now, "C", 0.04)]
    asked = []

    def eligible(t):
        asked.append(t)
        return t != "A"

    tails, _ = select_universe(recent, now, 1800, n_tail=2, n_ctrl=0, eligible=eligible)
    assert tails == ["B", "C"]
    assert asked == ["A", "B", "C"]


def test_seen_dedupes_and_is_bounded():
    s = Seen(cap=2)
    assert s.add("a") and not s.add("a")
    s.add("b"), s.add("c")
    assert s.add("a")  # evicted, so new again


def test_restart_after_kill_keeps_both_runs_readable(tmp_path):
    killed = Sink(str(tmp_path))
    killed.write("books", [{"x": 1}, {"x": 2}])  # flushed, never closed: no gzip trailer
    fresh = Sink(str(tmp_path))
    fresh.write("books", [{"x": 3}])
    fresh.close()
    assert len(list(tmp_path.glob("*/*.books*.jsonl.gz"))) == 2
    assert [r["x"] for r in read_stream(str(tmp_path), "books")] == [1, 2, 3]


def test_sink_restart_writes_new_readable_part(tmp_path):
    sink = Sink(str(tmp_path))
    sink.write("trades", [{"x": 1}])
    sink.close()
    sink = Sink(str(tmp_path))
    sink.write("trades", [{"x": 2}])
    sink.close()
    assert [r["x"] for r in read_stream(str(tmp_path), "trades")] == [1, 2]


def _recorder(tmp_path, markets, events):
    import time
    from datetime import datetime, timezone
    from tape import Recorder

    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")

    class Args:
        host, out, rate, window = "prod", str(tmp_path), 100.0, 1800
        sweep_min, sweep_min_sports = 15, 60

    rec = Recorder(Args)
    now = time.time()

    def get(path, **_):
        kind, key = path.strip("/").split("/")
        if kind == "markets":
            ev, mins = markets[key]
            return {"market": {"ticker": key, "event_ticker": ev, "status": "active",
                               "close_time": iso(now + mins * 60)}}
        return {"event": {"event_ticker": key, "category": events[key]}}

    rec.api.get = get
    return rec


def test_eligible_respects_sweep_window_by_category(tmp_path):
    rec = _recorder(tmp_path,
                    {"BTC30": ("E-BTC", 30), "BTC10": ("E-BTC", 10),
                     "TEN30": ("E-TEN", 30), "TEN90": ("E-TEN", 90)},
                    {"E-BTC": "Crypto", "E-TEN": "Sports"})
    assert rec.eligible("BTC30") and not rec.eligible("BTC10")
    assert not rec.eligible("TEN30") and rec.eligible("TEN90")


def test_eligible_drops_markets_seen_to_stop(tmp_path):
    rec = _recorder(tmp_path, {"TEN90": ("E-TEN", 90)}, {"E-TEN": "Sports"})
    assert rec.eligible("TEN90")
    rec.status["TEN90"] = "inactive"
    assert not rec.eligible("TEN90")
