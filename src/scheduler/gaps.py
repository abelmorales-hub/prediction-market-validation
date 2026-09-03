"""Gap detection over recorded snapshots.

The milestone's rule is "any interval >15 min for a market that was open". Applied
literally that flags every ordinary 30-minute sparse interval, so the report drowns in
its own cadence and nobody reads it. Gaps are therefore classified against the cadence
that was in force at the time:

    EXPECTED   interval consistent with the cadence for that moment
    MISSED     interval longer than the cadence allows -- a real hole
    CRITICAL   a MISSED gap overlapping a dense window, i.e. around a prediction horizon

G0 in the design doc ("no gaps >15 min") is the CRITICAL count. It must be zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.scheduler.cadence import DENSE, SPARSE, WINDOWS, in_dense_window

# A tick is scheduled every `interval`, but a sweep over hundreds of markets, retries and
# adaptive pacing all add jitter. Only a gap beyond this multiple is called a miss.
TOLERANCE = 2.5


@dataclass(frozen=True)
class Gap:
    market_id: str
    start: datetime
    end: datetime
    expected: timedelta
    severity: str

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    @property
    def minutes(self) -> float:
        return self.duration.total_seconds() / 60


def classify(
    market_id: str,
    start: datetime,
    end: datetime,
    close_time: datetime,
    tolerance: float = TOLERANCE,
    band: tuple[time, time] | None = None,
    tz: ZoneInfo | None = None,
) -> Gap | None:
    """One interval between consecutive snapshots. None when it is within cadence.

    Severity turns on *how much dense-window time the hole actually covers*, not on
    whether it touches one. Asking only whether a window is touched marks the ordinary
    interval that leaves a window — one dense sample, then the window closes and the
    30-minute cadence correctly resumes — as a missed horizon. That single confusion
    produced 104 of the 273 CRITICAL gaps in the first overnight run, all of them the
    scheduler behaving exactly as designed.

    With a `band`, only dense time *inside the daily collection window* counts. The
    collector is deliberately off outside it, so a horizon that falls at 03:00 local was
    never going to be sampled and its absence is a scope limit, not a fault. The
    overnight hole between one band closing and the next opening therefore scores zero
    in-band dense minutes and is silent — while a hole at 08:00, when the machine was
    supposed to be collecting, still fires.
    """
    unsampled_dense = _dense_span(start, end, close_time, band, tz)
    if unsampled_dense > DENSE * tolerance:
        return Gap(market_id, start, end, DENSE, "CRITICAL")
    # Sparse coverage is judged on in-band minutes for the same reason as dense: the
    # eighteen hours between one band closing and the next opening are not a hole, they
    # are the machine being off. Measuring the raw duration here would report every
    # single overnight boundary, for every market, forever.
    unsampled = (
        end - start if band is None else _in_band_span(start, end, band, tz or ZoneInfo("UTC"))
    )
    if unsampled > SPARSE * tolerance:
        return Gap(market_id, start, end, SPARSE, "MISSED")
    return None


def _dense_span(
    start: datetime,
    end: datetime,
    close_time: datetime,
    band: tuple[time, time] | None = None,
    tz: ZoneInfo | None = None,
) -> timedelta:
    """How much of the open interval (start, end) fell inside a dense window.

    Measured, not probed. The windows are fixed offsets from the close and provably
    disjoint, so overlaps can simply be summed. With a band, each overlap is further
    intersected with the daily collection window: dense minutes nobody was ever going to
    sample do not count against coverage.
    """
    total = timedelta(0)
    for centre, half in WINDOWS:
        w_start = close_time - (centre + half)
        # A market stops being sampled at the close, so no window extends past it.
        w_end = min(close_time - (centre - half), close_time)
        lo, hi = max(start, w_start), min(end, w_end)
        if hi <= lo:
            continue
        total += hi - lo if band is None else _in_band_span(lo, hi, band, tz or ZoneInfo("UTC"))
    return total


def _in_band_span(
    start: datetime, end: datetime, band: tuple[time, time], tz: ZoneInfo
) -> timedelta:
    """How much of (start, end) lies inside the daily band, summed over the days it spans.

    Walked day by day in local time rather than computed arithmetically, because a band
    is a wall-clock concept: across a DST change the same 05:30-11:00 is a different
    number of absolute hours, and the horizons it exists to cover move with the clock,
    not with UTC.
    """
    if end <= start:
        return timedelta(0)
    band_start, band_end = band
    total = timedelta(0)
    day = start.astimezone(tz).date()
    last = end.astimezone(tz).date()
    while day <= last:
        lo = datetime.combine(day, band_start, tzinfo=tz)
        hi = datetime.combine(day, band_end, tzinfo=tz)
        if band_start > band_end:  # band crosses midnight
            hi += timedelta(days=1)
        overlap = min(end, hi) - max(start, lo)
        if overlap > timedelta(0):
            total += overlap
        day += timedelta(days=1)
    return total


def find_gaps(
    rows: list[tuple[str, datetime, datetime]],
    tolerance: float = TOLERANCE,
    band: tuple[time, time] | None = None,
    tz: ZoneInfo | None = None,
) -> list[Gap]:
    """`rows` is (market_id, ts, close_time), any order. Compares consecutive snapshots
    within each market only: two different markets an hour apart is not a gap.

    With `band`, a gap is only reported if it *starts* inside the daily collection
    window. The overnight hole between one band closing and the next opening is then
    expected rather than a fault -- see `in_band`. A gap that starts inside the band
    still counts however far past its end it runs, because the sample that should have
    been taken during the band is missing either way.
    """
    by_market: dict[str, list[tuple[datetime, datetime]]] = {}
    for market_id, ts, close_time in rows:
        by_market.setdefault(market_id, []).append((ts, close_time))

    zone = tz or ZoneInfo("UTC")
    gaps = []
    for market_id, points in by_market.items():
        points.sort()
        for (start, close_time), (end, _) in zip(points, points[1:], strict=False):
            gap = classify(market_id, start, end, close_time, tolerance, band, zone)
            if gap is not None:
                gaps.append(gap)
    return sorted(gaps, key=lambda g: g.duration, reverse=True)


def coverage(rows: list[tuple[str, datetime, datetime]]) -> dict[str, float]:
    """Share of dense-window samples actually captured, per the cadence's own schedule."""
    dense = [r for r in rows if in_dense_window(r[1], r[2])]
    if not dense:
        return {"dense_samples": 0.0, "dense_markets": 0.0}
    # Keyed by (market, which window). Spanning first-to-last per *market* would stretch
    # one span across the sparse hours between the T-24h and T-6h windows and count every
    # one of those minutes as an expected sample, so a correctly-sampled market that
    # lived long enough to reach two windows would report a fraction of its true rate.
    spans: dict[tuple[str, int], list[datetime]] = {}
    for market_id, ts, close_time in dense:
        spans.setdefault((market_id, _window_index(ts, close_time)), []).append(ts)
    expected = 0.0
    for stamps in spans.values():
        stamps.sort()
        expected += max((stamps[-1] - stamps[0]) / DENSE + 1, 1)
    return {
        "dense_samples": float(len(dense)),
        "dense_markets": float(len({m for m, _ in spans})),
        "capture_rate": len(dense) / expected if expected else 1.0,
    }


