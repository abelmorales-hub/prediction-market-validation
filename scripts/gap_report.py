"""Report holes in snapshot coverage.

Run: uv run python -m scripts.gap_report [--days 1] [--tolerance 2.5]

Exits 1 if any CRITICAL gap is found, so it can gate G0 ("no gaps >15 min" around the
prediction horizons) rather than merely being read.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from src.config import get_settings
from src.scheduler.gaps import coverage, find_gaps, parse_band, parse_since
from src.storage.duckdb_views import connect


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1, help="look back this many days")
    ap.add_argument(
        "--since",
        help="ISO instant to measure from, e.g. 2026-09-03T18:33Z. Overrides --days.",
    )
    ap.add_argument("--tolerance", type=float, default=2.5, help="multiple of cadence")
    ap.add_argument("--limit", type=int, default=20, help="rows to print")
    ap.add_argument(
        "--band",
        help="daily collection window in local time, e.g. 05:30-11:00. Gaps starting "
        "outside it are not counted: the collector is not running then, by design.",
    )
    ap.add_argument("--tz", default="Europe/Madrid", help="timezone the band is expressed in")
    args = ap.parse_args()

    # A rolling --days window cannot answer the question the pre-declaration asks. That
    # document fixes an analysis window with a start instant, and says the window is
    # void for any period with a CRITICAL gap *inside it*. Judged over a rolling two
    # days, a clean window still fails on holes that predate it and are already
    # excluded from the study.
    if args.since:
        try:
            since = parse_since(args.since)
        except ValueError:
            print(f"--since is not an ISO instant: {args.since!r}", file=sys.stderr)
            return 2
        label = f"since {since.isoformat()}"
    else:
        since = datetime.now(UTC) - timedelta(days=args.days)
        label = f"last {args.days}d"

    root = get_settings().data_dir / "snapshots"
    con = connect(root)
    # Bound as an ISO string: duckdb needs pytz to accept a tz-aware datetime parameter,
    # and that is not worth a dependency for one comparison.
    rows = con.execute(
        "select market_id, ts, close_time from book_snapshots "
        "where ts >= cast(? as timestamptz) order by market_id, ts",
        [since.isoformat()],
    ).fetchall()

    band_note = f"  band {args.band} {args.tz}" if args.band else ""
    print(f"GAP REPORT  {label}  tolerance {args.tolerance}x cadence{band_note}")
    if not args.band:
        # Deliberately a warning and not a default. Hardcoding the operative band
        # here would put the same constant in two places, and a band that drifts
        # from its own specification is exactly the bug this warning exists to
        # stop: pre_declaration.md said 05:30-11:00 for a day while the collector
        # ran to 11:30, and nothing anywhere errored.
        print(
            "NOTE: no --band given, so this measures the ORIGINAL G0 (continuous\n"
            "      collection). Since 2026-09-04 the collector runs in a daily band\n"
            "      and G0 is defined inside it -- see docs/pre_declaration.md,\n"
            "      Amendments 3 and 4. Without --band every overnight hole counts\n"
            "      as a fault and this gate can never pass.\n"
        )
    print(f"snapshots: {len(rows)}  markets: {len({r[0] for r in rows})}\n")
    if not rows:
        print("No snapshots in range. Nothing to check.")
        return 0

    typed = [(str(a), b, c) for a, b, c in rows]
    stats = coverage(typed)
    print(
        f"dense-window samples: {int(stats['dense_samples'])} "
        f"across {int(stats['dense_markets'])} markets "
        f"(capture rate {stats.get('capture_rate', 1.0):.1%})\n"
    )

    band = None
    if args.band:
        try:
            band = parse_band(args.band)
        except ValueError as exc:
            print(f"--band: {exc}", file=sys.stderr)
            return 2

    gaps = find_gaps(typed, tolerance=args.tolerance, band=band, tz=ZoneInfo(args.tz))
    critical = [g for g in gaps if g.severity == "CRITICAL"]
    missed = [g for g in gaps if g.severity == "MISSED"]

    if gaps:
        print(f"{'SEVERITY':<10} {'MARKET':<34} {'MINUTES':>8}  {'EXPECTED':>8}  FROM")
        for g in gaps[: args.limit]:
            print(
                f"{g.severity:<10} {g.market_id[:33]:<34} {g.minutes:>8.1f}"
                f"  {g.expected.total_seconds() / 60:>8.0f}  {g.start.isoformat()}"
            )
        if len(gaps) > args.limit:
            print(f"... {len(gaps) - args.limit} more")
        print()

    print(f"CRITICAL (dense window, gates G0): {len(critical)}")
    print(f"MISSED   (sparse region)         : {len(missed)}")
    if critical:
        print("\nG0 NOT MET: a prediction horizon may have gone unsampled.")
        return 1
    print("\nG0 clean: no gaps around the prediction horizons.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
