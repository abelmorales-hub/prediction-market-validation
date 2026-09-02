"""Write baseline forecasts into `predictions`.

A baseline is a forecast, so it goes through the same door as any model would: the
append-only `predictions` table, stamped before the market closes, carrying the market
price observed at the same instant. Nothing here is a special case, which is the point —
if this path is sound, the path a real model would take is sound.

**Baselines must be emitted while the market is still open.** The database refuses a
prediction stamped at or after the close, and rightly: a forecast made after the fact is
not a forecast. This is a live job, not something that can be backfilled later, and that
constraint is the honest cost of forward-testing.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from src.analysis.horizons import HorizonPoint
from src.baselines.simple import baseline_market, baseline_shrink
from src.storage.supabase_repo import Prediction, Repo

log = logging.getLogger(__name__)

SHRINK_ALPHA = 0.9
SHRINK_CENTRE = 0.5

# No features are computed in this build, so there is nothing to hash. A literal marker
# beats a hash of the empty string, which would look like a real fingerprint.
NO_FEATURES = "none"


@dataclass
class EmitStats:
    considered: int = 0
    written: int = 0
    skipped_closed: int = 0
    skipped_existing: int = 0
    skipped_degenerate: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "considered": self.considered,
            "written": self.written,
            "skipped_closed": self.skipped_closed,
            "skipped_existing": self.skipped_existing,
            "skipped_degenerate": self.skipped_degenerate,
            "failed": self.failed,
        }


def is_scorable(mid: float) -> bool:
    """A price of exactly 0 or 1 cannot be scored and must not be nudged.

    The schema requires `0 < p_hat < 1`, so a degenerate mid would either be rejected or
    have to be clipped. Clipping is the dangerous option: `baseline_market` would then no
    longer equal the price it is benchmarked against, the null model would stop scoring
    exactly zero, and G2 — the one test that proves the pipeline has no bias — would be
    quietly broken by a rounding decision made here. Excluding the row keeps the null
    exact and costs only markets that had stopped being uncertain anyway.
    """
    return 0.0 < mid < 1.0


def baseline_values(mid: float) -> dict[str, float]:
    """Every closed-form baseline evaluated at one market price.

    `baseline_base_rate` is absent by design: it needs a category rate derived from prior
    resolutions, and with nothing resolved yet any rate would be computed from the very
    outcomes it is later scored against. Adding it here would be the P2 leak the docstring
    in `simple.py` warns about. It enters the report once genuine prior outcomes exist.
    """
    return {
        "baseline_market": float(baseline_market([mid])[0]),
        "baseline_shrink": float(baseline_shrink([mid], alpha=SHRINK_ALPHA)[0]),
    }


def emit(
    repo: Repo,
    points: list[HorizonPoint],
    close_times: Mapping[str, datetime],
    git_sha: str,
    now: datetime,
) -> EmitStats:
    """Insert one prediction per (market, horizon, baseline).

    Duplicates are left to the database. `predictions` is append-only and its primary key
    covers the horizon and model, so a re-run cannot silently double-count; a conflict
    here means the row is already recorded, which is success, not failure.
    """
    stats = EmitStats()
    for point in points:
        close = close_times.get(point.market_id)
        if close is None or now >= close:
            # The trigger would refuse this anyway. Counting it here names the reason
            # rather than surfacing it as an opaque integrity error.
            stats.skipped_closed += 1
            continue
        if not is_scorable(point.mid):
            stats.skipped_degenerate += 1
            continue
        for name, p_hat in baseline_values(point.mid).items():
            stats.considered += 1
            try:
                repo.insert_prediction(
                    Prediction(
                        market_id=point.market_id,
                        ts=point.ts,
                        horizon_label=point.horizon_label,
                        p_hat=p_hat,
                        model_version=name,
                        git_sha=git_sha,
                        features_hash=NO_FEATURES,
                        market_mid=point.mid,
                        market_bid=point.bid,
                        market_ask=point.ask,
                    )
                )
                stats.written += 1
            except Exception as exc:  # noqa: BLE001 - one bad row must not stop the pass
                # Postgres aborts the whole transaction on any failed statement, so
                # without this rollback the first duplicate would cascade into
                # "current transaction is aborted" for every remaining row and the pass
                # would report hundreds of failures caused by one.
                repo.conn.rollback()
                if _is_duplicate(exc):
                    stats.skipped_existing += 1
                else:
                    stats.failed += 1
                    log.warning("prediction insert failed for %s: %s", point.market_id, exc)
    return stats


def _is_duplicate(exc: Exception) -> bool:
    return "duplicate key" in str(exc).lower() or "unique constraint" in str(exc).lower()
