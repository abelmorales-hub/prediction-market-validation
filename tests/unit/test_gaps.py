"""Gap classification.

The trap this guards against: applying ">15 min" literally flags every ordinary
30-minute sparse interval, the report fills with thousands of false positives, and the
one gap that matters — a hole across a prediction horizon — is invisible in the noise.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.scheduler.gaps import (
    BandDay,
    band_days,
    classify,
    coverage,
    find_gaps,
    g0_status,
    in_band,
    parse_band,
    parse_since,
)

CLOSE = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def at(**kw: float) -> datetime:
    return CLOSE - timedelta(**kw)


def test_ordinary_sparse_interval_is_not_a_gap() -> None:
    """30 minutes apart in the sparse region is the cadence working, not a hole."""
    assert classify("A", at(hours=48), at(hours=47, minutes=30), CLOSE) is None


def test_ordinary_dense_interval_is_not_a_gap() -> None:
    assert classify("A", at(hours=24), at(hours=23, minutes=59), CLOSE) is None


def test_long_hole_in_the_sparse_region_is_missed_not_critical() -> None:
    gap = classify("A", at(hours=48), at(hours=44), CLOSE)
    assert gap is not None
    assert gap.severity == "MISSED"
    assert gap.minutes == 240


def test_hole_inside_a_dense_window_is_critical() -> None:
    """Sixteen minutes at T-24h is under any sparse threshold but skips the horizon."""
    gap = classify("A", at(hours=24, minutes=8), at(hours=23, minutes=52), CLOSE)
    assert gap is not None
    assert gap.severity == "CRITICAL"
    assert gap.expected == timedelta(minutes=1)


def test_hole_spanning_a_dense_window_is_critical_even_if_both_ends_are_sparse() -> None:
    """Starts before the window opens, ends after it closes. Neither endpoint is dense,
    but the entire T-24h horizon went unsampled."""
    gap = classify("A", at(hours=26), at(hours=22), CLOSE)
    assert gap is not None
    assert gap.severity == "CRITICAL"


def test_sparse_hole_that_touches_no_window_is_only_missed() -> None:
    gap = classify("A", at(hours=20), at(hours=16), CLOSE)
    assert gap is not None
    assert gap.severity == "MISSED"


def test_tolerance_absorbs_jitter() -> None:
    """A sweep over hundreds of markets drifts. 70 s in a dense window is not a hole."""
    assert classify("A", at(hours=24), at(hours=24) + timedelta(seconds=70), CLOSE) is None
    assert classify("A", at(hours=24), at(hours=24) + timedelta(seconds=200), CLOSE) is not None


def test_gaps_are_per_market() -> None:
    rows = [
        ("A", at(hours=48), CLOSE),
        ("B", at(hours=40), CLOSE),
        ("A", at(hours=47, minutes=30), CLOSE),
    ]
    assert find_gaps(rows) == [], "two markets far apart is not a gap in either"


def test_find_gaps_orders_worst_first() -> None:
    rows = [
        ("A", at(hours=48), CLOSE),
        ("A", at(hours=44), CLOSE),  # 4h hole
        ("A", at(hours=41), CLOSE),  # 3h hole
        ("A", at(hours=40), CLOSE),  # 1h: inside 30min x 2.5 tolerance, not a gap
    ]
    found = find_gaps(rows)
    assert [round(g.minutes) for g in found] == [240, 180]


def test_unsorted_input_is_handled() -> None:
    rows = [("A", at(hours=44), CLOSE), ("A", at(hours=48), CLOSE)]
    found = find_gaps(rows)
    assert len(found) == 1 and round(found[0].minutes) == 240


def test_single_snapshot_has_no_intervals() -> None:
    assert find_gaps([("A", at(hours=48), CLOSE)]) == []


def test_leaving_a_dense_window_is_not_a_critical_gap() -> None:
    """Regression, first overnight run. Last dense sample just before the window closes,
    then the 30-minute cadence correctly resumes. Judging the whole interval against the
    1-minute cadence because it *starts* inside the window marked 104 correct intervals
    as missed horizons."""
    gap = classify("A", at(hours=23, minutes=1), at(hours=22, minutes=30), CLOSE)
    assert gap is None, "the window closed 1 min in; the rest is ordinary sparse cadence"


def test_entering_a_dense_window_late_is_still_critical() -> None:
    """The mirror case must keep firing: a hole that runs well into a window."""
    gap = classify("A", at(hours=24, minutes=35), at(hours=24, minutes=5), CLOSE)
    assert gap is not None and gap.severity == "CRITICAL"


def test_severity_tracks_dense_minutes_lost_not_mere_contact() -> None:
    inside = classify("A", at(hours=24), at(hours=23, minutes=56), CLOSE)
    assert inside is not None and inside.severity == "CRITICAL", "4 dense min lost"
    grazing = classify("A", at(hours=23, minutes=0, seconds=30), at(hours=22), CLOSE)
    assert grazing is None, "30 s of dense time lost is within tolerance"


def test_coverage_is_per_window_not_per_market() -> None:
    """A market sampled cleanly in two separate windows must not be charged for the
    sparse hours between them."""
    rows = [("A", at(hours=24) + timedelta(minutes=i), CLOSE) for i in range(-2, 3)]
    rows += [("A", at(hours=6) + timedelta(minutes=i), CLOSE) for i in range(-2, 3)]
    stats = coverage(rows)
    assert stats["dense_samples"] == 10
    assert stats["dense_markets"] == 1
    assert stats["capture_rate"] == 1.0, "both windows fully sampled"


def test_coverage_counts_only_dense_samples() -> None:
    rows = [("A", at(hours=48), CLOSE)] + [
        ("A", at(hours=24) + timedelta(minutes=i), CLOSE) for i in range(-3, 4)
    ]
    stats = coverage(rows)
    assert stats["dense_samples"] == 7, "the sparse sample is not counted"
    assert stats["capture_rate"] == 1.0, "every scheduled minute present"


def test_coverage_falls_when_minutes_are_missing() -> None:
    rows = [("A", at(hours=24) + timedelta(minutes=i), CLOSE) for i in (0, 1, 2, 8, 9)]
    assert coverage(rows)["capture_rate"] < 0.6


# ---------------------------------------------------------------- --since parsing


def test_a_bare_timestamp_is_utc_not_machine_local() -> None:
    """The exact bug that put the pre-declaration's analysis window two hours in the
    future: an instant read off a CEST clock, written down as if it were UTC."""
    assert parse_since("2026-09-03T17:07").tzinfo is UTC
    assert parse_since("2026-09-03T17:07").hour == 17


def test_an_explicit_offset_is_respected() -> None:
    naive = parse_since("2026-09-03T19:07:40+02:00")
    assert naive.astimezone(UTC).hour == 17


def test_z_suffix_parses() -> None:
    assert parse_since("2026-09-03T17:07:40Z").astimezone(UTC).hour == 17


def test_a_non_instant_raises_rather_than_defaulting() -> None:
    with pytest.raises(ValueError):
        parse_since("last tuesday")


# ---------------------------------------------------------------- collection band


MADRID = ZoneInfo("Europe/Madrid")
BAND = (time(5, 30), time(11, 0))


def test_instants_inside_and_outside_the_band() -> None:
    assert in_band(datetime(2026, 9, 4, 6, 0, tzinfo=MADRID), BAND, MADRID)
    assert in_band(datetime(2026, 9, 4, 10, 59, tzinfo=MADRID), BAND, MADRID)
    assert not in_band(datetime(2026, 9, 4, 3, 0, tzinfo=MADRID), BAND, MADRID)
    assert not in_band(datetime(2026, 9, 4, 23, 0, tzinfo=MADRID), BAND, MADRID)


def test_the_band_is_evaluated_in_local_time_not_utc() -> None:
    """07:00 Madrid is 05:00 UTC. Judged in UTC it would fall outside a 05:30 start,
    and every East Coast horizon would be discarded as out-of-band."""
    seven_local = datetime(2026, 9, 4, 5, 0, tzinfo=UTC)  # 07:00 CEST
    assert in_band(seven_local, BAND, MADRID)


def test_the_overnight_hole_is_not_counted_as_a_gap() -> None:
    """The collector is not running overnight by design. Counting that as a fault makes
    the gate permanently red, and a gate nobody can pass is a gate nobody reads."""
    close = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    last_of_band = datetime(2026, 9, 4, 10, 58, tzinfo=MADRID)  # band closes at 11:00
    first_of_next = datetime(2026, 9, 5, 5, 32, tzinfo=MADRID)  # band reopens at 05:30
    rows = [("A", last_of_band, close), ("A", first_of_next, close)]
    assert find_gaps(rows, band=BAND, tz=MADRID) == []
    assert find_gaps(rows) != [], "without a band the same 18 hours is reported"


def test_stopping_early_inside_the_band_is_still_reported() -> None:
    """The band excuses the hours the collector is off by design, not an early finish.
    Last sample at 09:00 with the band open until 11:00 is two hours that should have
    been collected."""
    close = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    rows = [
        ("A", datetime(2026, 9, 4, 9, 0, tzinfo=MADRID), close),
        ("A", datetime(2026, 9, 5, 5, 32, tzinfo=MADRID), close),
    ]
    assert find_gaps(rows, band=BAND, tz=MADRID) != []


def test_a_hole_inside_the_band_is_still_a_gap() -> None:
    """The band excuses being switched off, not missing a horizon while switched on."""
    close = datetime(2026, 9, 5, 8, 0, tzinfo=MADRID)  # T-24h horizon at 08:00 the 4th
    rows = [
        ("A", datetime(2026, 9, 4, 7, 40, tzinfo=MADRID), close),
        ("A", datetime(2026, 9, 4, 8, 20, tzinfo=MADRID), close),
    ]
    gaps = find_gaps(rows, band=BAND, tz=MADRID)
    assert gaps and gaps[0].severity == "CRITICAL"


def test_parse_band_round_trip() -> None:
    assert parse_band("05:30-11:00") == (time(5, 30), time(11, 0))
    with pytest.raises(ValueError):
        parse_band("05:30")


# ------------------------------------------------- band-day ledger (Amendment 10)


OPERATIVE = (time(5, 30), time(11, 30))


def local(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=MADRID)


def sampled(day: date) -> tuple[str, datetime, datetime]:
    """One in-band sample of that day's own market. Each band-day tracks a different set
    of markets -- they close within 30 h of admission -- which is exactly why a lost day
    leaves no consecutive pair for `find_gaps` to measure."""
    ts = local(day, 8)
    return (f"M-{day}", ts, ts + timedelta(hours=20))


def ledger_of(pattern: str) -> list[BandDay]:
    """`.` a clean day, `x` a lost one, one character per day from 2026-09-05."""
    start = date(2026, 9, 5)
    return [BandDay(start + timedelta(days=i), 1 if c == "." else 0) for i, c in enumerate(pattern)]


def test_a_day_with_no_in_band_snapshot_is_lost() -> None:
    """Regression, 2026-09-13. The collector wrote nothing for a whole band and
    `find_gaps` saw nothing wrong, because a gap needs two consecutive snapshots and
    there were none. The ledger counts days that should exist instead."""
    days = [date(2026, 9, 11), date(2026, 9, 12), date(2026, 9, 13)]
    rows = [sampled(d) for d in days[:2]]
    got = band_days(rows, local(days[0], 0), local(days[-1], 12), OPERATIVE, MADRID)
    assert [d.day for d in got] == days
    assert [d.clean for d in got] == [True, True, False]
    assert find_gaps(rows, band=OPERATIVE, tz=MADRID) == [], "the old check stays silent"


def test_a_band_still_open_is_not_judged_lost() -> None:
    """Run mid-morning, today has not had its chance yet. Calling it lost would make the
    gate fail every time it is run before 11:30."""
    day = date(2026, 9, 13)
    assert band_days([], local(day, 0), local(day, 8), OPERATIVE, MADRID) == []


def test_a_band_that_opened_before_the_window_is_not_judged() -> None:
    """`--since` lands mid-band: those hours were never this window's to collect."""
    day = date(2026, 9, 5)
    assert band_days([], local(day, 9), local(day, 23), OPERATIVE, MADRID) == []


def test_one_lost_day_does_not_restart_the_clock() -> None:
    """The whole point of Amendment 10: a lost day costs a day, not the window."""
    status = g0_status(ledger_of("........x"))
    assert status.restarted_on is None
    assert status.clean == 8, "the lost day buys no credit"
    assert status.elapsed == 9
    assert not status.met


def test_two_consecutive_lost_days_restart_the_clock() -> None:
    """Not recovering unattended is the operational failure G0 exists to catch."""
    status = g0_status(ledger_of("........xx..."))
    assert status.restarted_on == date(2026, 9, 14)
    assert status.clean == 3, "counting resumes after the restart, not before"


def test_fourteen_clean_days_around_one_lost_day_meets_g0() -> None:
    status = g0_status(ledger_of("........x" + "." * 6))
    assert status.met
    assert status.completed_on == date(2026, 9, 19), "one day later than an unbroken run"


def test_the_span_expiring_short_of_fourteen_restarts_the_clock() -> None:
    """Alternating loss never puts two lost days together, but never accrues fourteen
    either. Without the span the counter would crawl forward for ever."""
    status = g0_status(ledger_of(".x" * 9))
    assert status.restarted_on is not None
    assert not status.met
