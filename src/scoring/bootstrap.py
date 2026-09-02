"""Clustered (block) bootstrap over the paired differences.

P5: observations are not independent. Every market in one MLB slate shares the same
shocks; "NOAA names the Nth storm" markets are one bet sliced up. Resampling individual
rows would treat those as independent evidence and shrink the interval by a factor of
3-5, which is precisely how a null result gets published as an edge.

So whole clusters are resampled with replacement, never rows.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class BootstrapResult:
    mean: float
    ci_low: float
    ci_high: float
    n: int
    n_clusters: int
    effective_n: float
    design_effect: float

    @property
    def excludes_zero(self) -> bool:
        """G3's condition. Both bounds on the same side of zero."""
        return (self.ci_low > 0.0) or (self.ci_high < 0.0)


def clustered_bootstrap(
    d: object,
    clusters: object,
    n_iter: int = 10_000,
    alpha: float = 0.05,
    seed: int = 0,
) -> BootstrapResult:
    """Percentile CI for mean(d), resampling whole clusters.

    `effective_n` is reported so the result is directly comparable with the power table
    in design doc §5, where the required n is quoted for independent observations. It is
    derived from the observed design effect — the ratio of the clustered sampling
    variance to the variance an i.i.d. sample of the same size would have — rather than
    from an assumed ICC, so it reflects the correlation actually present in this data.
    """
    dv = np.asarray(d, dtype=np.float64)
    cv = np.asarray(clusters)
    if dv.shape != cv.shape:
        raise ValueError(f"shape mismatch: d {dv.shape} vs clusters {cv.shape}")
    if dv.size == 0:
        raise ValueError("empty input")
    if not np.all(np.isfinite(dv)):
        raise ValueError("d contains non-finite values")
    if n_iter < 1:
        raise ValueError("n_iter must be >= 1")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")

    _, inverse = np.unique(cv, return_inverse=True)
    n_clusters = int(inverse.max()) + 1
    # Per-cluster sums and counts: a resampled mean is then a ratio of two gathers,
    # which keeps 10k iterations over thousands of markets in the millisecond range.
    sums = np.bincount(inverse, weights=dv, minlength=n_clusters)
    counts = np.bincount(inverse, minlength=n_clusters).astype(np.float64)

    rng = np.random.default_rng(seed)
    picks = rng.integers(0, n_clusters, size=(n_iter, n_clusters))
    boot_means = sums[picks].sum(axis=1) / counts[picks].sum(axis=1)

    lo, hi = np.quantile(boot_means, [alpha / 2.0, 1.0 - alpha / 2.0])

    n = int(dv.size)
    se_clustered = float(boot_means.std(ddof=1))
    se_iid = float(dv.std(ddof=1) / np.sqrt(n)) if n > 1 else 0.0
    if se_iid > 0.0:
        # Clustering cannot manufacture information, so a design effect below 1 is
        # sampling noise. Clamping keeps effective_n <= n rather than claiming the
        # correlated sample is worth more than an independent one.
        design_effect = max((se_clustered / se_iid) ** 2, 1.0)
    else:
        design_effect = 1.0

    return BootstrapResult(
        mean=float(dv.mean()),
        ci_low=float(lo),
        ci_high=float(hi),
        n=n,
        n_clusters=n_clusters,
        effective_n=n / design_effect,
        design_effect=design_effect,
    )


def required_n(delta: float, sigma_d: float = 0.06, alpha: float = 0.05, power: float = 0.8) -> int:
    """Paired-difference sample size, design doc §5: n = (z_a/2 + z_b)^2 * sigma^2 / delta^2.

    sigma_d defaults to the doc's working assumption of 0.06. **Replace it with the
    empirical value once ~100 markets have resolved** — the whole table is only as good
    as that guess.
    """
    from scipy.stats import norm

    if delta <= 0:
        raise ValueError("delta must be > 0")
    z = norm.ppf(1.0 - alpha / 2.0) + norm.ppf(power)
    return int(np.ceil(z**2 * sigma_d**2 / delta**2))
