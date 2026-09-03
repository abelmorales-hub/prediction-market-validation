"""G2b, the one-sided reading.

The failure this guards against is a gate that rejects an honest flat forecast, or
passes a leaking one, because it asserted the permuted null centres on zero.
"""

import numpy as np

from src.scoring.permutation import permutation_test

RNG = np.random.default_rng(5)


def _market_like(n: int) -> tuple[np.ndarray, np.ndarray]:
    mid = RNG.uniform(0.05, 0.95, size=n)
    y = (RNG.uniform(size=n) < mid).astype(float)
    return mid, y


def test_a_leaking_forecast_lands_in_the_upper_tail() -> None:
    mid, y = _market_like(400)
    cheat = np.clip(0.5 * mid + 0.5 * y, 0.02, 0.98)
    r = permutation_test("cheat", cheat, mid, y, n_iter=300)
    assert r.leaks
    assert r.verdict == "LEAK"


def test_an_honest_flat_forecast_passes_despite_a_positive_null() -> None:
    """The whole reason the criterion is one-sided: shrinking toward 0.5 beats sharp
    forecasts on shuffled labels, so the null centres above zero with no leakage."""
    mid, y = _market_like(400)
    shrink = 0.9 * mid + 0.05
    r = permutation_test("shrink", shrink, mid, y, n_iter=300)
    assert not r.leaks
    assert r.null_mean > 0.0, "a flatter forecast should score positively on random labels"
    assert r.sharpness < r.market_sharpness


def test_the_market_itself_is_reported_as_vacuous_not_as_a_pass() -> None:
    """p_hat = m has zero variance under permutation. A p-value here would read as
    evidence about a model that was never tested."""
    mid, y = _market_like(200)
    r = permutation_test("baseline_market", mid, mid, y, n_iter=100)
    assert r.degenerate
    assert r.verdict == "VACUOUS"
    assert not r.leaks
