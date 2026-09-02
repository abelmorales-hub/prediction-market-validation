"""Emitting baseline forecasts into the append-only predictions table."""

from datetime import UTC, datetime, timedelta

import pytest

from src.analysis.emit import baseline_values, emit, is_scorable
from src.analysis.horizons import HorizonPoint

NOW = datetime(2026, 9, 10, 0, 0, tzinfo=UTC)
CLOSE = NOW + timedelta(hours=24)


class FakeConn:
    def __init__(self) -> None:
        self.rollbacks = 0

    def rollback(self) -> None:
        self.rollbacks += 1


class FakeRepo:
    """Records inserts and can be told to fail on specific ones."""

    def __init__(self, fail_with: dict[int, Exception] | None = None) -> None:
        self.conn = FakeConn()
        self.rows: list[object] = []
        self.fail_with = fail_with or {}
        self._n = 0

    def insert_prediction(self, p: object) -> int:
        exc = self.fail_with.get(self._n)
        self._n += 1
        if exc is not None:
            raise exc
        self.rows.append(p)
        return len(self.rows)


def _point(mid: float = 0.6, market: str = "M1") -> HorizonPoint:
    return HorizonPoint(market, "T-24h", NOW, NOW, mid, mid - 0.01, mid + 0.01)


def test_writes_one_row_per_baseline() -> None:
    repo = FakeRepo()
    stats = emit(repo, [_point()], {"M1": CLOSE}, "abc123", NOW)
    assert stats.written == 2
    assert {r.model_version for r in repo.rows} == {"baseline_market", "baseline_shrink"}


def test_the_market_baseline_stores_the_price_it_is_benchmarked_against() -> None:
    """p_hat must equal market_mid exactly, or the null stops scoring zero."""
    repo = FakeRepo()
    emit(repo, [_point(mid=0.37)], {"M1": CLOSE}, "sha", NOW)
    row = next(r for r in repo.rows if r.model_version == "baseline_market")
    assert row.p_hat == row.market_mid == 0.37


def test_a_closed_market_is_skipped_before_the_database_refuses_it() -> None:
    repo = FakeRepo()
    stats = emit(repo, [_point()], {"M1": NOW - timedelta(seconds=1)}, "sha", NOW)
    assert stats.written == 0 and stats.skipped_closed == 1
    assert repo.rows == [], "must not even attempt a post-close write"


def test_a_market_with_no_known_close_is_skipped() -> None:
    repo = FakeRepo()
    stats = emit(repo, [_point()], {}, "sha", NOW)
    assert stats.skipped_closed == 1 and repo.rows == []


@pytest.mark.parametrize("mid", [0.0, 1.0])
def test_a_degenerate_price_is_excluded_not_clipped(mid: float) -> None:
    """Clipping into (0,1) would make baseline_market differ from the price it is scored
    against, quietly breaking the one test that proves the pipeline is unbiased."""
    repo = FakeRepo()
    stats = emit(repo, [_point(mid=mid)], {"M1": CLOSE}, "sha", NOW)
    assert stats.skipped_degenerate == 1 and repo.rows == []
    assert not is_scorable(mid)


def test_is_scorable_accepts_the_open_interval() -> None:
    assert is_scorable(0.0001) and is_scorable(0.9999) and is_scorable(0.5)


def test_a_duplicate_rolls_back_and_the_pass_continues() -> None:
    """Postgres aborts the transaction on any failed statement. Without the rollback the
    first duplicate cascades into 'transaction is aborted' for every remaining row."""
    repo = FakeRepo(fail_with={0: Exception('duplicate key value violates unique constraint')})
    points = [_point(market="M1"), _point(market="M2")]
    stats = emit(repo, points, {"M1": CLOSE, "M2": CLOSE}, "sha", NOW)
    assert stats.skipped_existing == 1
    assert stats.written == 3, "the remaining rows still went in"
    assert stats.failed == 0
    assert repo.conn.rollbacks == 1


def test_a_real_failure_is_counted_and_does_not_stop_the_pass() -> None:
    repo = FakeRepo(fail_with={0: Exception("connection reset")})
    points = [_point(market="M1"), _point(market="M2")]
    stats = emit(repo, points, {"M1": CLOSE, "M2": CLOSE}, "sha", NOW)
    assert stats.failed == 1 and stats.written == 3
    assert repo.conn.rollbacks == 1


def test_base_rate_is_not_emitted_yet() -> None:
    """It needs a rate from prior resolutions; with nothing resolved, any rate would come
    from the very outcomes it is later scored against."""
    assert set(baseline_values(0.5)) == {"baseline_market", "baseline_shrink"}


def test_shrink_pulls_toward_the_centre() -> None:
    assert baseline_values(0.9)["baseline_shrink"] == pytest.approx(0.86)
