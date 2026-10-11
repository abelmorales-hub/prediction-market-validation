"""Write forecasts for every horizon that has coverage and is still open.

Baselines and H2 both, in one pass and through one door: they must be stamped at the
same instant with the same market price, and two jobs would eventually drift apart on
which snapshot each considered current. The script keeps its name because the schedule
and the supervisor entry are named after it.

Run: uv run python -m scripts.emit_baselines [--tolerance-min 5]

This must run while markets are open. The database refuses a prediction stamped at or
after the close, so a horizon that passes without this job running cannot be recovered.
Run it alongside the snapshotter, not afterwards.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from datetime import UTC, datetime, timedelta

from src.analysis.emit import emit
from src.analysis.horizons import horizon_points
from src.analysis.station_bias import StationBiasForecaster
from src.config import get_settings
from src.storage.duckdb_views import connect as duck_connect
from src.storage.supabase_repo import Repo, connect


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001 - provenance is best effort, not a reason to stop
        return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tolerance-min", type=float, default=5.0)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    cfg = get_settings()
    if not cfg.supabase_db_url:
        print("SUPABASE_DB_URL not set", file=sys.stderr)
        return 2

    now = datetime.now(UTC)
    con = duck_connect(cfg.data_dir / "snapshots")
    rows = con.execute(
        "select market_id, ts, mid, best_bid, best_ask, close_time from book_snapshots "
        "where mid is not null order by market_id, ts"
    ).fetchall()

    by_market: dict[str, list[tuple[datetime, float, float, float]]] = {}
    closes: dict[str, datetime] = {}
    for market_id, ts, mid, bid, ask, close_time in rows:
        by_market.setdefault(market_id, []).append((ts, float(mid), float(bid), float(ask)))
        closes[market_id] = close_time

    points = []
    for market_id, samples in by_market.items():
        points.extend(
            horizon_points(
                market_id, closes[market_id], samples, timedelta(minutes=args.tolerance_min)
            )
        )

    print(f"markets with snapshots: {len(by_market)}")
    print(f"horizon points found  : {len(points)}")
    if not points:
        print("nothing to emit")
        return 0

    with connect(cfg.supabase_db_url) as conn:
        repo = Repo(conn)
        # One catalogue read for the whole pass. The forecaster needs each market's strike,
        # station and target day, and this job runs on a horizon that cannot be revisited —
        # it does not get to spend that window on a query per point.
        #
        # H3 since 2026-10-11, and H2 is no longer emitted. H2 is closed: it lost G3, and
        # the pre-declaration forbids re-testing it, so writing its rows would only create
        # something to be tempted by. Nothing is lost by stopping — H2's p_hat is a pure
        # function of the ensemble record and the strike, both stored, so it is
        # recomputable from any H3 row at any time. What is never recomputable is a
        # prediction that was not written, which is why H3 emits from the moment it exists.
        forecaster = StationBiasForecaster(repo, repo.market_meta([p.market_id for p in points]))
        stats = emit(repo, points, closes, git_sha(), now, forecaster=forecaster)
    print("  ".join(f"{k}={v}" for k, v in stats.as_dict().items()))
    # Printed every run, including the zeroes. A model that quietly stops forecasting
    # looks exactly like a model with nothing to say, and the baselines keep flowing
    # either way — so the reason for every refusal is on the line or it is invisible.
    print("  ".join(f"{k}={v}" for k, v in forecaster.stats.as_dict().items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
