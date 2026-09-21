"""KalshiSource against respx, driven by the recorded responses. No network."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from pydantic import ValidationError

from src.ingest.base import Market, MarketDataSource, Resolution
from src.ingest.http import MAX_ATTEMPTS, TICK_ATTEMPTS
from src.ingest.kalshi import KalshiMarket, KalshiSource

BASE = "https://api.test/trade-api/v2"

SERIES = {"series": [{"ticker": "KXCS2GAME", "category": "Sports"}]}


@pytest.fixture
def source() -> Any:
    with KalshiSource(BASE, 5.0) as s:
        yield s


def test_source_satisfies_the_protocol() -> None:
    assert isinstance(KalshiSource(BASE, 5.0), MarketDataSource)


def test_prices_arrive_as_dollar_strings_and_parse_to_floats(sample: Any) -> None:
    row = sample("markets_open")["markets"][0]
    m = KalshiMarket.model_validate(row)
    assert isinstance(m.volume_fp, float)
    for price in (m.yes_bid_dollars, m.yes_ask_dollars):
        assert price is None or isinstance(price, float)


def test_empty_string_price_becomes_none_not_zero() -> None:
    """Absence is sent as "", and coercing that to 0.0 would invent a 0% quote."""
    m = KalshiMarket.model_validate(
        {
            "ticker": "A-B",
            "event_ticker": "A",
            "close_time": "2026-09-05T01:40:00Z",
            "yes_bid_dollars": "",
            "yes_ask_dollars": "0.4200",
            "volume_fp": "",
        }
    )
    assert m.yes_bid_dollars is None
    assert m.yes_ask_dollars == 0.42
    assert m.volume_fp == 0.0


def test_series_ticker_is_derived_from_the_market_ticker(sample: Any) -> None:
    m = KalshiMarket.model_validate(sample("market_single")["market"])
    assert m.ticker.startswith(m.series_ticker + "-")


@respx.mock
def test_list_open_markets_drops_combos_and_attaches_category(source: KalshiSource) -> None:
    rows = {
        "cursor": "",
        "markets": [
            {
                "ticker": "KXCS2GAME-X-VP",
                "event_ticker": "KXCS2GAME-X",
                "title": "real market",
                "status": "active",
                "close_time": "2026-09-05T01:40:00Z",
                "yes_bid_dollars": "0.4900",
                "yes_ask_dollars": "0.5300",
                "volume_fp": "1000.00",
            },
            {
                "ticker": "KXMVECROSSCATEGORY-S1-A",
                "event_ticker": "KXMVECROSSCATEGORY-S1",
                "title": "combo shard",
                "status": "active",
                "close_time": "2026-09-05T01:40:00Z",
                "mve_collection_ticker": "KXMVECROSSCATEGORY-SHARD1-R",
                "volume_fp": "0.00",
            },
        ],
    }
    respx.get(f"{BASE}/markets").mock(return_value=httpx.Response(200, json=rows))
    respx.get(f"{BASE}/series").mock(return_value=httpx.Response(200, json=SERIES))

    out = source.list_open_markets()
    assert [m.market_id for m in out] == ["KXCS2GAME-X-VP"]
    assert out[0].category == "Sports", "category comes from the series, not the market"
    assert out[0].event_family == "KXCS2GAME-X", "clustering key for the bootstrap"

    assert len(source.list_open_markets(include_combos=True)) == 2


@respx.mock
def test_paging_follows_the_cursor_and_stops_when_it_empties(source: KalshiSource) -> None:
    pages = [
        httpx.Response(200, json={"cursor": "c1", "markets": [_row("A-1")]}),
        httpx.Response(200, json={"cursor": "", "markets": [_row("A-2")]}),
    ]
    respx.get(f"{BASE}/markets").mock(side_effect=pages)
    respx.get(f"{BASE}/series").mock(return_value=httpx.Response(200, json=SERIES))
    assert [m.market_id for m in source.list_open_markets()] == ["A-1", "A-2"]


@respx.mock
def test_get_book_converts_the_recorded_orderbook(source: KalshiSource, sample: Any) -> None:
    respx.get(f"{BASE}/markets/T/orderbook").mock(
        return_value=httpx.Response(200, json=sample("market_orderbook"))
    )
    book = source.get_book("T")
    assert book.best_ask > book.best_bid
    assert book.yes_asks and book.yes_bids


@respx.mock
def test_a_failing_book_cannot_hold_the_sweep(source: KalshiSource) -> None:
    """One market's retries used to be every market's outage.

    `get_book` is called once a minute per market inside the dense sweep, all of it in
    one thread, so eight attempts backing off toward 120 s stall the markets queued
    behind it too. That is what opened 70 CRITICAL dense-window gaps between 2026-09-17
    and -20: the stalls spanned the minute the windows opened. The next tick is the
    retry, so this call has to give up while the tick still has time left.
    """
    source._http._wait = lambda *a, **k: 0  # backoff, not attempts, is what tests skip
    route = respx.get(f"{BASE}/markets/T/orderbook").mock(
        side_effect=httpx.ConnectError("WinError 10054")
    )
    with pytest.raises(httpx.ConnectError):
        source.get_book("T")
    assert route.call_count == TICK_ATTEMPTS
    assert TICK_ATTEMPTS < MAX_ATTEMPTS, "the sweep's budget must stay below the default"


@respx.mock
def test_get_price_history_reads_bid_ask_not_just_trade_price(
    source: KalshiSource, sample: Any
) -> None:
    respx.get(url__regex=rf"{BASE}/series/.+/candlesticks").mock(
        return_value=httpx.Response(200, json=sample("candlesticks"))
    )
    pts = source.get_price_history(
        "KXCS2GAME-A-B", datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 2, tzinfo=UTC)
    )
    assert pts, "recorded sample has candles"
    quoted = [p for p in pts if p.mid is not None]
    assert quoted, "bid/ask must survive parsing; a mid from trade price is a different number"
    assert all(0.0 <= p.mid <= 1.0 for p in quoted)


@respx.mock
def test_resolution_maps_yes_no_and_refuses_to_guess_anything_else(
    source: KalshiSource,
) -> None:
    rows = {
        "cursor": "",
        "markets": [
            _row("A-1", result="yes", status="finalized"),
            _row("A-2", result="no", status="finalized"),
            _row("A-3", result="", status="finalized"),
            _row("A-4", result="void", status="finalized"),
        ],
    }
    respx.get(f"{BASE}/markets").mock(return_value=httpx.Response(200, json=rows))
    out: list[Resolution] = source.list_recently_resolved(datetime(2026, 9, 1, tzinfo=UTC))
    assert {r.market_id: r.outcome for r in out} == {"A-1": 1, "A-2": 0}
    assert all(r.outcome in (0, 1) for r in out)


def _row(ticker: str, **extra: Any) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "event_ticker": ticker.rsplit("-", 1)[0],
        "title": ticker,
        "status": "active",
        "close_time": "2026-09-05T01:40:00Z",
        "expiration_time": "2026-09-05T01:40:00Z",
        "volume_fp": "1.00",
        **extra,
    }


def test_market_model_is_immutable() -> None:
    """P3: nothing downstream may quietly rewrite a captured observation."""
    m = Market(
        market_id="A-1",
        event_family="A",
        series_id="A",
        question="q",
        status="active",
        close_time=datetime(2026, 9, 5, tzinfo=UTC),
    )
    with pytest.raises(ValidationError):
        m.market_id = "B-2"  # type: ignore[misc]


@respx.mock
def test_future_expiration_time_does_not_become_the_resolution_time(
    source: KalshiSource,
) -> None:
    """expiration_time is a scheduled expiry, not a settlement instant. For a market that
    closed early it can sit days ahead while status already reads 'finalized'. Letting it
    through put resolved_at after now, which silently excluded the market from every
    window query — reconciliation read zero while thousands had settled."""
    future = (datetime.now(UTC) + timedelta(days=5)).isoformat().replace("+00:00", "Z")
    past = (datetime.now(UTC) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    rows = {
        "cursor": "",
        "markets": [
            _row("A-1", result="yes", status="finalized", close_time=past, expiration_time=future),
        ],
    }
    respx.get(f"{BASE}/markets").mock(return_value=httpx.Response(200, json=rows))
    (res,) = source.list_recently_resolved(datetime.now(UTC) - timedelta(days=1))
    assert res.resolved_at <= datetime.now(UTC), "a settled market cannot resolve in the future"
    assert res.resolved_at == datetime.fromisoformat(past.replace("Z", "+00:00"))


@respx.mock
def test_past_expiration_time_is_used_when_available(source: KalshiSource) -> None:
    close = (datetime.now(UTC) - timedelta(hours=5)).isoformat().replace("+00:00", "Z")
    expiry = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    rows = {
        "cursor": "",
        "markets": [
            _row("A-1", result="no", status="finalized", close_time=close, expiration_time=expiry),
        ],
    }
    respx.get(f"{BASE}/markets").mock(return_value=httpx.Response(200, json=rows))
    (res,) = source.list_recently_resolved(datetime.now(UTC) - timedelta(days=1))
    assert res.resolved_at == datetime.fromisoformat(expiry.replace("Z", "+00:00"))
