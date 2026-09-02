"""Compare what the venue says resolved against what we captured.

Run: uv run python -m scripts.reconcile [--days 7] [--gate 98]

Two coverage numbers, because one of them is not the gate:

**Tracked coverage** — of the markets we were actually watching (present in the
catalogue) that resolved in the window, how many have an outcome row. This is the G0
gate. It detects the thing that can silently corrupt the study: a market we predicted on
whose result we never recorded.

**Venue coverage** — of everything the venue resolved, how many we have. Reported, never
gated. The venue settles ~5,000 markets a day, overwhelmingly auto-generated combo shards
and strike-ladder rungs that the universe filter excludes on purpose. Gating on that
number would demand we capture markets we deliberately chose not to study, and no
achievable value of it would mean anything.

Reporting only the tracked number would be self-congratulatory, so both are printed and
a collapse in venue coverage is still visible.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta

from src.config import get_settings
from src.ingest.kalshi import KalshiSource
from src.storage.supabase_repo import Repo, connect


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--gate", type=float, default=98.0, help="minimum tracked coverage percent")
    ap.add_argument("--limit", type=int, default=15, help="missing ids to print")
    args = ap.parse_args()

    cfg = get_settings()
    if not cfg.supabase_db_url:
        print("SUPABASE_DB_URL not set", file=sys.stderr)
        return 2

    until = datetime.now(UTC)
    since = until - timedelta(days=args.days)

    with KalshiSource(cfg.kalshi_base_url, cfg.http_timeout_s, cache_dir=cfg.cache_dir) as source:
        venue = source.list_recently_resolved(since)

    venue_ids = {r.market_id for r in venue if since <= r.resolved_at < until}
    with connect(cfg.supabase_db_url) as conn:
        repo = Repo(conn)
        local_ids = repo.outcome_ids(since, until)
        catalogued = set(repo.market_meta(sorted(venue_ids)))

    tracked = venue_ids & catalogued
    tracked_captured = tracked & local_ids
    tracked_missing = tracked - local_ids
    venue_captured = venue_ids & local_ids

    tracked_pct = 100.0 * len(tracked_captured) / len(tracked) if tracked else 0.0
    venue_pct = 100.0 * len(venue_captured) / len(venue_ids) if venue_ids else 0.0

    print(f"RECONCILIATION  {since.date()} -> {until.date()}  ({args.days}d)")
    print(f"  resolved per venue      : {len(venue_ids)}")
    print(f"  of those, in catalogue  : {len(tracked)}")
    print(f"  outcomes recorded       : {len(local_ids)}")
    print()
    print(
        f"  TRACKED coverage : {tracked_pct:6.2f}%   ({len(tracked_captured)}/{len(tracked)})"
        f"   <- gate {args.gate:.0f}%"
    )
    print(
        f"  VENUE   coverage : {venue_pct:6.2f}%   ({len(venue_captured)}/{len(venue_ids)})"
        f"   <- reported, not gated"
    )

    if tracked_missing:
        print("\n  tracked but no outcome recorded:")
        for market_id in sorted(tracked_missing)[: args.limit]:
            print(f"    {market_id}")
        if len(tracked_missing) > args.limit:
            print(f"    ... {len(tracked_missing) - args.limit} more")

    if not tracked:
        print("\nNo catalogued market resolved in this window. Coverage is vacuous,")
        print("not passing: run the snapshotter long enough for tracked markets to settle.")
        return 1
    if tracked_pct < args.gate:
        print(f"\nBELOW GATE: {tracked_pct:.2f}% < {args.gate:.0f}%")
        return 1
    print("\nGate met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
