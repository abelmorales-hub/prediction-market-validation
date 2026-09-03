"""Record the GEFS ensemble for every settlement station, point-in-time.

Run: uv run python -m scripts.fetch_gefs [--stations CLIMDW,CLINYC]

Every station is fetched on every pass rather than only those with an open market. It
is 19 requests, the ensemble is the same object whichever ladder rung asks for it, and
it removes any need for this job to know what a market is -- the mapping from market
to station belongs to the model that consumes these rows, not to the feed that writes
them.

**This cannot be backfilled.** The endpoint serves the current run and does not name
it, so a row's point-in-time value comes from having been written before the
prediction that cites it. A gap is a permanent hole, exactly as with emit_baselines.
"""

from __future__ import annotations

import argparse
import logging
import sys

from src.config import get_settings
from src.ingest.gefs import GefsSource
from src.ingest.stations import STATIONS, station_for
from src.storage.supabase_repo import Repo, connect

log = logging.getLogger("fetch_gefs")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stations", help="comma-separated codes; default is all")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    cfg = get_settings()
    if not cfg.gefs_base_url:
        print("GEFS_BASE_URL not set", file=sys.stderr)
        return 2
    if not cfg.supabase_db_url:
        print("SUPABASE_DB_URL not set", file=sys.stderr)
        return 2

    if args.stations:
        codes = [c.strip().upper() for c in args.stations.split(",") if c.strip()]
        unknown = [c for c in codes if station_for(c) is None]
        if unknown:
            print(f"unknown station codes: {', '.join(unknown)}", file=sys.stderr)
            return 2
        wanted = [s for c in codes if (s := station_for(c)) is not None]
    else:
        wanted = list(STATIONS.values())

    written = skipped = failed = 0
    forecast_rows = 0

    with (
        GefsSource(
            cfg.gefs_base_url,
            cfg.http_timeout_s,
            model=cfg.gefs_model,
            forecast_days=cfg.gefs_forecast_days,
        ) as source,
        connect(cfg.supabase_db_url) as conn,
    ):
        repo = Repo(conn)
        for station in wanted:
            try:
                forecast = source.fetch(station)
            except Exception as exc:  # noqa: BLE001 - one bad station must not end the pass
                failed += 1
                log.warning("%s: %s: %s", station.code, type(exc).__name__, exc)
                continue

            for day in forecast.days:
                payload = forecast.payload_for(day.target_date)
                if payload is None:  # unreachable: the date came from forecast.days
                    continue
                if repo.insert_ensemble(payload):
                    written += 1
                    forecast_rows += int(day.is_pure_forecast)
                else:
                    skipped += 1

    print(
        f"stations={len(wanted)}  written={written}  "
        f"pure_forecast={forecast_rows}  duplicate={skipped}  failed={failed}"
    )
    # A pass where every station failed is a dead feed, not a quiet day.
    return 1 if failed == len(wanted) and wanted else 0


if __name__ == "__main__":
    sys.exit(main())
