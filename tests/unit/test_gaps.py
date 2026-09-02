"""Gap classification.

The trap this guards against: applying ">15 min" literally flags every ordinary
30-minute sparse interval, the report fills with thousands of false positives, and the
one gap that matters — a hole across a prediction horizon — is invisible in the noise.
"""

from datetime import UTC, datetime, timedelta

from src.scheduler.gaps import classify, coverage, find_gaps

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
    gap = classify("A", at(hours=25, minutes=20), at(hours=24, minutes=30), CLOSE)
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
