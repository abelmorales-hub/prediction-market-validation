"""Pick the snapshot that stands for a prediction horizon.

The scorer compares a forecast against the market price *at the same instant* (P1). That
guarantee is only as good as this function: choose the snapshot badly and every number
downstream is quietly wrong while staying in a plausible range, which is exactly the
failure G2 exists to catch and exactly the one it cannot see.

So the rule is strict. A horizon is represented by the nearest snapshot **at or before**
it, within a tolerance, and by nothing otherwise. Never a later one — a snapshot after
T-1h has seen a minute of trading the forecast at T-1h had not, and scoring it as though
it were contemporaneous hands the model a free look at the future.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta

HORIZONS: tuple[tuple[str, timedelta], ...] = (
    ("T-24h", timedelta(hours=24)),
    ("T-6h", timedelta(hours=6)),
    ("T-1h", timedelta(hours=1)),
)

# The dense cadence is one minute, so a horizon with any coverage at all has a sample
# within a minute or two of it. Five minutes accepts ordinary jitter and still refuses a
# sparse-region sample standing in for a dense one.
TOLERANCE = timedelta(minutes=5)


@dataclass(frozen=True)
class HorizonPoint:
    market_id: str
    horizon_label: str
    target: datetime
    ts: datetime
    mid: float
    bid: float
    ask: float

    @property
    def lag(self) -> timedelta:
        """How far before the horizon the snapshot actually sits."""
        return self.target - self.ts


def pick(
    rows: list[tuple[datetime, float, float, float]],
    target: datetime,
    tolerance: timedelta = TOLERANCE,
) -> tuple[datetime, float, float, float] | None:
    """Nearest row at or before `target`, or None if none is within `tolerance`.

    `rows` must be sorted by timestamp.
    """
    if not rows:
        return None
    idx = bisect_right([r[0] for r in rows], target)
    if idx == 0:
        return None
    row = rows[idx - 1]
    return row if target - row[0] <= tolerance else None


def horizon_points(
    market_id: str,
    close_time: datetime,
    rows: list[tuple[datetime, float, float, float]],
    tolerance: timedelta = TOLERANCE,
) -> list[HorizonPoint]:
    """One point per horizon that has usable coverage. Missing horizons are absent, not
    filled: a horizon we failed to observe is missing evidence, not a neutral prior."""
    ordered = sorted(rows, key=lambda r: r[0])
    out = []
    for label, offset in HORIZONS:
        target = close_time - offset
        got = pick(ordered, target, tolerance)
        if got is None:
            continue
        ts, mid, bid, ask = got
        out.append(HorizonPoint(market_id, label, target, ts, mid, bid, ask))
    return out
