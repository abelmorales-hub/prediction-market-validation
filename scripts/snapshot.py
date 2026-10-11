"""Run the snapshot scheduler.

Run: uv run python -m scripts.snapshot [--minutes 60] [--tick 60]

Ctrl-C flushes the buffer before exiting. A hard kill loses at most one tick.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime, time

from src.config import get_settings
from src.ingest.kalshi import KalshiSource
from src.scheduler import wakelock
from src.scheduler.runner import SnapshotRunner
from src.storage.supabase_repo import Repo, connect


def parse_until(spec: str) -> datetime:
    """The absolute instant this run must stop at.

    Two spellings, because the two hosts want different things:

    * `HH:MM` — *today's* occurrence of a local wall-clock time. Deliberately does not
      roll forward to tomorrow: a time already past means the band has closed, and the
      caller exits rather than collecting a whole extra window. Local, because a band on
      a desktop brackets horizons fixed to a wall clock and has to keep bracketing them
      across a DST change.
    * a full ISO 8601 instant — what a UTC runner wants, where there is no DST seam to
      track and the shift boundary is the thing that must not move. A naive value is
      read as local.

    An instant, not a duration, on purpose: the old code converted this to a tick count
    once at startup, so anything that slowed a tick stretched the run past the band.
    See `run_forever`'s `deadline`.
    """
    if len(spec) <= 5 and ":" in spec:
        target_t = time.fromisoformat(spec)
        now = datetime.now().astimezone()
        return now.replace(hour=target_t.hour, minute=target_t.minute, second=0, microsecond=0)
    parsed = datetime.fromisoformat(spec)
    return parsed if parsed.tzinfo else parsed.astimezone()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=0, help="0 = run until interrupted")
    ap.add_argument(
        "--until",
        help="instant to stop at: local HH:MM, or a full ISO 8601 timestamp. Absolute "
        "where --minutes is a budget, so a run that starts late still ends when the "
        "window does instead of overrunning into the next one.",
    )
    ap.add_argument("--tick", type=float, default=60.0, help="seconds between sweeps")
    ap.add_argument("--horizon-hours", type=int, default=30, help="track markets closing within")
    ap.add_argument("--min-volume", type=float, default=1000.0, help="liquidity floor")
    ap.add_argument("--max-markets", type=int, default=400, help="cap on tracked markets")
    ap.add_argument("--no-catalogue", action="store_true", help="skip the Supabase catalogue")
    ap.add_argument(
        "--category",
        default="Climate and Weather",
        help="admit only series the catalogue lists in this category. The venue-wide "
        "sweep returns nothing once combo shards fill its pages, so admission is driven "
        "per series; empty string restores the venue-wide sweep and its failure mode.",
    )
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

    # Admission is per series, so the series have to come from somewhere before the first
    # sweep. Without a catalogue there is no work list and the only option left is the
    # venue-wide sweep, which is why this refuses rather than quietly collecting nothing.
    series: list[str] | None = None
    if args.category:
        if repo is None:
            print(
                f"--category {args.category!r} needs the catalogue; "
                "pass --category '' to sweep venue-wide instead",
                file=sys.stderr,
            )
            return 2
        series = repo.series_in_category(args.category)
        if not series:
            print(f"catalogue lists no series in {args.category!r}", file=sys.stderr)
            return 2
        logging.info("admitting from %d catalogued %s series", len(series), args.category)

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
            series_tickers=series,
        )
        deadline = parse_until(args.until) if args.until else None
        if deadline is not None and deadline <= datetime.now(UTC):
            logging.info("already past --until %s; nothing to collect", args.until)
            return 0
        # --minutes stays a tick count: it is a budget, and a caller asking for sixty
        # minutes of sweeps is asking for sixty minutes of sweeps. --until is the window,
        # and a window is a pair of instants. Both bounds apply when both are given.
        ticks = int(args.minutes * 60 / args.tick) if args.minutes else None
        try:
            stats = runner.run_forever(
                closing_within_s=args.horizon_hours * 3600,
                tick_s=args.tick,
                max_ticks=ticks,
                deadline=deadline,
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
