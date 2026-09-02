"""Horizon selection. P1 lives or dies here: the market price the scorer compares
against is whatever this function returns."""

from datetime import UTC, datetime, timedelta

from src.analysis.horizons import HORIZONS, horizon_points, pick

CLOSE = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def row(minutes_before_close: float, mid: float = 0.5):
    return (CLOSE - timedelta(minutes=minutes_before_close), mid, mid - 0.01, mid + 0.01)


def test_picks_the_nearest_row_at_or_before_the_target() -> None:
    rows = [row(1445), row(1441), row(1439)]  # 24h05m, 24h01m, 23h59m before close
    got = pick(rows, CLOSE - timedelta(hours=24))
    assert got is not None and got[0] == CLOSE - timedelta(minutes=1441)


def test_never_picks_a_row_after_the_target() -> None:
    """A snapshot past the horizon has seen trading the forecast had not. Scoring it as
    contemporaneous is a look-ahead leak that would inflate every downstream number."""
    rows = [row(1439)]  # only a row *after* T-24h
    assert pick(rows, CLOSE - timedelta(hours=24)) is None


def test_exact_hit_is_accepted() -> None:
    rows = [row(1440)]
    got = pick(rows, CLOSE - timedelta(hours=24))
    assert got is not None and got[0] == CLOSE - timedelta(hours=24)


def test_a_stale_row_beyond_tolerance_is_refused() -> None:
    """A sparse-region sample must not stand in for a dense-window horizon."""
    rows = [row(1470)]  # 30 min before T-24h
    assert pick(rows, CLOSE - timedelta(hours=24)) is None
    assert pick(rows, CLOSE - timedelta(hours=24), tolerance=timedelta(minutes=45)) is not None


def test_empty_input_yields_nothing() -> None:
    assert pick([], CLOSE) is None
    assert horizon_points("A", CLOSE, []) == []


def test_all_three_horizons_are_found_when_covered() -> None:
    rows = [row(1441), row(361), row(61)]
    points = horizon_points("A", CLOSE, rows)
    assert [p.horizon_label for p in points] == ["T-24h", "T-6h", "T-1h"]
    assert all(p.lag <= timedelta(minutes=5) for p in points)
    assert [h[0] for h in HORIZONS] == ["T-24h", "T-6h", "T-1h"]


def test_an_uncovered_horizon_is_absent_not_imputed() -> None:
    """A horizon we failed to observe is missing evidence, not a neutral prior."""
    rows = [row(1441)]
    assert [p.horizon_label for p in horizon_points("A", CLOSE, rows)] == ["T-24h"]


def test_unsorted_rows_are_handled() -> None:
    rows = [row(61), row(1441), row(361)]
    assert len(horizon_points("A", CLOSE, rows)) == 3


def test_the_recorded_price_is_the_one_at_the_chosen_instant() -> None:
    """Not a later price, not an average. The pairing is the entire benchmark."""
    rows = [row(1441, mid=0.30), row(1439, mid=0.90)]
    (point,) = [p for p in horizon_points("A", CLOSE, rows) if p.horizon_label == "T-24h"]
    assert point.mid == 0.30
    assert point.ts == CLOSE - timedelta(minutes=1441)
