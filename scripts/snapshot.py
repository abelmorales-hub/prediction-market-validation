"""Run the snapshot scheduler.

Run: uv run python -m scripts.snapshot [--minutes 60] [--tick 60]

Ctrl-C flushes the buffer before exiting. A hard kill loses at most one tick.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, time

from src.config import get_settings
from src.ingest.kalshi import KalshiSource
from src.scheduler import wakelock
from src.scheduler.runner import SnapshotRunner
from src.storage.supabase_repo import Repo, connect


def _minutes_until(hhmm: str) -> int:
    """Minutes from now until *today's* occurrence of a local wall-clock time.

    Deliberately does not roll forward to tomorrow. A negative result means the band has
    already closed, and the caller exits instead of collecting: that is what stops a
    restart tick firing after the band from running a whole extra window.

    Local, not UTC. The band exists to bracket horizons that are themselves fixed to a
    wall clock, and it has to keep bracketing them across a DST change.
    """
    target_t = time.fromisoformat(hhmm)
    now = datetime.now().astimezone()
    target = now.replace(
        hour=target_t.hour, minute=target_t.minute, second=0, microsecond=0
    )
    return int((target - now).total_seconds() // 60)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=0, help="0 = run until interrupted")
    ap.add_argument(
        "--until",
        help="local HH:MM to stop at. Unlike --minutes this is absolute, so a run that "
        "starts late still ends when the collection band does instead of overrunning it "
        "and holding the machine awake.",
    )
    ap.add_argument("--tick", type=float, default=60.0, help="seconds between sweeps")
    ap.add_argument("--horizon-hours", type=int, default=30, help="track markets closing within")
    ap.add_argument("--min-volume", type=float, default=1000.0, help="liquidity floor")
    ap.add_argument("--max-markets", type=int, default=400, help="cap on tracked markets")
    ap.add_argument("--no-catalogue", action="store_true", help="skip the Supabase catalogue")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", stream=sys.stdout
    )
    # httpx logs a line per request; at hundreds of markets a sweep that is noise.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = get_settings()
    root = cfg.data_dir / "snapshots"

    # Waking the machine for this job is not enough on its own: Windows keeps counting
    # down to idle sleep while it runs, so a collection window would end thirty minutes
    # in and leave nothing but a gap to explain it.
    wakelock.hold_awake()

    # The catalogue is what lets the resolution watcher and reconciliation know which
    # markets we were actually watching. Without it `markets` stays empty and coverage
    # cannot be measured against anything meaningful.
    conn = None
    repo = None
    if not args.no_catalogue and cfg.supabase_db_url:
        conn = connect(cfg.supabase_db_url)
        repo = Repo(conn)

    with KalshiSource(
        cfg.kalshi_base_url,
        cfg.http_timeout_s,
        cache_dir=cfg.cache_dir,
        metadata_ttl_s=cfg.metadata_cache_ttl_s,
    ) as source:
        runner = SnapshotRunner(
            source,
            root,
            min_volume=args.min_volume,
            max_markets=args.max_markets,
            repo=repo,
        )
        minutes = args.minutes
        if args.until:
            remaining = _minutes_until(args.until)
            if remaining <= 0:
                logging.info("already past --until %s; nothing to collect", args.until)
                return 0
            # Whichever bound is tighter. --until is what keeps a late-starting instance
            # from running the full --minutes and overrunning the band.
            minutes = remaining if not minutes else min(minutes, remaining)
        ticks = int(minutes * 60 / args.tick) if minutes else None
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
            if conn is not None:
                conn.close()
            # Simétrico con hold_awake(). Windows suelta el estado de ejecución
            # al morir el proceso, así que esto no arregla una fuga -- lo que
            # arregla es que soltar un bloqueo de todo el sistema dependiera de
            # que el proceso termine. Sin esta línea, release() no se llamaba
            # desde ningún sitio: era código muerto con nombre de contrato.
            wakelock.release()

    print("\n" + "  ".join(f"{k}={v}" for k, v in stats.as_dict().items()))
    if stats.failures:
        print("failures: " + ", ".join(f"{k}={v}" for k, v in stats.failures.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
