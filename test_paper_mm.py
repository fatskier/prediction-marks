from paper_mm import Quote, cheap_bid, maker_leg, resting_at


def q(**kw):
    base = dict(ticker="T", side="no", price=0.05, size=10, queue_ahead=100, placed=0.0, id=1)
    base.update(kw)
    return Quote(**base)


def test_queue_ahead_is_consumed_before_we_fill():
    x = q()
    assert x.on_trade("no", 0.05, 60) == 0 and x.queue_ahead == 40
    assert x.on_trade("no", 0.05, 45) == 5 and x.queue_ahead == 0
    assert x.on_trade("no", 0.05, 100) == 5 and x.remaining == 0
    assert x.on_trade("no", 0.05, 100) == 0


def test_print_through_our_level_fills_us_in_full():
    x = q()
    assert x.on_trade("no", 0.04, 1) == 10


def test_better_bids_and_the_other_side_do_not_fill_us():
    x = q()
    assert x.on_trade("no", 0.06, 500) == 0
    assert x.on_trade("yes", 0.05, 500) == 0
    assert x.queue_ahead == 100


def test_cancellations_ahead_move_us_up_but_additions_behind_do_not():
    x = q()
    x.on_level(30)
    assert x.queue_ahead == 30
    x.on_level(80)
    assert x.queue_ahead == 30


def test_book_helpers():
    book = {"yes": [["0.9300", "5"], ["0.9500", "20"]], "no": [["0.0300", "7"], ["0.0400", "250"]]}
    assert cheap_bid(book) == ("no", 0.04, 250.0)
    assert resting_at(book, "no", 0.03) == 7
    t = {"taker_side": "yes", "yes_price_dollars": "0.9600", "no_price_dollars": "0.0400"}
    assert maker_leg(t) == ("no", 0.04)
