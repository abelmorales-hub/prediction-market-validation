"""Parsing and validating the ensemble feed. No network.

The failures guarded against here are all silent: a short run averaged as if complete,
a control member dropped, a UTC-day boundary applied to a market that settles on the
local day.
"""

from datetime import UTC, date, datetime

import pytest

from src.ingest.gefs import MIN_MEMBERS, EnsembleDay, _local_today, _members, _parse
from src.ingest.stations import STATIONS, station_for

CHI = STATIONS["CLIMDW"]
NOW = datetime(2026, 9, 3, 16, 0, tzinfo=UTC)


def _body(days: list[str], n_members: int = 31, offset_s: int = -18000) -> dict:
    daily: dict = {"time": days}
    for prefix in ("temperature_2m_max", "temperature_2m_min"):
        base = 90.0 if prefix.endswith("max") else 70.0
        daily[prefix] = [base] * len(days)  # control run, unsuffixed
        for i in range(1, n_members):
            daily[f"{prefix}_member{i:02d}"] = [base + i * 0.1] * len(days)
    return {
        "latitude": 41.75,
        "longitude": -87.75,
        "elevation": 184.0,
        "utc_offset_seconds": offset_s,
        "daily": daily,
    }


def test_the_control_run_counts_as_a_member() -> None:
    """The unsuffixed key is the control run. Dropping it turns 31 members into 30
    without anything looking wrong."""
    daily = _body(["2026-09-04"])["daily"]
    assert len(_members(daily, "temperature_2m_max")) == 31


def test_a_full_response_parses_into_days() -> None:
    f = _parse(_body(["2026-09-03", "2026-09-04"]), CHI, "gfs025", NOW)
    assert len(f.days) == 2
    assert f.days[0].members == 31
    assert f.grid_elevation_m == 184.0
    assert f.station_code == "CLIMDW"


def test_a_truncated_run_is_refused_not_averaged() -> None:
    with pytest.raises(ValueError, match="below the"):
        _parse(_body(["2026-09-04"], n_members=5), CHI, "gfs025", NOW)


def test_an_api_error_body_raises() -> None:
    with pytest.raises(ValueError, match="ensemble API error"):
        _parse({"error": True, "reason": "bad model"}, CHI, "gfs025", NOW)


def test_a_response_without_a_daily_block_raises() -> None:
    with pytest.raises(ValueError, match="no daily block"):
        _parse({"latitude": 1.0}, CHI, "gfs025", NOW)


def test_a_day_already_under_way_is_not_a_pure_forecast() -> None:
    """Local today at Chicago is 2026-09-03 when it is 16:00 UTC. Its max may already
    be partly observed, so it must not be counted as a T-24h forecast."""
    f = _parse(_body(["2026-09-03", "2026-09-04"]), CHI, "gfs025", NOW)
    by_date = {d.target_date: d for d in f.days}
    assert by_date[date(2026, 9, 3)].is_pure_forecast is False
    assert by_date[date(2026, 9, 4)].is_pure_forecast is True


def test_local_today_uses_the_station_offset_not_the_machine() -> None:
    """The collector runs in Europe and every station is in North America; a machine-
    local date would be a day ahead for most of the evening."""
    # 03:00 UTC is still the previous day at a UTC-5 station.
    at = datetime(2026, 9, 4, 3, 0, tzinfo=UTC)
    assert _local_today({"utc_offset_seconds": -18000}, at) == date(2026, 9, 3)
    assert _local_today({"utc_offset_seconds": 0}, at) == date(2026, 9, 4)


def test_local_today_reads_the_given_instant_not_the_wall_clock() -> None:
    """Regression: it called datetime.now() and ignored the instant it was handed.

    Two failures in one. The unit tests below passed the day they were written and
    failed the next morning, because `is_pure_forecast` was computed against the
    real date instead of the record's. And since `fetched_at` is stamped *before*
    the HTTP request, a fetch straddling local midnight stored a timestamp from one
    day next to a flag computed for the next -- a point-in-time row contradicting
    itself, which is the one thing this table exists to prevent.
    """
    body = {"utc_offset_seconds": 0}
    assert _local_today(body, datetime(2001, 1, 1, 12, 0, tzinfo=UTC)) == date(2001, 1, 1)
    assert _local_today(body, datetime(2099, 12, 31, 12, 0, tzinfo=UTC)) == date(2099, 12, 31)


def test_payload_keeps_every_member() -> None:
    """A mean and a spread cannot reconstruct the probability of a 2-degree bucket."""
    f = _parse(_body(["2026-09-04"]), CHI, "gfs025", NOW)
    payload = f.payload_for(date(2026, 9, 4))
    assert payload is not None
    assert len(payload["tmax_f"]) == 31
    assert payload["station"] == "CLIMDW"
    assert payload["is_pure_forecast"] is True
    assert payload["grid"]["elevation_m"] == 184.0


def test_payload_for_a_date_not_in_the_run_is_none() -> None:
    f = _parse(_body(["2026-09-04"]), CHI, "gfs025", NOW)
    assert f.payload_for(date(2030, 1, 1)) is None


def test_min_members_floor_is_below_a_full_run() -> None:
    assert MIN_MEMBERS < 31


def test_every_station_has_a_plausible_north_american_position() -> None:
    for code, s in STATIONS.items():
        assert 24.0 < s.latitude < 49.0, code
        assert -125.0 < s.longitude < -66.0, code
        assert s.timezone.startswith("America/"), code


def test_unknown_station_returns_none_rather_than_guessing() -> None:
    assert station_for("CLIXXX") is None
    assert station_for("climdw") is STATIONS["CLIMDW"]


def test_the_three_stations_that_are_not_the_obvious_airport() -> None:
    """Chicago is Midway, Houston is Hobby, New York is Central Park. Each is a
    different place from the airport a careless reading would pick, by enough degrees
    to move a bucket."""
    assert STATIONS["CLIMDW"].name.endswith("Midway")
    assert STATIONS["CLIHOU"].name.endswith("Hobby")
    assert STATIONS["CLINYC"].name.endswith("Central Park")


def test_ensemble_day_member_count_is_the_max_series() -> None:
    day = EnsembleDay(date(2026, 9, 4), (90.0, 91.0), (70.0, 71.0), True)
    assert day.members == 2
