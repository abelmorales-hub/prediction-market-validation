"""Arithmetic baselines. Every one is closed form — no fitting, no state, no inference.

These exist to give the scorer something to fail against. A pipeline that cannot show
`baseline_market` scoring exactly zero is a pipeline whose positive results mean nothing,
so the trivial baselines are more load-bearing than the interesting ones.

Nothing here may import from `src.scoring`. The dependency runs the other way: the report
takes forecasts as data. That keeps the scorer unable to see what produced a number.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


def _as_mid(market_mid: object) -> FloatArray:
    mid = np.asarray(market_mid, dtype=np.float64)
    if mid.size == 0:
        raise ValueError("empty input")
    if not np.all(np.isfinite(mid)):
        raise ValueError("market_mid contains non-finite values")
    if np.any(mid < 0.0) or np.any(mid > 1.0):
        raise ValueError("market_mid outside [0, 1]")
    return mid


def baseline_market(market_mid: object) -> FloatArray:
    """The market price, unchanged.

    The null model. Scored against the market it must give mean(d) == 0 and BSS == 0 to
    machine precision; G2 asserts exactly that. Any drift here is a bug in the pairing —
    a timestamp misalignment shows up first as this baseline scoring nonzero.
    """
    return _as_mid(market_mid).copy()


def baseline_shrink(market_mid: object, alpha: float = 0.9, centre: float = 0.5) -> FloatArray:
    """Market price pulled toward `centre`: alpha * mid + (1 - alpha) * centre.

    This is the one that catches a credulous pipeline. Prediction markets overprice
    longshots, so shrinking toward the centre wins in the [0, 0.05] and (0.95, 1] buckets
    while containing no information whatsoever. If a real model's edge does not survive
    `excluding_tails`, it has merely rediscovered this.
    """
    if not 0.0 <= alpha <= 1.0:
        raise ValueError("alpha must be in [0, 1]")
    if not 0.0 <= centre <= 1.0:
        raise ValueError("centre must be in [0, 1]")
    return np.asarray(alpha * _as_mid(market_mid) + (1.0 - alpha) * centre, dtype=np.float64)


def baseline_base_rate(y_prior: object, size: int) -> FloatArray:
    """A constant forecast at the resolution rate observed in `y_prior`.

    **`y_prior` must not overlap the outcomes being scored.** The base rate is a forecast,
    so it is subject to P2 like any other: computing it from the same rows it is scored
    against leaks the answer and produces a baseline that beats the market for free. The
    caller owns that split — this function cannot check it, which is why it is stated here
    rather than assumed.
    """
    prior = np.asarray(y_prior, dtype=np.float64)
    if prior.size == 0:
        raise ValueError("empty y_prior")
    if not np.all((prior == 0.0) | (prior == 1.0)):
        raise ValueError("y_prior must be binary 0/1")
    if size < 1:
        raise ValueError("size must be >= 1")
    return np.full(size, float(prior.mean()), dtype=np.float64)
