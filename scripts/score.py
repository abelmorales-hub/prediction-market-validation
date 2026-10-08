"""Generate the validation report over whatever has resolved.

Run: uv run python -m scripts.score [--out reports/validation.md] [--horizon T-1h]

Prints and writes markdown. If nothing has resolved yet it says so plainly and exits 0:
an empty result is a true statement about the data, not a failure of the script.

**Frozen models are withheld unless named.** H2 gets one pre-committed G3 reading, and
this command used to spend it on anyone who ran it: `build_frame` picks up every
model_version in the table, so an unfiltered run printed H2's BSS. Pass
`--model gefs_ensemble_raw` to take that look deliberately. See docs/pre_declaration.md.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from src.analysis.frame import (
    FROZEN_MODELS,
    build_frame,
    g3_sample,
    read_exclusions,
    resolve_models,
)
from src.config import get_settings
from src.scheduler.gaps import parse_since
from src.scoring.bootstrap import required_n
from src.scoring.report import build_report
from src.storage.supabase_repo import Repo, connect


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/validation.md")
    ap.add_argument("--horizon", default=None, help="restrict to one horizon label")
    ap.add_argument("--n-iter", type=int, default=10_000)
    ap.add_argument(
        "--model",
        action="append",
        metavar="MODEL_VERSION",
        help="score only this model_version; repeatable. Default: every model except the "
        f"frozen ones ({', '.join(sorted(FROZEN_MODELS))}), whose score is a single "
        "pre-committed look",
    )
    # Amendment 11's sample, as flags rather than defaults: an unqualified run still
    # scores everything, which is what the health gates want. The G3 run passes all three.
    ap.add_argument("--since", help="ISO instant; drop predictions stamped before it")
    ap.add_argument("--until", help="ISO instant; drop predictions stamped at or after it")
    ap.add_argument(
        "--exclude",
        metavar="PATH",
        help="file of market ids to exclude, from `gap_report --critical-markets`",
    )
    ap.add_argument(
        "--require-n",
        type=int,
        metavar="N",
        help="refuse to score unless the sample has N complete observations. A guard on "
        "an irreversible act: H2 gets one look, and `715` is Amendment 6's n. It can "
        "only abort, never change what a report that does run says",
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

    if args.since or args.until or args.exclude:
        rows, census = g3_sample(
            rows,
            since=parse_since(args.since) if args.since else None,
            until=parse_since(args.until) if args.until else None,
            exclude=read_exclusions(Path(args.exclude)) if args.exclude else (),
        )
        print("SAMPLE (docs/pre_declaration.md, Amendment 11)")
        for line in census.as_lines():
            print(f"  {line}")
        print()

    frame = build_frame(rows, models=models)
    if args.horizon:
        frame = frame.restricted_to(args.horizon)

    # Before `build_report`, which is where a score first exists. `build_frame` pivots and
    # drops unresolved rows but computes nothing, so stopping here spends no look.
    if args.require_n is not None and len(frame) < args.require_n:
        print(
            f"{len(frame)} complete observations, {args.require_n} required: "
            f"{args.require_n - len(frame)} short. Nothing scored."
        )
        print("A pre-committed n is only a commitment while the run can still be refused.")
        return 1

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
