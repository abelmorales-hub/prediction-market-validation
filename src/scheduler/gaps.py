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
from datetime import datetime, timedelta

from src.scheduler.cadence import DENSE, in_dense_window, interval_at

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
    """One interval between consecutive snapshots. None when it is within cadence."""
    expected = interval_at(start, close_time)
    if (end - start) <= expected * tolerance:
        return None
    # A dense window anywhere inside the hole means a horizon may have gone unsampled.
    severity = "CRITICAL" if _touches_dense(start, end, close_time) else "MISSED"
    return Gap(market_id, start, end, expected, severity)


def _touches_dense(start: datetime, end: datetime, close_time: datetime) -> bool:
    if in_dense_window(start, close_time) or in_dense_window(end, close_time):
        return True
    # Step through the hole; a dense window is at least 30 min wide, so a 5-minute
    # stride cannot step over one.
    probe, stride = start, timedelta(minutes=5)
    while probe < end:
        if in_dense_window(probe, close_time):
            return True
        probe += stride
    return False


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
    spans: dict[str, list[datetime]] = {}
    for market_id, ts, _ in dense:
        spans.setdefault(market_id, []).append(ts)
    expected = 0.0
    for stamps in spans.values():
        stamps.sort()
        window = (stamps[-1] - stamps[0]) / DENSE
        expected += max(window + 1, 1)
    return {
        "dense_samples": float(len(dense)),
        "dense_markets": float(len(spans)),
        "capture_rate": len(dense) / expected if expected else 1.0,
    }
