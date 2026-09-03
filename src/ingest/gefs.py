"""GEFS ensemble temperature, read-only, recorded point-in-time.

The forecast is served as JSON by an Open-Meteo ensemble endpoint rather than pulled
as GRIB2 from NOMADS. That is a deliberate trade: GRIB would give the raw run and its
init time, at the cost of an eccodes/cfgrib toolchain that is painful on Windows and a
decoder between us and the numbers. JSON gives 31 members, already aggregated to the
local calendar day the markets settle on.

**Point-in-time is guaranteed by the clock, not by the payload.** The endpoint returns
whatever run is current and does not name it, so nothing here can prove which
initialisation produced a number after the fact. What makes a record trustworthy is
that it was written *before* the prediction it feeds: `fetched_at` is the guarantee,
and it is only meaningful because the fetch happens live. Backfilling this table is
therefore impossible in the same way `emit_baselines` is impossible to backfill, and
for the same reason -- see PIT_GUARANTEE.

A consequence worth stating: for a target date already under way, part of the daily
max may reflect weather that has already happened. That is legitimate information at
`fetched_at`, but it means a record is only a pure forecast when `fetched_at` precedes
the local start of the target day. `EnsembleDay.is_pure_forecast` records which.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from src.ingest.http import PacedClient
from src.ingest.stations import Station

PIT_GUARANTEE = "recorded-live: contains no information published after fetched_at"

# GEFS runs 31 members. Fewer means a truncated or partially published run, which is a
# reason to refuse the record rather than to average over whatever arrived.
EXPECTED_MEMBERS = 31
MIN_MEMBERS = 20

# Fahrenheit throughout: the markets are quoted in it, and converting once at the
# boundary is safer than converting at every comparison against a strike.
TEMPERATURE_UNIT = "fahrenheit"


@dataclass(frozen=True)
class EnsembleDay:
    """One local calendar day, one member value per ensemble member."""

    target_date: date
    tmax_f: tuple[float, ...]
    tmin_f: tuple[float, ...]
    is_pure_forecast: bool
    """False when the day had already begun locally at fetch time, so the value may
    incorporate observed weather rather than only forecast weather."""

    @property
    def members(self) -> int:
        return len(self.tmax_f)


@dataclass(frozen=True)
class EnsembleForecast:
    station_code: str
    model: str
    fetched_at: datetime
    grid_latitude: float
    grid_longitude: float
    grid_elevation_m: float
    days: tuple[EnsembleDay, ...]

    def day(self, target: date) -> EnsembleDay | None:
        for d in self.days:
            if d.target_date == target:
                return d
        return None

    def payload_for(self, target: date) -> dict[str, Any] | None:
        """The JSON stored in features_pit. Members are kept individually: a mean and a
        spread cannot reconstruct the distribution, and the probability of a 2-degree
        bucket depends on its shape."""
        day = self.day(target)
        if day is None:
            return None
        return {
            "station": self.station_code,
            "model": self.model,
            "target_date": target.isoformat(),
            "fetched_at": self.fetched_at.isoformat(),
            "is_pure_forecast": day.is_pure_forecast,
            "members": day.members,
            "tmax_f": list(day.tmax_f),
            "tmin_f": list(day.tmin_f),
            "grid": {
                "latitude": self.grid_latitude,
                "longitude": self.grid_longitude,
                "elevation_m": self.grid_elevation_m,
            },
        }


class GefsSource:
    """Read-only ensemble client. Has no method that can write anything."""

    def __init__(
        self,
        base_url: str,
        timeout: float,
        model: str = "gfs025",
        forecast_days: int = 4,
    ) -> None:
        # Reuses the market client's transport for its retry and pacing behaviour. No
        # cache is passed: a cached forecast would defeat the entire point of recording
        # what was knowable at fetch time.
        self._http = PacedClient(base_url, timeout)
        self.model = model
        self.forecast_days = forecast_days

    def __enter__(self) -> GefsSource:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def fetch(self, station: Station) -> EnsembleForecast:
        fetched_at = datetime.now(UTC)
        body = self._http.get_json(
            "/v1/ensemble",
            {
                "latitude": station.latitude,
                "longitude": station.longitude,
                "models": self.model,
                "daily": "temperature_2m_max,temperature_2m_min",
                "temperature_unit": TEMPERATURE_UNIT,
                "forecast_days": self.forecast_days,
                "timezone": station.timezone,
            },
        )
        return _parse(body, station, self.model, fetched_at)


def _members(daily: dict[str, Any], prefix: str) -> list[list[float | None]]:
    """Per-member series, ordered by member number.

    The unsuffixed `temperature_2m_max` key is the control run and is included: it is
    a legitimate member, and dropping it would silently change n from 31 to 30.
    """
    keys = sorted(k for k in daily if k == prefix or k.startswith(prefix + "_member"))
    return [daily[k] for k in keys]


def _parse(
    body: dict[str, Any], station: Station, model: str, fetched_at: datetime
) -> EnsembleForecast:
    """Validate at the boundary. A malformed response must raise here rather than
    become a probability later."""
    if body.get("error"):
        raise ValueError(f"ensemble API error: {body.get('reason')}")

    daily = body.get("daily")
    if not isinstance(daily, dict) or "time" not in daily:
        raise ValueError("ensemble response has no daily block")

    tmax = _members(daily, "temperature_2m_max")
    tmin = _members(daily, "temperature_2m_min")
    if len(tmax) < MIN_MEMBERS or len(tmin) < MIN_MEMBERS:
        raise ValueError(
            f"{station.code}: {len(tmax)} max / {len(tmin)} min members, "
            f"below the {MIN_MEMBERS} floor"
        )

    local_today = _local_today(body)
    days = []
    for i, day_text in enumerate(daily["time"]):
        target = date.fromisoformat(day_text)
        highs = tuple(v for s in tmax if (v := s[i]) is not None)
        lows = tuple(v for s in tmin if (v := s[i]) is not None)
        if len(highs) < MIN_MEMBERS or len(lows) < MIN_MEMBERS:
            continue  # a partially published day is dropped, not averaged over
        days.append(
            EnsembleDay(
                target_date=target,
                tmax_f=highs,
                tmin_f=lows,
                is_pure_forecast=target > local_today,
            )
        )

    if not days:
        raise ValueError(f"{station.code}: no day had enough members")

    return EnsembleForecast(
        station_code=station.code,
        model=model,
        fetched_at=fetched_at,
        grid_latitude=float(body.get("latitude", station.latitude)),
        grid_longitude=float(body.get("longitude", station.longitude)),
        grid_elevation_m=float(body.get("elevation", 0.0)),
        days=tuple(days),
    )


def _local_today(body: dict[str, Any]) -> date:
    """Today at the station, derived from the offset the API itself applied.

    Using the machine's local date instead would be wrong from any other timezone,
    which is exactly the situation here: the collector runs in Europe and every
    station is in North America.
    """
    offset_s = int(body.get("utc_offset_seconds", 0))
    return datetime.fromtimestamp(datetime.now(UTC).timestamp() + offset_s, UTC).date()
