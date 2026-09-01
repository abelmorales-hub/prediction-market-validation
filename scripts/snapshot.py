"""Run the snapshot scheduler.

Run: uv run python -m scripts.snapshot [--minutes 60] [--tick 60]

Ctrl-C flushes the buffer before exiting. A hard kill loses at most one tick.
"""

from __future__ import annotations

import argparse
import logging
import sys

from src.config import get_settings
from src.ingest.kalshi import KalshiSource
from src.scheduler.runner import SnapshotRunner


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=0, help="0 = run until interrupted")
    ap.add_argument("--tick", type=float, default=60.0, help="seconds between sweeps")
    ap.add_argument("--horizon-hours", type=int, default=30, help="track markets closing within")
    ap.add_argument("--min-volume", type=float, default=1000.0, help="liquidity floor")
    ap.add_argument("--max-markets", type=int, default=400, help="cap on tracked markets")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", stream=sys.stdout
    )
    # httpx logs a line per request; at hundreds of markets a sweep that is noise.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = get_settings()
    root = cfg.data_dir / "snapshots"

    with KalshiSource(
        cfg.kalshi_base_url,
        cfg.http_timeout_s,
        cache_dir=cfg.cache_dir,
        metadata_ttl_s=cfg.metadata_cache_ttl_s,
    ) as source:
        runner = SnapshotRunner(
            source, root, min_volume=args.min_volume, max_markets=args.max_markets
        )
        ticks = int(args.minutes * 60 / args.tick) if args.minutes else None
        try:
            stats = runner.run_forever(
                closing_within_s=args.horizon_hours * 3600,
                tick_s=args.tick,
                max_ticks=ticks,
            )
        except KeyboardInterrupt:
            print("\ninterrupted", file=sys.stderr)
            stats = runner.stats
        finally:
            runner.flush()

    print("\n" + "  ".join(f"{k}={v}" for k, v in stats.as_dict().items()))
    if stats.failures:
        print("failures: " + ", ".join(f"{k}={v}" for k, v in stats.failures.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
