"""Arithmetic baselines. Every one is closed form — no fitting, no state, no inference.

These exist to give the scorer something to fail against. A pipeline that cannot show
`baseline_market` scoring exactly zero is a pipeline whose positive results mean nothing,
so the trivial baselines are more load-bearing than the interesting ones.

Nothing here may import from `src.scoring`. The dependency runs the other way: the report
takes forecasts as data. That keeps the scorer unable to see what produced a number.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

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


def baseline_base_rate(
    category_rates: Mapping[str, float],
    categories: Sequence[str],
    default: float | None = None,
) -> FloatArray:
    """Each row forecast at its category's historical YES rate.

    **`category_rates` must be computed from resolutions strictly prior to the rows being
    scored.** A base rate is a forecast and P2 binds it like any other: derive it from the
    same outcomes it is scored against and it beats the market for free. The split belongs
    to the caller — this function receives rates already computed and cannot verify their
    provenance, which is why it is stated here rather than assumed.

    A category with no rate raises unless `default` is given. Silently substituting 0.5
    for an unseen category would score it as a real forecast rather than an absence.
    """
    if not categories:
        raise ValueError("empty categories")
    for name, rate in category_rates.items():
        if not 0.0 <= rate <= 1.0:
            raise ValueError(f"rate for {name!r} outside [0, 1]")
    if default is not None and not 0.0 <= default <= 1.0:
        raise ValueError("default outside [0, 1]")

    missing = {c for c in categories if c not in category_rates}
    if missing and default is None:
        raise ValueError(f"no rate for categories: {sorted(missing)}")
    return np.asarray(
        [category_rates.get(c, default) for c in categories], dtype=np.float64
    )
