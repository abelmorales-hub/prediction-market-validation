"""Scoring metrics. Pure arithmetic over arrays.

This module must not import from models/ or baselines/ — enforced by import-linter, not
by discipline. A scorer that can see the model is a scorer that will eventually be bent
to flatter it.

Everything here is elementwise and unweighted. The benchmark is always the market price
at the *same timestamp* as the prediction: P1 in the design doc. If the two timestamps
drift apart, every number below is quietly wrong while staying in a plausible range,
which is what G2 exists to catch.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.stats import rankdata

FloatArray = NDArray[np.float64]

# log_loss is unbounded at 0 and 1. The database already refuses p_hat outside (0,1),
# so this clip is a second line of defence, not the primary one.
EPS = 1e-15


def _as_arrays(p: object, y: object) -> tuple[FloatArray, FloatArray]:
    """Validate at the trust boundary. Silent shape or domain errors here would produce
    a number rather than an exception, and a number is what gets believed."""
    pa = np.asarray(p, dtype=np.float64)
    ya = np.asarray(y, dtype=np.float64)
    if pa.shape != ya.shape:
        raise ValueError(f"shape mismatch: p {pa.shape} vs y {ya.shape}")
    if pa.size == 0:
        raise ValueError("empty input")
    if not np.all(np.isfinite(pa)):
        raise ValueError("p contains non-finite values")
    if np.any(pa < 0.0) or np.any(pa > 1.0):
        raise ValueError("p outside [0, 1]")
    if not np.all((ya == 0.0) | (ya == 1.0)):
        raise ValueError("y must be binary 0/1")
    return pa, ya


def brier(p: object, y: object) -> FloatArray:
    """Elementwise squared error. Bounded in [0, 1] for p in [0,1], y in {0,1}."""
    pa, ya = _as_arrays(p, y)
    return np.asarray((pa - ya) ** 2, dtype=np.float64)


def log_loss(p: object, y: object) -> FloatArray:
    """Elementwise negative log likelihood, clipped away from the asymptotes."""
    pa, ya = _as_arrays(p, y)
    pc = np.clip(pa, EPS, 1.0 - EPS)
    return np.asarray(-(ya * np.log(pc) + (1.0 - ya) * np.log(1.0 - pc)), dtype=np.float64)


def paired_diff(p_model: object, p_market: object, y: object) -> FloatArray:
    """d_i = BS_market,i - BS_model,i.

    Positive means the model beat the market on that case. Pairing is the whole point:
    the two predictions describe the same event at the same instant, so the difference
    removes the event's own difficulty from the comparison.
    """
    bs_model = brier(p_model, y)
    bs_market = brier(p_market, y)
    if bs_model.shape != bs_market.shape:
        raise ValueError("model and market predictions must align one-to-one")
    return np.asarray(bs_market - bs_model, dtype=np.float64)


def bss(p_model: object, p_market: object, y: object) -> float:
    """Brier Skill Score: 1 - sum(BS_model) / sum(BS_market).

    Reads as fractional improvement over the market. A sustained 0.03-0.05 would be
    exceptional. Feeding the market's own price in as the model must give exactly 0.
    """
    bs_model = brier(p_model, y)
    bs_market = brier(p_market, y)
    denominator = float(bs_market.sum())
    if denominator == 0.0:
        # The market was perfectly right on every case. Skill over a perfect forecaster
        # is undefined, not zero, and returning 0 would read as "no edge" rather than
        # "unanswerable".
        return float("nan")
    return 1.0 - float(bs_model.sum()) / denominator


@dataclass(frozen=True)
class ReliabilityResult:
    bin_edges: FloatArray
    mean_predicted: FloatArray
    observed_rate: FloatArray
    counts: NDArray[np.int64]

    @property
    def populated(self) -> NDArray[np.bool_]:
        return self.counts > 0


def reliability_curve(p: object, y: object, n_bins: int = 10) -> ReliabilityResult:
    """Predicted probability vs observed frequency, in equal-width bins.

    Empty bins are reported as NaN rather than 0. A bin nobody predicted into has no
    observed rate, and plotting it at zero invents a calibration failure.
    """
    pa, ya = _as_arrays(p, y)
    if n_bins < 1:
        raise ValueError("n_bins must be >= 1")
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    # Rightmost edge inclusive so p == 1.0 lands in the last bin, not one past it.
    idx = np.clip(np.digitize(pa, edges[1:-1], right=False), 0, n_bins - 1)

    counts = np.bincount(idx, minlength=n_bins).astype(np.int64)
    sum_pred = np.bincount(idx, weights=pa, minlength=n_bins)
    sum_obs = np.bincount(idx, weights=ya, minlength=n_bins)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean_pred = np.where(counts > 0, sum_pred / counts, np.nan)
        observed = np.where(counts > 0, sum_obs / counts, np.nan)
    return ReliabilityResult(edges, mean_pred, observed, counts)


def ece(p: object, y: object, n_bins: int = 10) -> float:
    """Expected calibration error: count-weighted mean gap between predicted and
    observed, over populated bins only."""
    curve = reliability_curve(p, y, n_bins)
    mask = curve.populated
    if not mask.any():
        return float("nan")
    gaps = np.abs(curve.observed_rate[mask] - curve.mean_predicted[mask])
    weights = curve.counts[mask].astype(np.float64)
    return float((gaps * weights).sum() / weights.sum())


def auc(p: object, y: object) -> float:
    """Area under the ROC curve, via the rank-sum identity.

    Ties get average ranks, which is what makes this agree with the probabilistic
    definition when a model emits repeated probabilities — common for a market price
    quantised to the tick.
    """
    pa, ya = _as_arrays(p, y)
    pos = ya == 1.0
    n_pos = int(pos.sum())
    n_neg = int(pa.size - n_pos)
    if n_pos == 0 or n_neg == 0:
        # One class only: discrimination is undefined, not 0.5.
        return float("nan")
    ranks = rankdata(pa)
    rank_sum = float(ranks[pos].sum())
    return (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)
