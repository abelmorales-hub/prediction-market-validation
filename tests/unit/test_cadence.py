"""Cadence is a pure function, so the boundaries can be asserted exactly.

The boundaries are the whole risk: an off-by-one at the edge of the T-24h window means
the horizon the scorer compares against was never sampled, and nothing downstream would
report that as an error.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.scheduler.cadence import (
    DENSE,
    SPARSE,
    WINDOWS,
    dense_window_starts,
    in_dense_window,
    interval_at,
    next_snapshot_at,
)

CLOSE = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def at(**kw: float) -> datetime:
    """An instant expressed as time-to-close."""
    return CLOSE - timedelta(**kw)


# -- the four windows -----------------------------------------------------


@pytest.mark.parametrize(
    "when,expected",
    [
        (at(hours=24), DENSE),  # T-24h centre
        (at(hours=6), DENSE),  # T-6h centre
        (at(hours=1), DENSE),  # T-1h centre
        (at(hours=48), SPARSE),  # far out
        (at(hours=12), SPARSE),  # between the 24h and 6h windows
        (at(hours=3), SPARSE),  # between the 6h and 1h windows
        (at(minutes=10), SPARSE),  # inside the last hour but past the T-1h window
    ],
)
def test_interval_for_each_region(when: datetime, expected: timedelta) -> None:
    assert interval_at(when, CLOSE) == expected


@pytest.mark.parametrize(
    "when,dense",
    [
        # T-24h +/- 15min: [T-24h15, T-23h45]
        (at(hours=24, minutes=15), True),
        (at(hours=23, minutes=45), True),
        (at(hours=24, minutes=15, seconds=1), False),
        (at(hours=23, minutes=44, seconds=59), False),
        # T-6h +/- 30min: [T-6h30, T-5h30]
        (at(hours=6, minutes=30), True),
        (at(hours=5, minutes=30), True),
        (at(hours=6, minutes=30, seconds=1), False),
        (at(hours=5, minutes=29, seconds=59), False),
        # T-1h +/- 15min: [T-1h15, T-45min]
        (at(hours=1, minutes=15), True),
        (at(minutes=45), True),
        (at(hours=1, minutes=15, seconds=1), False),
        (at(minutes=44, seconds=59), False),
    ],
)
def test_window_edges_are_inclusive(when: datetime, dense: bool) -> None:
    assert in_dense_window(when, CLOSE) is dense


def test_closed_market_is_never_dense() -> None:
    assert in_dense_window(CLOSE, CLOSE) is False
    assert in_dense_window(CLOSE + timedelta(minutes=1), CLOSE) is False


# -- next_snapshot_at -----------------------------------------------------


def test_dense_region_steps_one_minute() -> None:
    now = at(hours=24)
    assert next_snapshot_at(now, CLOSE) == now + DENSE


def test_sparse_region_steps_thirty_minutes() -> None:
    now = at(hours=48)
    assert next_snapshot_at(now, CLOSE) == now + SPARSE


def test_sparse_step_is_clamped_to_the_window_opening() -> None:
    """The bug this prevents: a 30-minute step starting shortly before the T-24h window
    lands deep inside it, and its opening minutes are never sampled.

    The narrower the window, the more this matters: at +/-15 min a single unclamped
    sparse step clears the entire window without taking one sample."""
    now = at(hours=24, minutes=35)  # 20 min before the T-24h window opens
    assert next_snapshot_at(now, CLOSE) == at(hours=24, minutes=15), "stop at the edge"
    assert in_dense_window(next_snapshot_at(now, CLOSE), CLOSE)


def test_clamping_picks_the_nearest_window() -> None:
    now = at(hours=6, minutes=50)  # T-6h30 window opens in 20 min
    assert next_snapshot_at(now, CLOSE) == at(hours=6, minutes=30)


def test_no_clamp_when_no_window_is_crossed() -> None:
    now = at(hours=12)
    assert next_snapshot_at(now, CLOSE) == now + SPARSE


def test_never_schedules_past_the_close() -> None:
    now = at(minutes=10)
    nxt = next_snapshot_at(now, CLOSE)
    assert nxt is not None and nxt <= CLOSE


def test_closed_market_has_no_next_snapshot() -> None:
    assert next_snapshot_at(CLOSE, CLOSE) is None
    assert next_snapshot_at(CLOSE + timedelta(hours=1), CLOSE) is None


def test_window_starts_are_ordered_and_correct() -> None:
    assert dense_window_starts(CLOSE) == [
        at(hours=24, minutes=15),
        at(hours=6, minutes=30),
        at(hours=1, minutes=15),
    ]


def test_walking_the_schedule_samples_every_window() -> None:
    """End to end: follow next_snapshot_at from T-30h to close and confirm every dense
    window is entered, and that the three horizons are covered to the minute."""
    now = at(hours=30)
    seen: list[datetime] = []
    while (nxt := next_snapshot_at(now, CLOSE)) is not None and len(seen) < 5000:
        seen.append(nxt)
        now = nxt
    assert now == CLOSE, "the walk must terminate exactly at close"
    for horizon in (timedelta(hours=24), timedelta(hours=6), timedelta(hours=1)):
        target = CLOSE - horizon
        nearest = min(abs((s - target).total_seconds()) for s in seen)
        assert nearest <= 60, f"horizon T-{horizon} unsampled within a minute"


def test_the_t24_window_stays_wider_than_the_emit_tolerance() -> None:
    """The T-24h half-width was cut from 1h to 15 min on 2026-09-03 to keep the sweep
    inside its cadence. It must not be cut again below what the scorer needs.

    A horizon is represented by the nearest snapshot at or before it within five
    minutes (scripts/emit_baselines.py --tolerance-min). The dense window therefore has
    to open at least that far ahead of the horizon, or the horizon is unrepresentable
    and no error is raised anywhere -- the prediction is simply never emitted.
    """
    emit_tolerance = timedelta(minutes=5)
    t24_half_width = next(half for centre, half in WINDOWS if centre == timedelta(hours=24))
    assert t24_half_width >= emit_tolerance * 2, (
        f"T-24h half-width {t24_half_width} leaves too little margin over the "
        f"{emit_tolerance} emit tolerance"
    )
