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
