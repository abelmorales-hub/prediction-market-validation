"""Record outcomes for newly settled markets.

Run: uv run python -m scripts.watch_resolutions [--hours 24]

was_disputed is never populated: the venue exposes no dispute field. See
src/resolution/watcher.py for what is recorded instead and why it is heuristic.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta

from src.config import get_settings
from src.ingest.kalshi import KalshiSource
from src.resolution.watcher import ResolutionWatcher
from src.storage.supabase_repo import Repo, connect


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24, help="look back this far")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cfg = get_settings()
    if not cfg.supabase_db_url:
        print("SUPABASE_DB_URL not set", file=sys.stderr)
        return 2

    since = datetime.now(UTC) - timedelta(hours=args.hours)
    with (
        KalshiSource(cfg.kalshi_base_url, cfg.http_timeout_s, cache_dir=cfg.cache_dir) as source,
        connect(cfg.supabase_db_url) as conn,
    ):
        stats = ResolutionWatcher(source, Repo(conn)).poll(since)

    print("  ".join(f"{k}={v}" for k, v in stats.as_dict().items()))
    if stats.errors:
        print("errors: " + ", ".join(f"{k}={v}" for k, v in stats.errors.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
