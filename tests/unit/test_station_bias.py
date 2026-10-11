"""H3: the correction is applied, the point-in-time rule is inherited, and the look is
still withheld from anyone who did not ask for it."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from src.analysis.frame import FROZEN_MODELS
from src.analysis.horizons import HorizonPoint
from src.analysis.station_bias import MODEL_VERSION, StationBiasForecaster
from src.models.station_bias import OFFSET_F, POOLED_OFFSET_F, offset_for
from src.models.weather import ensemble_probability

TS = datetime(2026, 11, 2, 5, 0, tzinfo=UTC)
DAY = date(2026, 11, 3)
MEMBERS = [70.0, 71.0, 71.4, 72.0, 72.6, 73.0, 74.0]


class FakeRepo:
    """Only the one method the forecaster is allowed to reach the database through."""

    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.calls: list[tuple[str, date, datetime]] = []

    def latest_ensemble_before(
        self, station: str, target_date: date, before: datetime
    ) -> dict[str, Any] | None:
        self.calls.append((station, target_date, before))
        return self.row


def ensemble(members: list[float] | None = None) -> dict[str, Any]:
    return {
        "station": "CLIPHX",
        "target_date": DAY,
        "fetched_at": datetime(2026, 11, 2, 4, 0, tzinfo=UTC),
        "is_pure_forecast": False,
        "tmax_f": list(members if members is not None else MEMBERS),
    }


def meta(**over: Any) -> dict[str, dict[str, Any]]:
    row: dict[str, Any] = {
        "series_id": "KXHIGHTPHX",
        "strike_type": "between",
        "floor_strike": 74.0,
        "cap_strike": 75.0,
        "settlement_station": "CLIPHX",
        "settles_on": DAY,
    }
    row.update(over)
    return {"M1": row}


def point() -> HorizonPoint:
    return HorizonPoint(
        market_id="M1",
        horizon_label="T-24h",
        target=TS + timedelta(minutes=3),
        ts=TS,
        mid=0.4,
        bid=0.39,
        ask=0.41,
    )


def test_the_offset_moves_the_members_and_therefore_the_probability() -> None:
    """Phoenix settles ~3.5F hotter than its grid cell, so members that miss the 74-75
    bucket land in it once corrected. This is the whole hypothesis in one assert."""
    repo = FakeRepo(ensemble())
    out = StationBiasForecaster(repo, meta())(point())  # type: ignore[arg-type]

    raw = ensemble_probability(MEMBERS, "between", 74.0, 75.0)
    shifted = [v + offset_for("CLIPHX") for v in MEMBERS]
    assert out[MODEL_VERSION].p_hat == ensemble_probability(shifted, "between", 74.0, 75.0)
    assert out[MODEL_VERSION].p_hat != raw, "a correction that changes nothing is not one"


def test_the_members_move_rather_than_the_strikes() -> None:
    """Shifting floor/cap by -offset is equivalent only for a whole-degree offset, because
    settlement is an integer and members are rounded half-up first. These offsets are
    tenths, so the two are not interchangeable, and the members are what move."""
    offset = offset_for("CLIPHX")
    assert offset != round(offset), "pick a station with a fractional offset if this fires"
    by_members = ensemble_probability([v + offset for v in MEMBERS], "between", 74.0, 75.0)
    by_strikes = ensemble_probability(MEMBERS, "between", 74.0 - offset, 75.0 - offset)
    assert by_members != by_strikes


def test_the_forecast_is_asked_for_the_ensemble_as_of_the_snapshot_not_now() -> None:
    """Inherited from the frozen parent, and the single place a look-ahead leak could
    enter. Asserted here because inheriting it is the claim being made."""
    repo = FakeRepo(ensemble())
    StationBiasForecaster(repo, meta())(point())  # type: ignore[arg-type]
    assert repo.calls == [("CLIPHX", DAY, TS)]


def test_every_refusal_the_parent_counts_is_still_counted() -> None:
    cases: dict[str, tuple[dict[str, Any], dict[str, Any] | None]] = {
        "h2_not_catalogued": ({}, ensemble()),
        "h2_unsupported_series": (meta(series_id="KXLOWTPHX"), ensemble()),
        "h2_no_strike": (meta(strike_type=None), ensemble()),
        "h2_no_station_or_day": (meta(settlement_station=None), ensemble()),
        "h2_no_ensemble": (meta(), None),
        "h2_bad_rung": (meta(strike_type="parity"), ensemble()),
    }
    for counter, (m, row) in cases.items():
        fc = StationBiasForecaster(FakeRepo(row), m)  # type: ignore[arg-type]
        assert fc(point()) == {}, counter
        assert fc.stats.as_dict()[counter] == 1, counter
        assert fc.stats.as_dict()["h2_forecast"] == 0, counter


def test_an_unfitted_station_gets_the_pooled_offset_rather_than_a_crash() -> None:
    """New Orleans was added to the universe mid-study once. A KeyError here would cost a
    whole station its rows, silently, for as long as nobody read the counters."""
    assert "CLINOPE" not in OFFSET_F
    assert offset_for("CLINOPE") == POOLED_OFFSET_F


def test_h3_is_withheld_from_a_run_that_did_not_name_it() -> None:
    assert MODEL_VERSION in FROZEN_MODELS
