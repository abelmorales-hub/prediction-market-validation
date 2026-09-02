"""Markdown validation report.

Forecasts arrive as data — `{name: probabilities}` — never as an import. `src.scoring`
cannot see `src.baselines` or `src.models`, enforced by import-linter, so this module has
no way to know what produced a column and no way to treat one favourably.

The report's job is to make an underpowered result *look* underpowered. A mean(d) of
+0.004 with n=180 is not a small edge, it is no measurement at all, and printed in a bare
table next to a confidence interval it reads like the former. Every row therefore carries
its own power verdict, and §5's required-n is computed from the **empirical** sigma_d
rather than the doc's 0.06 working assumption.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
from numpy.typing import NDArray

from src.scoring.bootstrap import clustered_bootstrap, required_n
from src.scoring.metrics import auc, bss, ece, paired_diff
from src.scoring.stratify import (
    PRICE_BUCKETS,
    TAIL_BUCKETS,
    assign_price_bucket,
    bucket_labels,
)

FloatArray = NDArray[np.float64]

MIN_STRATUM_N = 20


@dataclass(frozen=True)
class Row:
    """One forecast scored on one stratum."""

    name: str
    n: int
    n_clusters: int
    effective_n: float
    mean_d: float
    ci_low: float
    ci_high: float
    brier_skill: float
    calibration_error: float
    discrimination: float
    # None when mean(d) is exactly zero: the n needed to detect an effect of zero is not
    # a large number, it is undefined. The market baseline always lands here.
    required_effective_n: int | None

    @property
    def excludes_zero(self) -> bool:
        return (self.ci_low > 0.0) or (self.ci_high < 0.0)

    @property
    def underpowered(self) -> bool:
        """Not enough effective observations to have detected the effect it reports."""
        need = self.required_effective_n
        return need is not None and self.effective_n < need

    @property
    def verdict(self) -> str:
        if self.required_effective_n is None:
            return "reference"
        if self.underpowered:
            return "UNDERPOWERED"
        return "adequate" if self.excludes_zero else "null"


def score(
    name: str,
    p_model: object,
    p_market: object,
    y: object,
    clusters: object,
    n_iter: int = 10_000,
    alpha: float = 0.05,
    seed: int = 0,
) -> Row:
    """Score one forecast against the market on one set of rows."""
    d = paired_diff(p_model, p_market, y)
    boot = clustered_bootstrap(d, clusters, n_iter=n_iter, alpha=alpha, seed=seed)

    # sigma from this sample, not the doc's 0.06 placeholder. required_n scales with
    # sigma^2, so using the assumption where a measurement exists can be wrong by an
    # order of magnitude in either direction.
    sigma_d = float(np.std(d, ddof=1)) if d.size > 1 else 0.0
    delta = abs(boot.mean)
    need = required_n(delta, sigma_d=sigma_d) if delta > 0.0 and sigma_d > 0.0 else None

    return Row(
        name=name,
        n=boot.n,
        n_clusters=boot.n_clusters,
        effective_n=boot.effective_n,
        mean_d=boot.mean,
        ci_low=boot.ci_low,
        ci_high=boot.ci_high,
        brier_skill=bss(p_model, p_market, y),
        calibration_error=ece(p_model, y),
        discrimination=auc(p_model, y),
        required_effective_n=need,
    )


def _fmt(x: float, places: int = 4) -> str:
    return "n/a" if np.isnan(x) else f"{x:+.{places}f}" if places == 4 else f"{x:.{places}f}"


def _table(rows: Sequence[Row]) -> str:
    head = (
        "| forecast | n | clusters | eff. n | mean(d) | 95% CI | BSS | ECE | AUC | "
        "need eff. n | verdict |\n"
        "|---|--:|--:|--:|--:|:--:|--:|--:|--:|--:|:--|"
    )
    out = [head]
    for r in rows:
        need = "n/a" if r.required_effective_n is None else f"{r.required_effective_n:,}"
        out.append(
            f"| {r.name} | {r.n:,} | {r.n_clusters:,} | {r.effective_n:,.0f} | "
            f"{_fmt(r.mean_d)} | [{_fmt(r.ci_low)}, {_fmt(r.ci_high)}] | "
            f"{_fmt(r.brier_skill)} | {_fmt(r.calibration_error, 3)} | "
            f"{_fmt(r.discrimination, 3)} | {need} | {r.verdict} |"
        )
    return "\n".join(out)


def _score_all(
    forecasts: Mapping[str, object],
    p_market: FloatArray,
    y: FloatArray,
    clusters: NDArray[np.generic],
    mask: NDArray[np.bool_] | None,
    n_iter: int,
    alpha: float,
    seed: int,
) -> list[Row]:
    sel: slice | NDArray[np.bool_] = slice(None) if mask is None else mask
    return [
        score(
            name,
            np.asarray(p, dtype=np.float64)[sel],
            p_market[sel],
            y[sel],
            clusters[sel],
            n_iter=n_iter,
            alpha=alpha,
            seed=seed,
        )
        for name, p in forecasts.items()
    ]


def build_report(
    forecasts: Mapping[str, object],
    p_market: object,
    y: object,
    clusters: object,
    categories: object | None = None,
    market_mid: object | None = None,
    edges: tuple[float, ...] = PRICE_BUCKETS,
    min_stratum_n: int = MIN_STRATUM_N,
    n_iter: int = 10_000,
    alpha: float = 0.05,
    seed: int = 0,
) -> str:
    """Full markdown report: overall, tails excluded, by price bucket, by category.

    `market_mid` defaults to `p_market` — the price *is* the mid in the normal case, but
    they are separate arguments because a forecast may be scored against a benchmark that
    is not the bucketing variable.
    """
    pm = np.asarray(p_market, dtype=np.float64)
    ya = np.asarray(y, dtype=np.float64)
    cl = np.asarray(clusters)
    if not forecasts:
        raise ValueError("no forecasts to score")
    if not (pm.shape == ya.shape == cl.shape):
        raise ValueError("p_market, y and clusters must align one-to-one")

    mid = pm if market_mid is None else np.asarray(market_mid, dtype=np.float64)
    parts: list[str] = []

    parts.append("# Forecast validation report\n")
    parts.append(f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC\n")
    parts.append(
        f"`n` = {pm.size:,} paired observations across {len(np.unique(cl)):,} event "
        f"families. The benchmark is the market price at the same timestamp as each "
        f"prediction (P1).\n"
    )
    parts.append(
        "**Reading this table.** `eff. n` is the nominal n divided by the observed design "
        "effect - clustered evidence is worth less than its row count. `need eff. n` is "
        "section 5's requirement to detect the effect size actually observed, at alpha 0.05 and "
        "80% power, using this sample's sigma_d. A row marked `UNDERPOWERED` did not have "
        "the evidence to find what it reports, **whatever its confidence interval says**.\n"
    )

    parts.append("\n## Overall\n")
    parts.append(_table(_score_all(forecasts, pm, ya, cl, None, n_iter, alpha, seed)))

    labels = bucket_labels(edges)
    idx = assign_price_bucket(mid, edges)
    keep = np.array([labels[i] not in TAIL_BUCKETS for i in idx])
    parts.append("\n## Excluding tail price buckets (G3)\n")
    parts.append(
        "section 4.2: almost all fake edge lives in the tails. An improvement that disappears "
        "here is longshot bias, not skill.\n"
    )
    if int(keep.sum()) < min_stratum_n:
        parts.append(f"_Only {int(keep.sum())} non-tail observations; not reported._")
    else:
        parts.append(_table(_score_all(forecasts, pm, ya, cl, keep, n_iter, alpha, seed)))

    parts.append("\n## By price bucket\n")
    parts.extend(
        _section(
            labels,
            [labels[i] for i in idx],
            forecasts,
            pm,
            ya,
            cl,
            min_stratum_n,
            n_iter,
            alpha,
            seed,
        )
    )

    if categories is not None:
        cats = [str(c) for c in np.asarray(categories)]
        if len(cats) != pm.size:
            raise ValueError("categories must align one-to-one")
        parts.append("\n## By category\n")
        parts.extend(
            _section(
                sorted(set(cats)),
                cats,
                forecasts,
                pm,
                ya,
                cl,
                min_stratum_n,
                n_iter,
                alpha,
                seed,
            )
        )

    return "\n".join(parts) + "\n"


def _section(
    labels: Sequence[str],
    keys: Sequence[str],
    forecasts: Mapping[str, object],
    pm: FloatArray,
    ya: FloatArray,
    cl: NDArray[np.generic],
    min_stratum_n: int,
    n_iter: int,
    alpha: float,
    seed: int,
) -> list[str]:
    keys_arr = np.asarray(keys)
    out: list[str] = []
    for label in labels:
        mask = keys_arr == label
        n = int(mask.sum())
        if n < min_stratum_n:
            # Omitted, but *named*. Silently dropping a thin stratum hides which slices
            # the dataset cannot speak to, and that absence is itself a finding.
            out.append(f"\n### {label}\n\n_n = {n}, below the {min_stratum_n}-row floor._")
            continue
        out.append(f"\n### {label}\n")
        out.append(_table(_score_all(forecasts, pm, ya, cl, mask, n_iter, alpha, seed)))
    return out
