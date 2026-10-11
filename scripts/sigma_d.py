"""Estimate H2's planning sigma_d without looking at a single outcome.

Run: uv run python -m scripts.sigma_d [--model gefs_ensemble_raw] [--selfcheck]

Pre-declaration §6 fixes the planning assumption at sigma_d = 0.06 and says to revise it
"only once H2 has produced predictions that actually differ from the market's". H2 now
does. The obvious way to revise it -- score the paired Brier differences on resolved
markets -- would spend the single G3 run of §4, because the sample standard deviation
and the sample mean come off the same rows.

So sigma_d is derived instead, from the (p_hat, market_mid) pairs alone, under the null
that the market is calibrated: y ~ Bernoulli(m). For one observation, with
d = (p_hat - y)^2 - (m - y)^2 and D = p_hat - m,

    E[d | m]   = D^2                        (a market that is exactly right costs H2 D^2)
    Var[d | m] = 4 D^2 m (1 - m)            (because d(y=1) - d(y=0) = -2D)

and the law of total variance gives the sample sigma_d as
sqrt(mean(Var[d|m]) + var(E[d|m])). No outcome is read, so this is not a peek: the null
hypothesis supplies the outcome distribution, which is exactly what a power calculation
is entitled to assume. If the market is *not* calibrated the true sigma_d differs -- but
that is the alternative, and planning under the null is the conservative direction.

Reporting only. Exits 0 regardless, and writes nothing.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import numpy as np
from numpy.typing import NDArray

from src.analysis.horizons import HorizonPoint
from src.analysis.station_bias import MODEL_VERSION as H3_MODEL_VERSION
from src.analysis.station_bias import StationBiasForecaster
from src.config import get_settings
from src.scoring.bootstrap import required_n
from src.storage.supabase_repo import Repo, connect

FloatArray = NDArray[np.float64]

# §6: effective n is 1/3 to 1/5 of nominal once clustered by event family.
CLUSTER_INFLATION = (3, 5)


def sigma_d(p_hat: FloatArray, mid: FloatArray) -> float:
    """Standard deviation of the paired Brier difference under a calibrated market."""
    delta = p_hat - mid
    mean_d = delta**2
    var_within = 4.0 * delta**2 * mid * (1.0 - mid)
    return float(np.sqrt(var_within.mean() + mean_d.var()))


def _first_emission(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per (market, horizon), earliest ts -- the same rule as `build_frame`.

    A re-emitted horizon carries a later price. Counting both would weight busy markets
    twice while saying nothing new about the spread.
    """
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for r in sorted(rows, key=lambda r: r["ts"]):
        seen.setdefault((r["market_id"], r["horizon_label"]), r)
    return list(seen.values())


