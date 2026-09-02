"""Generate the validation report over whatever has resolved.

Run: uv run python -m scripts.score [--out reports/validation.md] [--horizon T-1h]

Prints and writes markdown. If nothing has resolved yet it says so plainly and exits 0:
an empty result is a true statement about the data, not a failure of the script.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from src.analysis.frame import build_frame
from src.config import get_settings
from src.scoring.bootstrap import required_n
from src.scoring.report import build_report
from src.storage.supabase_repo import Repo, connect


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/validation.md")
    ap.add_argument("--horizon", default=None, help="restrict to one horizon label")
    ap.add_argument("--n-iter", type=int, default=10_000)
    args = ap.parse_args()

    cfg = get_settings()
    if not cfg.supabase_db_url:
        print("SUPABASE_DB_URL not set", file=sys.stderr)
        return 2

    with connect(cfg.supabase_db_url) as conn:
        rows = Repo(conn).scored_pairs()

    frame = build_frame(rows)
    if args.horizon:
        frame = frame.restricted_to(args.horizon)

    if len(frame) == 0:
        print(f"scored_pairs returned {len(rows)} rows; 0 complete scorable observations.")
        print()
        print("Nothing has resolved yet, so there is no report to make. This is the")
        print("expected state until tracked markets begin settling -- forward-testing")
        print("cannot be hurried, which is the entire reason it is trustworthy.")
        print()
        print("Power required (design doc section 5, sigma_d = 0.06 assumed):")
        for delta in (0.020, 0.010, 0.005, 0.002):
            print(f"  detect mean(d) = {delta:.3f}  ->  n = {required_n(delta):,}")
        return 0

    md = build_report(
        frame.forecasts,
        frame.market_mid,
        frame.y,
        frame.clusters,
        categories=frame.categories,
        n_iter=args.n_iter,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"\nwritten to {out}  ({len(frame)} observations, {frame.n_clusters} clusters)")
    print(f"generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")
    return 0


if __name__ == "__main__":
    sys.exit(main())