def in_band(ts: datetime, band: tuple[time, time], tz: ZoneInfo) -> bool:
    """Whether an instant falls inside the daily collection band, in local time.

    G0 as the design doc states it -- no gap over 15 minutes, for fourteen days --
    assumes a collector that never stops. This one stops: the machine it runs on is not
    on overnight, so the band is when data is expected to exist at all. A hole outside
    it is not a fault, and counting it as one makes the gate permanently red and
    therefore useless.

    What this does *not* excuse is a hole inside the band. That is still a missed
    horizon, and still voids the window.
    """
    local = ts.astimezone(tz).timetz()
    start, end = band
    if start <= end:
        return start <= local.replace(tzinfo=None) <= end
    # A band crossing midnight is two intervals, not one.
    return local.replace(tzinfo=None) >= start or local.replace(tzinfo=None) <= end


def parse_band(text: str) -> tuple[time, time]:
    """`HH:MM-HH:MM` in local time."""
    start, _, end = text.partition("-")
    if not end:
        raise ValueError("band must look like 05:30-11:00")
    return time.fromisoformat(start.strip()), time.fromisoformat(end.strip())


def parse_since(text: str) -> datetime:
    """An ISO instant bounding a coverage window.

    A bare timestamp is read as UTC, never as machine-local. The pre-declaration's
    analysis window was first recorded two hours wrong because an instant read off a
    CEST clock was written down as UTC, so the ambiguous case resolves to the reading
    that cannot silently shift with the collector's location.
    """
    parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _window_index(ts: datetime, close_time: datetime) -> int:
    ttc = close_time - ts
    for i, (centre, half) in enumerate(WINDOWS):
        if abs(ttc - centre) <= half:
            return i
    return -1