def _counterfactual_pairs(repo: Repo, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recompute H3's p_hat on prices it has not been emitted against yet.

    Sizing a study needs sigma_d *before* its window opens, and sigma_d needs (p_hat, m)
    pairs -- but p_hat for a model that has not run yet is not in `predictions`. It is
    recoverable: p_hat is a pure function of the stored ensemble record and the market's
    strike, and the price comes off another model's row at the same instant. So the pairs
    are rebuilt by running the real forecaster over reconstructed horizon points.

    No outcome is read, which is what keeps this a power calculation rather than a look.
    The honest caveat runs the other way: the offsets were fitted on days this sample
    overlaps, so |p_hat - m| is likely *smaller* here than it will be out of sample. Since
    n grows as that disagreement shrinks, the n this produces errs large.
    """
    forecaster = StationBiasForecaster(repo, repo.market_meta([str(r["market_id"]) for r in rows]))
    out: list[dict[str, Any]] = []
    for row in rows:
        ts = row["ts"]
        point = HorizonPoint(
            market_id=str(row["market_id"]),
            horizon_label=str(row["horizon_label"]),
            # Only market_id and ts reach the forecaster. The rest is price context, and
            # the price that matters is the stored one, carried through below.
            target=ts,
            ts=ts,
            mid=float(row["market_mid"]),
            bid=float(row["market_mid"]),
            ask=float(row["market_mid"]),
        )
        forecast = forecaster(point).get(H3_MODEL_VERSION)
        if forecast is None:
            continue
        out.append({**row, "p_hat": forecast.p_hat})
    print("recomputed: " + "  ".join(f"{k}={v}" for k, v in forecaster.stats.as_dict().items()))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gefs_ensemble_raw", help="model_version to size")
    ap.add_argument("--selfcheck", action="store_true", help="run the maths check and exit")
    ap.add_argument(
        "--counterfactual",
        action="store_true",
        help=f"size {H3_MODEL_VERSION} instead: keep --model's prices and timestamps but "
        "recompute p_hat from the stored ensembles, for a model that has not emitted yet",
    )
    args = ap.parse_args()

    if args.selfcheck:
        _selfcheck()
        print("selfcheck OK")
        return 0

    cfg = get_settings()
    if not cfg.supabase_db_url:
        print("SUPABASE_DB_URL not set", file=sys.stderr)
        return 2

    with connect(cfg.supabase_db_url) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select p.market_id, p.ts, p.horizon_label, p.p_hat, p.market_mid,
                   m.event_family
            from predictions p
            join markets m on m.market_id = p.market_id
            where p.model_version = %s
            order by p.ts
            """,
            (args.model,),
        )
        rows = _first_emission(list(cur.fetchall()))
        if args.counterfactual:
            rows = _counterfactual_pairs(Repo(conn), rows)

    if not rows:
        print(f"no predictions for model_version={args.model!r}")
        return 0

    p_hat = np.array([float(r["p_hat"]) for r in rows], dtype=np.float64)
    mid = np.array([float(r["market_mid"]) for r in rows], dtype=np.float64)
    families = {str(r["event_family"]) for r in rows}
    delta = p_hat - mid
    sd = sigma_d(p_hat, mid)

    print(f"model_version : {args.model}")
    print(f"observations  : {len(rows)}  ({len(families)} event families)")
    print(f"|p_hat - mid| : mean {np.abs(delta).mean():.4f}  max {np.abs(delta).max():.4f}")
    print(f"sigma_d       : {sd:.4f}   (§6 planning assumption: 0.0600)")
    print(
        f"cost if the market is exactly calibrated: {float((delta**2).mean()):.4f} "
        "Brier per observation"
    )
    print()
    print("nominal n, and n after the §6 clustering correction (x3 to x5):")
    # mean(D^2) is the natural scale for *this* model, not a pre-declared one: it is what
    # H2 loses by if the market is right, so an effect that small is the least it can
    # plausibly be moving. The §6 grid was written before any model with real
    # disagreement existed, and 0.005 on a model that bets 0.165 away from the price is
    # not a hypothesis anyone would need weeks of data to reject.
    natural = float((delta**2).mean())
    for effect in (0.020, 0.010, 0.005, natural):
        n = required_n(effect, sigma_d=sd)
        lo, hi = (n * k for k in CLUSTER_INFLATION)
        tag = "  <- this model's own scale" if effect == natural else ""
        print(f"  delta = {effect:.3f}   nominal {n:8,d}   clustered {lo:8,d} - {hi:8,d}{tag}")
    return 0


def _selfcheck() -> None:
    """The closed form must match a simulation of the thing it claims to describe."""
    rng = np.random.default_rng(0)
    mid = rng.uniform(0.05, 0.95, size=400)
    p_hat = np.clip(mid + rng.normal(0, 0.15, size=400), 0.01, 0.99)

    # Agreeing with the market is a zero-variance difference, whatever the prices are.
    assert sigma_d(mid.copy(), mid) == 0.0

    # Monte Carlo the definition itself: draw outcomes from the null, score both
    # forecasts, take the standard deviation of the paired difference.
    draws = rng.random((4000, mid.size)) < mid
    d = (p_hat - draws) ** 2 - (mid - draws) ** 2
    empirical = float(d.std())
    closed = sigma_d(p_hat, mid)
    assert abs(empirical - closed) < 0.01 * max(empirical, closed), (empirical, closed)


if __name__ == "__main__":
    raise SystemExit(main())
