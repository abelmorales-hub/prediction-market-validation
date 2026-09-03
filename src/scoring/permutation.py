"""G2b: does a forecast know something about the outcome it should not?

The criterion is **one-sided**. Shuffling `y` and asking whether the null centres on
zero is wrong: shuffled labels are unpredictable, so a forecast closer to 0.5 beats a
sharp one against them, and the null centres above zero for any model flatter than the
market. Only the upper tail means leakage.

The test says nothing about `p̂ = m`, whose BSS is identically zero under every
permutation. That degenerate case is reported as such rather than given a p-value that
would read as evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.scoring.metrics import FloatArray, bss

DEGENERATE_SD = 1e-12


@dataclass(frozen=True)
class PermutationResult:
    name: str
    observed: float
    null_mean: float
    null_sd: float
    p_value: float
    """P(null >= observed). Small means the observed score sits in the upper tail."""
    sharpness: float
    market_sharpness: float
    degenerate: bool
    """True when the forecast is the market itself: no variance, nothing to test."""

    @property
    def leaks(self) -> bool:
        return not self.degenerate and self.p_value < 0.01

    @property
    def verdict(self) -> str:
        if self.degenerate:
            return "VACUOUS"
        return "LEAK" if self.leaks else "no leakage"


def permutation_test(
    name: str,
    p_model: FloatArray,
    p_market: FloatArray,
    y: FloatArray,
    n_iter: int = 2000,
    seed: int = 7,
) -> PermutationResult:
    observed = bss(p_model, p_market, y)
    rng = np.random.default_rng(seed)
    null = np.array([bss(p_model, p_market, rng.permutation(y)) for _ in range(n_iter)])

    null_sd = float(null.std())
    degenerate = null_sd < DEGENERATE_SD and abs(observed) < DEGENERATE_SD
    return PermutationResult(
        name=name,
        observed=float(observed),
        null_mean=float(null.mean()),
        null_sd=null_sd,
        # >= not >: with a discrete null, ties belong to the null, which is the
        # conservative direction for a leakage test.
        p_value=float((null >= observed).mean()),
        sharpness=float(np.abs(p_model - 0.5).mean()),
        market_sharpness=float(np.abs(p_market - 0.5).mean()),
        degenerate=degenerate,
    )
