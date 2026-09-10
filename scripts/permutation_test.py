"""G2b: shuffle the outcomes and check no forecast scores above its own null.

Run: uv run python -m scripts.permutation_test [--n-iter 2000] [--horizon T-24h]

Exits 1 if any forecast lands in the upper tail of its permuted null, which is the
leakage signature. A flat forecast having a *positive* null mean is expected, not a
failure -- see G2b in the design doc.

Frozen models are withheld unless named with `--model`, same as `scripts.score`: this
prints an observed BSS per forecast, so it leaked H2's score just as readily.
"""

from __future__ import annotations

import argparse
import sys

from src.analysis.frame import FROZEN_MODELS, build_frame, resolve_models
from src.config import get_settings
from src.scoring.permutation import permutation_test
from src.storage.supabase_repo import Repo, connect


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-iter", type=int, default=2000)
    ap.add_argument("--horizon", help="restrict to one horizon label")
    ap.add_argument(
        "--model",
        action="append",
        metavar="MODEL_VERSION",
        help="permute only this model_version; repeatable. Default: every model except the "
        f"frozen ones ({', '.join(sorted(FROZEN_MODELS))})",
    )
    args = ap.parse_args()

    cfg = get_settings()
    if not cfg.supabase_db_url:
        print("SUPABASE_DB_URL not set", file=sys.stderr)
        return 2

    with connect(cfg.supabase_db_url) as conn:
        rows = Repo(conn).scored_pairs()

    try:
        models = resolve_models(rows, args.model)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    withheld = sorted({str(r["model_version"]) for r in rows} - models)
    if withheld:
        print(f"withheld: {', '.join(withheld)}  (name it with --model to include it)\n")

    frame = build_frame(rows, models=models)
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
