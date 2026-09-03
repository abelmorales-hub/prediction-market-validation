"""Watch for newly settled markets and record their outcomes.

**`was_disputed` is never set.** The design doc's column is annotated `-- UMA`, which is
a Polymarket mechanism. Kalshi exposes no equivalent: a scan of 200 settled markets found
no field matching disput/void/contest/appeal/review, and `result` only ever took the
values 'yes' and 'no'. Writing `false` there would assert "not disputed" about every
market when the truth is "unknown", so the column stays NULL.

What is recorded instead, in `notes`, is a **heuristic**: markets whose settlement lag is
unusual for their category. Observed lag (`resolved_at - end_date`) across 200 settled
markets was p50 = 0 min, p90 = 4484 min, max = 21590 min — heavily zero-inflated with a
long tail. A late settlement is not a dispute; it is the only observable that correlates
with one. Anything built on this must treat it as a flag for manual review, never as
ground truth.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from src.ingest.base import MarketDataSource, Resolution
from src.storage.supabase_repo import Repo

log = logging.getLogger(__name__)

DEFAULT_QUANTILE = 0.95
DEFAULT_FLOOR_MIN = 60.0
DEFAULT_MIN_CATEGORY_N = 20


@dataclass
class WatchStats:
    seen: int = 0
    written: int = 0
    catalogued: int = 0
    skipped_unknown: int = 0
    skipped_unsettled: int = 0
    flagged_lag: int = 0
    errors: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, int]:
        return {
            "seen": self.seen,
            "written": self.written,
            "catalogued": self.catalogued,
            "skipped_unknown": self.skipped_unknown,
            "skipped_unsettled": self.skipped_unsettled,
            "flagged_lag": self.flagged_lag,
        }


def lag_minutes(resolved_at: datetime, end_date: datetime) -> float:
    return (resolved_at - end_date).total_seconds() / 60.0


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(int(q * len(ordered)), len(ordered) - 1)
    return ordered[idx]


def lag_thresholds(
    lags_by_category: dict[str, list[float]],
    quantile: float = DEFAULT_QUANTILE,
    floor_min: float = DEFAULT_FLOOR_MIN,
    min_n: int = DEFAULT_MIN_CATEGORY_N,
) -> dict[str, float]:
    """Per-category cutoff above which a settlement lag is called unusual.

    A category with too few observations gets no threshold at all rather than a
    threshold computed from noise — an outlier test on n=3 invents its own outliers.
    The absolute floor exists because the median lag is 0 for most categories, so a
    purely relative cutoff would flag every market that took two minutes to settle.
    """
    out = {}
    for category, lags in lags_by_category.items():
        if len(lags) < min_n:
            continue
        out[category] = max(_quantile(lags, quantile), floor_min)
    return out


class ResolutionWatcher:
    def __init__(
        self,
        source: MarketDataSource,
        repo: Repo,
        quantile: float = DEFAULT_QUANTILE,
        floor_min: float = DEFAULT_FLOOR_MIN,
        min_category_n: int = DEFAULT_MIN_CATEGORY_N,
        max_catalogue_fetches: int = 0,
    ) -> None:
        """`max_catalogue_fetches` defaults to 0: record outcomes only for markets
        already in the catalogue.

        The venue settles ~5,000 markets a day, almost all combo shards the universe
        filter excludes on purpose. Cataloguing an arbitrary slice of them would fill
        `markets` with rows we never studied and make tracked coverage meaningless. The
        snapshotter is what populates the catalogue, from the universe it actually
        watches.
        """
        self.source = source
        self.repo = repo
        self.quantile = quantile
        self.floor_min = floor_min
        self.min_category_n = min_category_n
        self.max_catalogue_fetches = max_catalogue_fetches

    def poll(self, since: datetime) -> WatchStats:
        stats = WatchStats()
        resolutions = self.source.list_recently_resolved(since)
        stats.seen = len(resolutions)
        if not resolutions:
            return stats

        meta = self._ensure_catalogued(resolutions, stats)
        self._record(resolutions, meta, stats)
        return stats

    def poll_catalogue(self, before: datetime) -> WatchStats:
        """Ask the venue about our own markets instead of scanning everything it settled.

        `poll` is unusable for a targeted backfill: the venue settles ~5,000 markets a
        day, they are overwhelmingly combo shards, and they fill the paginated
        `?status=settled` listing long before any market we track appears. Driving from
        the catalogue is also cheap — one request per market we actually studied.
        """
        stats = WatchStats()
        ids = self.repo.unresolved_market_ids(before)
        resolutions = []
        for market_id in ids:
            stats.seen += 1
            try:
                res = self.source.get_resolution(market_id)
            except Exception as exc:  # noqa: BLE001
                stats.errors[type(exc).__name__] = stats.errors.get(type(exc).__name__, 0) + 1
                log.warning("could not resolve %s: %s", market_id, exc)
                continue
            if res is None:
                stats.skipped_unsettled += 1
                continue
            resolutions.append(res)

        meta = self.repo.market_meta([r.market_id for r in resolutions])
        self._record(resolutions, meta, stats)
        return stats

    def _record(
        self,
        resolutions: list[Resolution],
        meta: dict[str, dict[str, object]],
        stats: WatchStats,
    ) -> None:
        thresholds = self._thresholds(resolutions, meta)
        for res in resolutions:
            info = meta.get(res.market_id)
            if info is None:
                stats.skipped_unknown += 1
                continue
            note = self._note(res, info, thresholds)
            if note:
                stats.flagged_lag += 1
            # was_disputed stays None: unknown, not false.
            self.repo.upsert_outcome(res, was_disputed=None, notes=note)
            stats.written += 1

    def _ensure_catalogued(
        self, resolutions: list[Resolution], stats: WatchStats
    ) -> dict[str, dict[str, object]]:
        """Outcomes carry a foreign key to markets, so anything unseen must be catalogued
        first. Most will already be there from having been snapshotted while open."""
        ids = [r.market_id for r in resolutions]
        meta = self.repo.market_meta(ids)
        missing = [i for i in ids if i not in meta]
        if not missing:
            return meta

        fetched = []
        for market_id in missing[: self.max_catalogue_fetches]:
            try:
                fetched.append(self.source.get_market(market_id))
            except Exception as exc:  # noqa: BLE001
                stats.errors[type(exc).__name__] = stats.errors.get(type(exc).__name__, 0) + 1
                log.warning("could not catalogue %s: %s", market_id, exc)
        if fetched:
            self.repo.upsert_markets(fetched)
            stats.catalogued = len(fetched)
            meta = self.repo.market_meta(ids)
        if len(missing) > self.max_catalogue_fetches:
            log.warning(
                "%d resolved markets left uncatalogued this pass (cap %d)",
                len(missing) - self.max_catalogue_fetches,
                self.max_catalogue_fetches,
            )
        return meta

    def _thresholds(
        self, resolutions: list[Resolution], meta: dict[str, dict[str, object]]
    ) -> dict[str, float]:
        by_category: dict[str, list[float]] = {}
        for res in resolutions:
            info = meta.get(res.market_id)
            if info is None:
                continue
            category = str(info.get("category") or "uncategorised")
            end_date = info.get("end_date")
            if isinstance(end_date, datetime):
                by_category.setdefault(category, []).append(lag_minutes(res.resolved_at, end_date))
        return lag_thresholds(by_category, self.quantile, self.floor_min, self.min_category_n)

    def _note(
        self,
        res: Resolution,
        info: dict[str, object],
        thresholds: dict[str, float],
    ) -> str | None:
        end_date = info.get("end_date")
        if not isinstance(end_date, datetime):
            return None
        category = str(info.get("category") or "uncategorised")
        cutoff = thresholds.get(category)
        if cutoff is None:
            return None
        lag = lag_minutes(res.resolved_at, end_date)
        if lag <= cutoff:
            return None
        return (
            f"HEURISTIC settlement_lag_outlier: {lag:.0f} min after close, "
            f"category '{category}' cutoff {cutoff:.0f} min. "
            f"Not a dispute flag - venue exposes none. Review manually."
        )
