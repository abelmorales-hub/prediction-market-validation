"""Wire the GEFS ensemble to a market's strike, point-in-time.

`src.models.weather` does the arithmetic and knows nothing about storage. This module is
the impure half: it decides *which* ensemble record a forecast is allowed to see, which
is the only place a look-ahead leak can enter. That decision is one line —
`latest_ensemble_before(station, settles_on, point.ts)` — and everything else here exists
to refuse cleanly when the inputs are not there.

Refusals are counted, never logged-and-forgotten. This project has already shipped one
silently dead collector; a forecaster that quietly stops producing rows would be worse,
because the baselines would keep flowing and the gap would look like a modelling result.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from src.analysis.emit import Forecast
from src.analysis.horizons import HorizonPoint
from src.ingest.gefs import PIT_GUARANTEE
from src.models.weather import ensemble_probability
from src.storage.supabase_repo import Repo

log = logging.getLogger(__name__)

MODEL_VERSION = "gefs_ensemble_raw"

# Series this module knows how to answer. KXHIGH* settles on the day's maximum, which is
# `tmax_f`. KXLOW* would need `tmin_f` and its own point-in-time argument (see the
# is_pure_forecast note below, which does not transfer), so it is declined rather than
# guessed at. A new series shows up in `unsupported_series` instead of vanishing.
TMAX_SERIES_PREFIX = "KXHIGH"


@dataclass
class ForecastStats:
    """Why a horizon point did or did not get an H2 forecast."""

    forecast: int = 0
    not_catalogued: int = 0
    unsupported_series: int = 0
    no_strike: int = 0
    no_station_or_day: int = 0
    no_ensemble: int = 0
    bad_rung: int = 0
    _warned: set[str] = field(default_factory=set, repr=False)

    def as_dict(self) -> dict[str, int]:
        return {
            "h2_forecast": self.forecast,
            "h2_not_catalogued": self.not_catalogued,
            "h2_unsupported_series": self.unsupported_series,
            "h2_no_strike": self.no_strike,
            "h2_no_station_or_day": self.no_station_or_day,
            "h2_no_ensemble": self.no_ensemble,
            "h2_bad_rung": self.bad_rung,
        }


class WeatherForecaster:
    """H2 as a callable over horizon points, for `emit`.

    `meta` is loaded once by the caller rather than queried per point: emit runs every
    four minutes over a few hundred points and it is the one job that cannot be re-run
    after the fact, so it does not get to spend that window on round trips.
    """

    def __init__(self, repo: Repo, meta: Mapping[str, Mapping[str, Any]]) -> None:
        self.repo = repo
        self.meta = meta
        self.stats = ForecastStats()
        # A six-rung ladder shares one station, one target day and one snapshot instant,
        # so it shares one ensemble record. Six identical queries per event otherwise.
        self._cache: dict[tuple[str, date, datetime], dict[str, Any] | None] = {}

    def __call__(self, point: HorizonPoint) -> dict[str, Forecast]:
        m = self.meta.get(point.market_id)
        if m is None:
            self.stats.not_catalogued += 1
            return {}
        if not str(m.get("series_id") or "").startswith(TMAX_SERIES_PREFIX):
            self.stats.unsupported_series += 1
            return {}

        strike_type = m.get("strike_type")
        if not strike_type:
            self.stats.no_strike += 1
            return {}

        station, settles_on = m.get("settlement_station"), m.get("settles_on")
        if not station or settles_on is None:
            self.stats.no_station_or_day += 1
            return {}

        row = self._ensemble(station, settles_on, point.ts)
        if row is None:
            self.stats.no_ensemble += 1
            return {}

        members = list(row["tmax_f"])
        try:
            p_hat = ensemble_probability(
                members, strike_type, m.get("floor_strike"), m.get("cap_strike")
            )
        except ValueError as exc:
            # A rung shape we do not understand is data, not a crash — but it must be
            # loud, because it silently removes markets from the study.
            self.stats.bad_rung += 1
            if strike_type not in self.stats._warned:
                self.stats._warned.add(strike_type)
                log.warning("cannot price rung %s (%s): %s", point.market_id, strike_type, exc)
            return {}

        self.stats.forecast += 1
        return {MODEL_VERSION: Forecast(p_hat, _features_hash(row))}

    def _ensemble(
        self, station: str, settles_on: date, before: datetime
    ) -> dict[str, Any] | None:
        """The most recent ensemble that existed at `before`.

        `before` is the snapshot's own timestamp, not now(): the forecast has to predate
        the market price it is paired against, or the comparison hands H2 information the
        market did not have.

        No filter on `is_pure_forecast`, deliberately. That flag means the target day had
        begun locally at fetch time, and T-24h for these markets lands around 01:00 local
        on the target day — so filtering on it would reject essentially every T-24h
        forecast and leave H2 with no sample at all. At 01:00 local the day's maximum
        still lies entirely in the future, so the record is a genuine forecast of the
        quantity being settled. The flag stays on the row and in the features hash, so
        the choice is auditable rather than assumed. This reasoning is about `tmax` only,
        which is why KXLOW is declined above.
        """
        key = (station, settles_on, before)
        if key not in self._cache:
            self._cache[key] = self.repo.latest_ensemble_before(station, settles_on, before)
        return self._cache[key]


def _features_hash(row: Mapping[str, Any]) -> str:
    """Fingerprint of the exact ensemble record used, resolvable back to `gefs_ensemble`.

    Provenance first, values second: the triple is that table's primary key, so a stored
    prediction can be traced to the row that produced it, and the member list is included
    so a mismatch is detectable rather than merely improbable.
    """
    payload = json.dumps(
        {
            "pit": PIT_GUARANTEE,
            "station": row["station"],
            "target_date": str(row["target_date"]),
            "fetched_at": row["fetched_at"].isoformat(),
            "is_pure_forecast": row["is_pure_forecast"],
            "tmax_f": list(row["tmax_f"]),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]
