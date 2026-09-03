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
from datetime import UTC, datetime, timedelta

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
) -> Gap | None:
    """One interval between consecutive snapshots. None when it is within cadence.

    Severity turns on *how much dense-window time the hole actually covers*, not on
    whether it touches one. Asking only whether a window is touched marks the ordinary
    interval that leaves a window — one dense sample, then the window closes and the
    30-minute cadence correctly resumes — as a missed horizon. That single confusion
    produced 104 of the 273 CRITICAL gaps in the first overnight run, all of them the
    scheduler behaving exactly as designed.
    """
    unsampled_dense = _dense_span(start, end, close_time)
    if unsampled_dense > DENSE * tolerance:
        return Gap(market_id, start, end, DENSE, "CRITICAL")
    if (end - start) > SPARSE * tolerance:
        return Gap(market_id, start, end, SPARSE, "MISSED")
    return None


def _dense_span(start: datetime, end: datetime, close_time: datetime) -> timedelta:
    """How much of the open interval (start, end) fell inside a dense window.

    Measured, not probed. The windows are fixed offsets from the close and provably
    disjoint, so overlaps can simply be summed.
    """
    total = timedelta(0)
    for centre, half in WINDOWS:
        w_start = close_time - (centre + half)
        # A market stops being sampled at the close, so no window extends past it.
        w_end = min(close_time - (centre - half), close_time)
        overlap = min(end, w_end) - max(start, w_start)
        if overlap > timedelta(0):
            total += overlap
    return total


def find_gaps(
    rows: list[tuple[str, datetime, datetime]], tolerance: float = TOLERANCE
) -> list[Gap]:
    """`rows` is (market_id, ts, close_time), any order. Compares consecutive snapshots
    within each market only: two different markets an hour apart is not a gap."""
    by_market: dict[str, list[tuple[datetime, datetime]]] = {}
    for market_id, ts, close_time in rows:
        by_market.setdefault(market_id, []).append((ts, close_time))

    gaps = []
    for market_id, points in by_market.items():
        points.sort()
        for (start, close_time), (end, _) in zip(points, points[1:], strict=False):
            gap = classify(market_id, start, end, close_time, tolerance)
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
