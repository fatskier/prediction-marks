import gzip
import json

from tape import Seen, Sink, is_tail, select_universe


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


def test_sink_appends_readable_gzip(tmp_path):
    sink = Sink(str(tmp_path))
    sink.write("trades", [{"x": 1}])
    sink.close()
    sink = Sink(str(tmp_path))
    sink.write("trades", [{"x": 2}])
    sink.close()
    (f,) = tmp_path.glob("*/*.trades.jsonl.gz")
    with gzip.open(f, "rt") as fh:
        assert [json.loads(line)["x"] for line in fh] == [1, 2]
