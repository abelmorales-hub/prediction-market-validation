"""What temperature actually settled, read off the resolved ladder.

The follow-up study needs a target to fit a station-bias correction against, and every
obvious source is a proxy: an ERA5 archive is another grid cell with its own bias, and a
station observation feed is a *different* observation from the one the venue paid on. The
ladder is not a proxy. It is the settlement, expressed as inequalities, and intersecting
them recovers the number to within a degree or two with no new data source and no new
failure mode.

Each rung constrains the integer settlement temperature T (see `src.models.weather` for
the three shapes, verified live against a full ladder):

    between, floor..cap   YES -> floor <= T <= cap      NO -> T < floor or T > cap
    less,    cap          YES -> T <= cap - 1           NO -> T >= cap
    greater, floor        YES -> T >= floor + 1         NO -> T <= floor

Five of those six are intervals and tighten the bounds. The sixth -- a `between` that did
not pay -- is a *disjunction*, and is deliberately ignored rather than approximated:
folding it in either direction would silently move the answer, and a ladder almost always
carries enough of the other five to pin T anyway.

The result is an interval, never a point estimate, because that is what the data
supports. A caller wanting one number takes the midpoint and inherits a known uncertainty
of half the width -- small against a station bias of one to three degrees, and stated
rather than hidden.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from src.models.weather import BETWEEN, GREATER, LESS

# Fahrenheit, and wide enough to be unreachable: the coldest and hottest readings in the
# 20-station universe are nowhere near these. Bounds start here and only ever narrow, so
# an unconstrained day is recognisable by still being this wide rather than by a null.
T_MIN, T_MAX = -100, 150


@dataclass(frozen=True)
class Settlement:
    """The integer degrees F a day could have settled at, given every resolved rung."""

    low: int
    high: int
    rungs: int
    """How many rungs contributed a bound. Zero means the interval is the open default."""

    @property
    def width(self) -> int:
        return self.high - self.low + 1

    @property
    def midpoint(self) -> float:
        return (self.low + self.high) / 2.0

    @property
    def pinned(self) -> bool:
        """True when the ladder identifies the settlement to one or two degrees.

        Two is as good as one here: the rungs are two degrees wide, so a `between` that
        paid cannot do better, and demanding width 1 would discard most of the data to
        buy half a degree.
        """
        return self.rungs > 0 and self.width <= 2


def settlement_interval(rungs: Iterable[Mapping[str, Any]]) -> Settlement:
    """Intersect the constraints a day's resolved rungs place on its settlement.

    Each row needs `strike_type`, `outcome` (0 or 1), and whichever of `floor_strike` /
    `cap_strike` its shape uses. A row missing what its own shape requires is skipped: a
    rung we cannot read is one fewer constraint, never a guessed one.

    Contradictory rungs -- which would mean the venue settled a ladder inconsistently, or
    that two different days have been grouped together -- produce `low > high`. That is
    reported rather than repaired: it is a data bug, and a silently widened interval would
    feed the bias fit a wrong number with no symptom.
    """
    low, high, used = T_MIN, T_MAX, 0
    for row in rungs:
        shape = row.get("strike_type")
        outcome = row.get("outcome")
        if outcome not in (0, 1):
            continue
        yes = outcome == 1
        floor_s, cap_s = row.get("floor_strike"), row.get("cap_strike")

        if shape == BETWEEN:
            if yes and floor_s is not None and cap_s is not None:
                low, high, used = max(low, int(floor_s)), min(high, int(cap_s)), used + 1
            # A `between` that did not pay says "below the floor OR above the cap".
            # Not an interval; see the module docstring.
        elif shape == LESS and cap_s is not None:
            if yes:
                high, used = min(high, int(cap_s) - 1), used + 1
            else:
                low, used = max(low, int(cap_s)), used + 1
        elif shape == GREATER and floor_s is not None:
            if yes:
                low, used = max(low, int(floor_s) + 1), used + 1
            else:
                high, used = min(high, int(floor_s)), used + 1

    return Settlement(low=low, high=high, rungs=used)


def group_by_station_day(
    rows: Iterable[Mapping[str, Any]],
    series_prefix: str | None = None,
) -> dict[tuple[str, Any], list[Mapping[str, Any]]]:
    """Bucket resolved rungs into the ladders they belong to.

    Keyed on `(settlement_station, settles_on)` rather than on the event ticker, because a
    station and a target day are what identify a temperature, and two series naming the
    same quantity for the same day (`KXHIGHNY` and `KXHIGHTNY` both exist) should pool
    their constraints rather than be treated as separate observations. `settles_on` is
    also the only correct key for joining an ensemble: a daily temperature market stops
    trading just after local midnight the following day, so `end_date` names the day after
    the one being forecast.

    **`series_prefix` is not optional in practice, and leaving it off is a real bug.** A
    station-day carries a maximum ladder *and* a minimum one -- `KXHIGHTATL` beside
    `KXLOWTATL` -- and the key cannot tell them apart. Intersecting a max with a min
    produces an empty interval on every day that has both: 544 of 649 station-days did,
    which is how this was caught. Pass `"KXHIGH"` to get maxima, the same prefix the H2
    forecaster uses to decide what it can answer. The default is `None` only so that a
    caller handling one quantity already is not forced to restate it.
    """
    grouped: dict[tuple[str, Any], list[Mapping[str, Any]]] = {}
    for row in rows:
        station, day = row.get("settlement_station"), row.get("settles_on")
        if not station or day is None:
            continue
        if series_prefix and not str(row.get("series_id") or "").startswith(series_prefix):
            continue
        grouped.setdefault((station, day), []).append(row)
    return grouped
