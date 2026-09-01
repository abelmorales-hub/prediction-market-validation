"""Book conversion is the highest-risk logic in the ingest layer.

Kalshi quotes both sides as bid stacks. Reading `no_dollars` as an ask ladder instead of
converting it at 1-p silently inverts the book, and every downstream number — mid, the
market's own Brier, the whole benchmark — would be wrong while looking plausible.
"""

from typing import Any

from src.ingest.kalshi import build_book


def test_no_bids_become_yes_asks_at_one_minus_p() -> None:
    book = build_book("T", [["0.60", "10"], ["0.62", "20"]], [["0.30", "5"], ["0.35", "7"]])
    assert [lv.price for lv in book.yes_bids] == [0.60, 0.62]
    # NO bids at .30/.35 are YES asks at .70/.65; best (lowest) ask last.
    assert [round(lv.price, 4) for lv in book.yes_asks] == [0.70, 0.65]
    assert book.best_bid == 0.62
    assert round(book.best_ask, 4) == 0.65
    assert round(book.mid, 4) == 0.635
    assert round(book.spread, 4) == 0.03


def test_sizes_survive_the_price_inversion() -> None:
    """The 1-p flip applies to price only; size travels with its level."""
    book = build_book("T", [["0.60", "10"]], [["0.30", "5"], ["0.35", "7"]])
    assert {round(lv.price, 4): lv.size for lv in book.yes_asks} == {0.70: 5.0, 0.65: 7.0}


def test_depth_within_one_cent_sums_only_the_touch_neighbourhood() -> None:
    book = build_book(
        "T",
        [["0.60", "1"], ["0.61", "2"], ["0.62", "4"]],  # bids; touch .62
        [["0.30", "8"], ["0.36", "16"], ["0.37", "32"]],  # asks .70/.64/.63; touch .63
    )
    bid_depth, ask_depth = book.depth_within(0.01)
    assert bid_depth == 6.0, "0.62 + 0.61 inside a cent of the touch, 0.60 outside"
    assert ask_depth == 48.0, "0.63 + 0.64 inside a cent of the touch, 0.70 outside"


def test_empty_book_yields_none_not_zero() -> None:
    """A market with no quotes must not report a mid of 0.0 — that is a real price."""
    book = build_book("T", [], [])
    assert book.best_bid is None and book.best_ask is None
    assert book.mid is None and book.spread is None
    assert book.depth_within() == (0.0, 0.0)


def test_one_sided_book_has_no_mid() -> None:
    book = build_book("T", [["0.60", "10"]], [])
    assert book.best_bid == 0.60
    assert book.best_ask is None
    assert book.mid is None


def test_recorded_orderbook_parses_into_a_sane_book(sample: Any) -> None:
    """Structural invariants on a real recorded book. Agreement with the venue's own
    quote is checked live in tests/integration/, not here: the recorded book and the
    recorded market object were two sequential requests against a market that moves,
    so any tolerance asserted against them would be arbitrary and flaky."""
    ob = sample("market_orderbook")["orderbook_fp"]
    market = sample("market_single")["market"]
    book = build_book(market["ticker"], ob.get("yes_dollars"), ob.get("no_dollars"))
    assert book.best_bid is not None and book.best_ask is not None
    assert book.best_ask > book.best_bid, "asks must sit above bids or the book is inverted"
    assert 0.0 < book.mid < 1.0
    assert all(0.0 <= lv.price <= 1.0 for lv in book.yes_bids + book.yes_asks)
    assert all(lv.size > 0 for lv in book.yes_bids + book.yes_asks)
    # Ascending bids, descending asks: the touch is the last element on both sides.
    assert list(book.yes_bids) == sorted(book.yes_bids, key=lambda lv: lv.price)
    assert list(book.yes_asks) == sorted(book.yes_asks, key=lambda lv: -lv.price)
