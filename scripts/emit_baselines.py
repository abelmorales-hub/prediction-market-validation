"""Write baseline forecasts for every horizon that has coverage and is still open.

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
        stats = emit(Repo(conn), points, closes, git_sha(), now)
    print("  ".join(f"{k}={v}" for k, v in stats.as_dict().items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
