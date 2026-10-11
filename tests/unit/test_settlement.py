"""Recovering the settled temperature from a ladder, including the ways it can lie."""

from datetime import date
from typing import Any

from src.analysis.settlement import (
    T_MAX,
    T_MIN,
    group_by_station_day,
    settlement_interval,
)


def rung(
    shape: str, outcome: int | None, floor: float | None = None, cap: float | None = None
) -> dict[str, Any]:
    return {"strike_type": shape, "outcome": outcome, "floor_strike": floor, "cap_strike": cap}


def ladder(settled: int) -> list[dict[str, Any]]:
    """A real KXHIGH shape: one `less` tail, three `between` buckets, one `greater` tail,
    with the strikes as the venue publishes them rather than parsed from a title."""
    rows = [
        rung("less", int(settled < 90), cap=90.0),
        rung("greater", int(settled > 95), floor=95.0),
    ]
    for lo, hi in ((90, 91), (92, 93), (94, 95)):
        rows.append(rung("between", int(lo <= settled <= hi), floor=float(lo), cap=float(hi)))
    return rows


def test_a_full_ladder_pins_the_settlement_to_the_paying_bucket() -> None:
    s = settlement_interval(ladder(92))
    assert (s.low, s.high) == (92, 93)
    assert s.pinned and s.width == 2


def test_the_tails_alone_still_bound_it() -> None:
    """Only liquid rungs are tracked (volume >= 1,000), so the bucket that paid is often
    missing. The two tails that did not pay still say where the day was."""
    s = settlement_interval([rung("less", 0, cap=90.0), rung("greater", 0, floor=95.0)])
    assert (s.low, s.high) == (90, 95)
    assert not s.pinned, "six degrees is a bound, not an answer, and must not be fitted on"


def test_a_settlement_in_the_upper_tail_is_half_open_but_still_bounded_below() -> None:
    s = settlement_interval([rung("greater", 1, floor=95.0), rung("less", 0, cap=90.0)])
    assert s.low == 96 and s.high == T_MAX
    assert not s.pinned


def test_a_between_that_did_not_pay_is_ignored_rather_than_approximated() -> None:
    """It means "below the floor OR above the cap". Folding that into an interval either
    way moves the answer silently, which is worse than one fewer constraint."""
    s = settlement_interval([rung("between", 0, floor=90.0, cap=91.0)])
    assert (s.low, s.high, s.rungs) == (T_MIN, T_MAX, 0)


def test_an_unreadable_rung_is_skipped_not_guessed() -> None:
    cases: list[list[dict[str, Any]]] = [
        [rung("between", 1), rung("less", 1), rung("greater", 1)],  # bounds absent
        [{"strike_type": "parity", "outcome": 1, "floor_strike": 90.0}],  # shape unknown
        [rung("between", None, floor=90.0, cap=91.0)],  # not settled yet
    ]
    for rows in cases:
        assert settlement_interval(rows).rungs == 0


def test_a_contradictory_ladder_is_reported_not_repaired() -> None:
    """Two rungs that cannot both be true means the venue settled inconsistently, or that
    two days were grouped as one. A widened interval would feed the fit a wrong number
    with no symptom; an impossible one is checkable."""
    s = settlement_interval([rung("less", 1, cap=90.0), rung("greater", 1, floor=95.0)])
    assert s.low > s.high


def test_a_minimum_ladder_must_not_be_intersected_with_the_maximum_one() -> None:
    """Regression, and it cost a whole fitting run. A station-day carries KXLOWTATL beside
    KXHIGHTATL -- the day's low and the day's high -- and they share the (station, day)
    key. Intersected, they produce an impossible interval; 379 of 649 ladders did. The
    contradiction check caught it, which is the only reason it is a test and not a set of
    plausible offsets."""
    day = date(2026, 9, 5)
    rows: list[dict[str, Any]] = [
        {"series_id": "KXHIGHTATL", "settlement_station": "CLIATL", "settles_on": day,
         "strike_type": "between", "floor_strike": 97.0, "cap_strike": 98.0, "outcome": 1},
        {"series_id": "KXLOWTATL", "settlement_station": "CLIATL", "settles_on": day,
         "strike_type": "between", "floor_strike": 73.0, "cap_strike": 74.0, "outcome": 1},
    ]

    merged = settlement_interval(group_by_station_day(rows)[("CLIATL", day)])
    assert merged.low > merged.high, "a max and a min cannot both be the settlement"

    highs = group_by_station_day(rows, series_prefix="KXHIGH")[("CLIATL", day)]
    pinned = settlement_interval(highs)
    assert (pinned.low, pinned.high) == (97, 98) and pinned.pinned


def test_rungs_group_by_station_and_target_day_not_by_ticker() -> None:
    rows: list[dict[str, Any]] = [
        {"settlement_station": "CLINYC", "settles_on": date(2026, 11, 2)},
        {"settlement_station": "CLINYC", "settles_on": date(2026, 11, 3)},
        {"settlement_station": "CLIDEN", "settles_on": date(2026, 11, 2)},
        {"settlement_station": None, "settles_on": date(2026, 11, 2)},
        {"settlement_station": "CLIDEN", "settles_on": None},
    ]
    assert set(group_by_station_day(rows)) == {
        ("CLINYC", date(2026, 11, 2)),
        ("CLINYC", date(2026, 11, 3)),
        ("CLIDEN", date(2026, 11, 2)),
    }, "a row with no station or no target day identifies no temperature"
