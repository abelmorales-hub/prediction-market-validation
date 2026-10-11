"""H3: the same ensemble, moved to the station the venue settles on.

H2 (`gefs_ensemble_raw`) lost G3 by 0.1008 in mean paired Brier. The failure was location,
not ignorance -- ECE 0.171 against the market's 0.049, AUC at chance inside every price
bucket -- which is what a distribution of the right shape in the wrong place looks like.
`src.models.station_bias` measures that place. This applies it.

The correction is one line: add the station's offset to every member before the strike
arithmetic. Everything else -- which ensemble record a forecast may see, which markets it
declines, how refusals are counted -- is inherited unchanged from `WeatherForecaster`,
deliberately. That class holds the single line where a look-ahead leak could enter
(`latest_ensemble_before(station, settles_on, point.ts)`), it is frozen, and inheriting it
means H3 provably shares H2's point-in-time rule instead of a copy that could drift.

**Why the members move and not the strikes.** Shifting a rung's floor and cap by minus the
offset would be equivalent *only* for a whole-degree offset, because settlement is an
integer and members are rounded half-up before comparison. These offsets are tenths of a
degree. Moving the members leaves the rounding where the venue puts it.

This file and `src/models/station_bias.py` are H3's frozen pair, the same way
`src/models/weather.py` and `src/analysis/weather.py` are H2's. A change to either is a new
`model_version` and a new amendment, never an edit.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from typing import Any

from src.analysis.emit import Forecast
from src.analysis.horizons import HorizonPoint
from src.analysis.weather import TMAX_SERIES_PREFIX, WeatherForecaster
from src.ingest.gefs import PIT_GUARANTEE
from src.models.station_bias import FIT_THROUGH, offset_for
from src.models.weather import ensemble_probability

log = logging.getLogger(__name__)

MODEL_VERSION = "gefs_station_bias_v1"


class StationBiasForecaster(WeatherForecaster):
    """H3 as a callable over horizon points, for `emit`.

    Shares its parent's constructor, its ensemble cache and its `stats` counters. Running
    an instance of each would double both the queries and the cache, so `emit` is handed
    one forecaster that answers with both models (see `scripts.emit_baselines`).
    """

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

        offset = offset_for(str(station))
        corrected = [float(v) + offset for v in row["tmax_f"]]
        try:
            p_hat = ensemble_probability(
                corrected, strike_type, m.get("floor_strike"), m.get("cap_strike")
            )
        except ValueError as exc:
            # A rung shape we cannot read is data, not a crash -- but it must be loud,
            # because it silently removes markets from the study.
            self.stats.bad_rung += 1
            if strike_type not in self.stats._warned:
                self.stats._warned.add(strike_type)
                log.warning("cannot price rung %s (%s): %s", point.market_id, strike_type, exc)
            return {}

        self.stats.forecast += 1
        return {MODEL_VERSION: Forecast(p_hat, _features_hash(row, str(station), offset))}


def _features_hash(row: Mapping[str, Any], station: str, offset: float) -> str:
    """Fingerprint of the ensemble record *and* the correction applied to it.

    The offset belongs in here rather than being implied by the model_version. A run of H3
    must be distinguishable from a run of a refitted successor, and the only way a stored
    prediction can prove which correction produced it is to carry the number. The training
    cutoff comes along so a row also records which fit the offset came from.
    """
    payload = json.dumps(
        {
            "pit": PIT_GUARANTEE,
            "station": row["station"],
            "target_date": str(row["target_date"]),
            "fetched_at": row["fetched_at"].isoformat(),
            "is_pure_forecast": row["is_pure_forecast"],
            "tmax_f": list(row["tmax_f"]),
            "offset_f": round(offset, 3),
            "offset_station": station,
            "fit_through": FIT_THROUGH,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]
