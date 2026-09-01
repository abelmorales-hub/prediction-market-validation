"""Hits the live Kalshi API. Excluded from the default run.

The point of these is the correspondence the recorded fixtures cannot prove: that the
NO-bid-to-YES-ask conversion reproduces the venue's own quote on a book fetched now.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.config import get_settings
from src.ingest.base import MarketDataSource
from src.ingest.kalshi import KalshiSource

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def source() -> KalshiSource:
    cfg = get_settings()
    with KalshiSource(
        cfg.kalshi_base_url,
        cfg.http_timeout_s,
        cache_dir=cfg.cache_dir,
        metadata_ttl_s=cfg.metadata_cache_ttl_s,
    ) as s:
        yield s


def test_live_source_satisfies_the_protocol(source: KalshiSource) -> None:
    assert isinstance(source, MarketDataSource)


def test_list_open_markets_returns_real_non_combo_markets(source: KalshiSource) -> None:
    markets = source.list_open_markets(closing_within_s=48 * 3600)
    assert markets, "no open markets closing within 48h"
    assert not any(m.is_combo for m in markets), "combos must be filtered by default"
    assert all(m.close_time > datetime.now(UTC) for m in markets)
    assert any(m.category for m in markets), "category must be joined from the series"
    assert len({m.event_family for m in markets}) > 1, "clustering key must vary"


def test_derived_book_reproduces_the_venue_quote(source: KalshiSource) -> None:
    """The conversion check that matters. A book fetched and a quote read moments apart
    must agree; an inverted NO side would disagree wildly, especially near the extremes.
    """
    markets = [
        m
        for m in source.list_open_markets(closing_within_s=48 * 3600)
        if m.yes_bid and m.yes_ask and 0 < m.yes_bid < m.yes_ask < 1
    ]
    markets.sort(key=lambda m: -m.volume)
    assert markets, "no two-sided quoted markets available"

    agreements = 0
    checked = markets[:5]
    for m in checked:
        book = source.get_book(m.market_id)
        fresh = source.get_market(m.market_id)
        assert book.best_bid is not None and book.best_ask is not None
        assert book.best_ask > book.best_bid, f"{m.market_id}: book inverted"
        if fresh.yes_bid is not None and fresh.yes_ask is not None:
            close = (
                abs(book.best_bid - fresh.yes_bid) <= 0.02
                and abs(book.best_ask - fresh.yes_ask) <= 0.02
            )
            agreements += close
    # Prices move between two sequential requests, so a majority is the honest bar; a
    # genuine misconversion would fail on every market, not most of them.
    assert agreements >= len(checked) // 2 + 1, f"only {agreements}/{len(checked)} agreed"


def test_price_history_returns_ordered_bars(source: KalshiSource) -> None:
    market = max(source.list_open_markets(closing_within_s=48 * 3600), key=lambda m: m.volume)
    end = datetime.now(UTC)
    points = source.get_price_history(
        market.market_id, end - timedelta(hours=6), end, interval_min=60
    )
    assert points, f"no candles for {market.market_id}"
    assert [p.ts for p in points] == sorted(p.ts for p in points)
    assert all(p.mid is None or 0.0 <= p.mid <= 1.0 for p in points)


def test_recently_resolved_yields_binary_outcomes(source: KalshiSource) -> None:
    resolutions = source.list_recently_resolved(datetime.now(UTC) - timedelta(hours=24))
    assert resolutions, "no markets resolved in the last 24h"
    assert {r.outcome for r in resolutions} <= {0, 1}
    assert all(r.raw_result in ("yes", "no") for r in resolutions)
