"""Resolution watcher against fakes. No network, no DB.

The behaviour under most scrutiny here is what the watcher *refuses* to do: it must
never populate was_disputed, because the venue exposes nothing to populate it from.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from src.ingest.base import Book, Market, PricePoint, Resolution
from src.resolution.watcher import (
    ResolutionWatcher,
    WatchStats,
    lag_minutes,
    lag_thresholds,
)

CLOSE = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


class FakeSource:
    def __init__(self, resolutions: list[Resolution], markets: dict[str, Market] | None = None):
        self.resolutions = resolutions
        self.markets = markets or {}
        self.get_market_calls: list[str] = []

    def list_recently_resolved(self, since: datetime) -> list[Resolution]:
        return self.resolutions

    def get_market(self, market_id: str) -> Market:
        self.get_market_calls.append(market_id)
        if market_id not in self.markets:
            raise KeyError(market_id)
        return self.markets[market_id]

    def list_open_markets(self, **kw: Any) -> list[Market]:
        return []

    def get_book(self, market_id: str, *, depth: int = 100) -> Book:
        raise NotImplementedError

    def get_price_history(self, *a: Any, **kw: Any) -> list[PricePoint]:
        return []


class FakeRepo:
    def __init__(self, meta: dict[str, dict[str, Any]] | None = None) -> None:
        self._meta = meta or {}
        self.outcomes: list[tuple[Resolution, bool | None, str | None]] = []
        self.upserted: list[Market] = []

    def market_meta(self, ids: Any) -> dict[str, dict[str, Any]]:
        return {i: self._meta[i] for i in ids if i in self._meta}

    def upsert_markets(self, markets: Any) -> int:
        self.upserted.extend(markets)
        for m in markets:
            self._meta[m.market_id] = {"category": m.category, "end_date": m.close_time}
        return len(markets)

    def upsert_outcome(
        self, r: Resolution, was_disputed: bool | None = None, notes: str | None = None
    ) -> None:
        self.outcomes.append((r, was_disputed, notes))


def _res(market_id: str, lag_min: float = 0.0, outcome: int = 1) -> Resolution:
    return Resolution(
        market_id=market_id,
        resolved_at=CLOSE + timedelta(minutes=lag_min),
        outcome=outcome,
        raw_result="yes" if outcome else "no",
    )


def _meta(category: str = "Sports") -> dict[str, Any]:
    return {"category": category, "end_date": CLOSE}


# -- the refusal ----------------------------------------------------------


def test_was_disputed_is_never_written(tmp_path: Any) -> None:
    """The venue exposes no dispute field. NULL means unknown; False would be a lie."""
    repo = FakeRepo({f"M-{i}": _meta() for i in range(30)})
    src = FakeSource([_res(f"M-{i}", lag_min=i * 100) for i in range(30)])
    ResolutionWatcher(src, repo).poll(CLOSE)  # type: ignore[arg-type]
    assert repo.outcomes, "something was written"
    assert all(disputed is None for _, disputed, _ in repo.outcomes)


def test_outcomes_are_written_for_known_markets() -> None:
    repo = FakeRepo({"M-1": _meta(), "M-2": _meta()})
    src = FakeSource([_res("M-1", outcome=1), _res("M-2", outcome=0)])
    stats = ResolutionWatcher(src, repo).poll(CLOSE)  # type: ignore[arg-type]
    assert stats.written == 2
    assert {r.market_id: r.outcome for r, _, _ in repo.outcomes} == {"M-1": 1, "M-2": 0}


# -- cataloguing ----------------------------------------------------------


def test_unknown_market_is_catalogued_before_its_outcome() -> None:
    """outcomes has a foreign key to markets, so an unseen market must be inserted first
    or the outcome is simply lost."""
    market = Market(
        market_id="NEW-1",
        event_family="E",
        series_id="E",
        question="q",
        category="Sports",
        status="finalized",
        close_time=CLOSE,
    )
    repo = FakeRepo({})
    src = FakeSource([_res("NEW-1")], markets={"NEW-1": market})
    watcher = ResolutionWatcher(src, repo, max_catalogue_fetches=10)  # type: ignore[arg-type]
    stats = watcher.poll(CLOSE)
    assert stats.catalogued == 1
    assert [m.market_id for m in repo.upserted] == ["NEW-1"]
    assert stats.written == 1


def test_market_that_cannot_be_catalogued_is_skipped_not_crashed() -> None:
    repo = FakeRepo({"M-1": _meta()})
    src = FakeSource([_res("M-1"), _res("GONE-1")], markets={})
    watcher = ResolutionWatcher(src, repo, max_catalogue_fetches=10)  # type: ignore[arg-type]
    stats = watcher.poll(CLOSE)
    assert stats.written == 1
    assert stats.skipped_unknown == 1
    assert stats.errors.get("KeyError") == 1


def test_uncatalogued_markets_are_skipped_by_default() -> None:
    """The venue settles ~5000 markets a day, almost all combo shards we never watched.
    Recording outcomes for them would make tracked coverage meaningless."""
    repo = FakeRepo({"M-1": _meta()})
    src = FakeSource([_res("M-1"), _res("NEVER-WATCHED-1")], markets={})
    stats = ResolutionWatcher(src, repo).poll(CLOSE)  # type: ignore[arg-type]
    assert stats.written == 1
    assert stats.skipped_unknown == 1
    assert src.get_market_calls == [], "no fetch attempted for markets we never tracked"


def test_already_catalogued_markets_cost_no_fetches() -> None:
    repo = FakeRepo({"M-1": _meta()})
    src = FakeSource([_res("M-1")])
    ResolutionWatcher(src, repo).poll(CLOSE)  # type: ignore[arg-type]
    assert src.get_market_calls == []


# -- the lag heuristic ----------------------------------------------------


def test_lag_is_measured_from_close_not_from_now() -> None:
    assert lag_minutes(CLOSE + timedelta(minutes=90), CLOSE) == 90.0


def test_no_threshold_for_a_category_with_too_few_observations() -> None:
    """An outlier test on n=3 invents its own outliers."""
    assert lag_thresholds({"Sports": [0.0, 1.0, 5000.0]}, min_n=20) == {}


def test_threshold_respects_the_absolute_floor() -> None:
    """Median lag is 0 for most categories, so a purely relative cutoff would flag every
    market that took two minutes to settle."""
    thresholds = lag_thresholds({"Sports": [0.0] * 100}, min_n=20, floor_min=60.0)
    assert thresholds["Sports"] == 60.0


def test_threshold_rises_above_the_floor_for_slow_categories() -> None:
    lags = [float(i * 100) for i in range(100)]  # p95 = 9500
    thresholds = lag_thresholds({"Politics": lags}, quantile=0.95, min_n=20, floor_min=60.0)
    assert thresholds["Politics"] > 60.0


def test_outlier_gets_a_note_that_says_it_is_heuristic() -> None:
    repo = FakeRepo({f"M-{i}": _meta() for i in range(40)})
    resolutions = [_res(f"M-{i}", lag_min=0.0) for i in range(39)] + [_res("M-39", lag_min=9000)]
    stats = ResolutionWatcher(FakeSource(resolutions), repo).poll(CLOSE)  # type: ignore[arg-type]
    assert stats.flagged_lag == 1
    notes = {r.market_id: n for r, _, n in repo.outcomes}
    assert notes["M-39"] is not None
    assert "HEURISTIC" in notes["M-39"]
    assert "Not a dispute flag" in notes["M-39"]
    assert notes["M-0"] is None, "an on-time settlement carries no note"


def test_nothing_is_flagged_when_the_category_is_too_small() -> None:
    repo = FakeRepo({"M-1": _meta(), "M-2": _meta()})
    src = FakeSource([_res("M-1", lag_min=0), _res("M-2", lag_min=99999)])
    stats = ResolutionWatcher(src, repo).poll(CLOSE)  # type: ignore[arg-type]
    assert stats.written == 2
    assert stats.flagged_lag == 0, "no threshold exists, so nothing can exceed it"


def test_categories_are_thresholded_independently() -> None:
    """A politics market settling a day late is normal; a sports one is not."""
    meta = {f"S-{i}": _meta("Sports") for i in range(25)}
    meta.update({f"P-{i}": _meta("Politics") for i in range(25)})
    repo = FakeRepo(meta)
    resolutions = [_res(f"S-{i}", lag_min=0.0) for i in range(24)]
    resolutions.append(_res("S-24", lag_min=1440))  # a day late for Sports: unusual
    resolutions += [_res(f"P-{i}", lag_min=1440.0) for i in range(25)]  # normal for Politics
    stats = ResolutionWatcher(FakeSource(resolutions), repo).poll(CLOSE)  # type: ignore[arg-type]
    flagged = {r.market_id for r, _, n in repo.outcomes if n}
    assert "S-24" in flagged
    assert not any(m.startswith("P-") for m in flagged)
    assert stats.flagged_lag == 1


def test_empty_poll_is_a_no_op() -> None:
    repo = FakeRepo({})
    stats = ResolutionWatcher(FakeSource([]), repo).poll(CLOSE)  # type: ignore[arg-type]
    assert stats == WatchStats()
    assert repo.outcomes == []


@pytest.mark.parametrize("outcome", [0, 1])
def test_outcome_values_pass_through_unchanged(outcome: int) -> None:
    repo = FakeRepo({"M-1": _meta()})
    ResolutionWatcher(FakeSource([_res("M-1", outcome=outcome)]), repo).poll(CLOSE)  # type: ignore[arg-type]
    assert repo.outcomes[0][0].outcome == outcome
