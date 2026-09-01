"""When to snapshot a market's book. Pure functions of (now, close_time).

Dense sampling brackets the three prediction horizons (T-24h, T-6h, T-1h) because those
are the instants the scorer compares against. Everywhere else is background coverage.

    T-24h ± 1h      -> 1 min
    T-6h  ± 30 min  -> 1 min
    T-1h  ± 15 min  -> 1 min
    otherwise       -> 30 min
"""

from __future__ import annotations

from datetime import datetime, timedelta

DENSE = timedelta(minutes=1)
SPARSE = timedelta(minutes=30)

# (centre before close, half-width)
WINDOWS: tuple[tuple[timedelta, timedelta], ...] = (
    (timedelta(hours=24), timedelta(hours=1)),
    (timedelta(hours=6), timedelta(minutes=30)),
    (timedelta(hours=1), timedelta(minutes=15)),
)


def time_to_close(now: datetime, close_time: datetime) -> timedelta:
    return close_time - now


def in_dense_window(now: datetime, close_time: datetime) -> bool:
    """Inclusive at both edges: exactly T-25h is inside the T-24h window."""
    ttc = time_to_close(now, close_time)
    if ttc <= timedelta(0):
        return False
    return any(abs(ttc - centre) <= half for centre, half in WINDOWS)


def interval_at(now: datetime, close_time: datetime) -> timedelta:
    return DENSE if in_dense_window(now, close_time) else SPARSE


def dense_window_starts(close_time: datetime) -> list[datetime]:
    """Absolute instants at which each dense window opens, earliest first."""
    return sorted(close_time - (centre + half) for centre, half in WINDOWS)


def next_snapshot_at(now: datetime, close_time: datetime) -> datetime | None:
    """Next instant to sample, or None once the market has closed.

    A sparse step is clamped to the opening of a dense window it would otherwise jump
    over. Without that, a 30-minute step starting 29 minutes before T-25h lands well
    inside the window and the first minutes of it — including the T-24h horizon on a
    bad alignment — are never sampled.
    """
    if now >= close_time:
        return None
    candidate = now + interval_at(now, close_time)
    if not in_dense_window(now, close_time):
        upcoming = [s for s in dense_window_starts(close_time) if now < s < candidate]
        if upcoming:
            return min(upcoming)
    return min(candidate, close_time)
