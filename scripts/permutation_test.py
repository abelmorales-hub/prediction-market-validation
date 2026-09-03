"""G2b: shuffle the outcomes and check no forecast scores above its own null.

Run: uv run python -m scripts.permutation_test [--n-iter 2000] [--horizon T-24h]

Exits 1 if any forecast lands in the upper tail of its permuted null, which is the
leakage signature. A flat forecast having a *positive* null mean is expected, not a
failure -- see G2b in the design doc.
"""

from __future__ import annotations

import argparse
import sys

from src.analysis.frame import build_frame
from src.config import get_settings
from src.scoring.permutation import permutation_test
from src.storage.supabase_repo import Repo, connect


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-iter", type=int, default=2000)
    ap.add_argument("--horizon", help="restrict to one horizon label")
    args = ap.parse_args()

    cfg = get_settings()
    if not cfg.supabase_db_url:
        print("SUPABASE_DB_URL not set", file=sys.stderr)
        return 2

    with connect(cfg.supabase_db_url) as conn:
        frame = build_frame(Repo(conn).scored_pairs())
    if args.horizon:
        frame = frame.restricted_to(args.horizon)

    if len(frame) == 0:
        print("no resolved observations yet; nothing to permute")
        return 0

    print(f"n = {len(frame)}  clusters = {frame.n_clusters}  base rate = {frame.y.mean():.3f}")
    print(f"permutations = {args.n_iter}\n")

    results = [
        permutation_test(name, frame.forecasts[name], frame.market_mid, frame.y, args.n_iter)
        for name in sorted(frame.forecasts)
    ]

    header = (
        f"{'forecast':18s} {'observed':>10s} {'null mean':>10s} "
        f"{'null sd':>9s} {'p':>7s}  verdict"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r.name:18s} {r.observed:+10.5f} {r.null_mean:+10.5f} "
            f"{r.null_sd:9.5f} {r.p_value:7.3f}  {r.verdict}"
        )

    print()
    for r in results:
        if r.degenerate:
            print(f"{r.name}: is the market itself, so BSS is 0 under every permutation.")
        elif r.sharpness < r.market_sharpness:
            print(
                f"{r.name}: flatter than the market "
                f"({r.sharpness:.3f} vs {r.market_sharpness:.3f}), so a null mean above zero "
                "is expected."
            )

    leaks = [r for r in results if r.leaks]
    if leaks:
        print("\nG2b FAILED: " + ", ".join(r.name for r in leaks))
        print("An observed score in the upper tail means the forecast saw the outcome.")
        return 1

    print("\nG2b passed: no forecast scores above its permuted null.")
    if all(r.degenerate for r in results):
        print("Every forecast was degenerate, so this run tested nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
