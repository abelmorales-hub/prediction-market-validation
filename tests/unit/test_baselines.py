"""Baselines are closed form, so these tests are mostly about the traps they set."""

import numpy as np
import pytest

from src.baselines.simple import baseline_base_rate, baseline_market, baseline_shrink
from src.scoring.metrics import bss, paired_diff

RNG = np.random.default_rng(7)


def test_market_baseline_is_the_identity() -> None:
    mid = RNG.uniform(0.01, 0.99, size=100)
    np.testing.assert_array_equal(baseline_market(mid), mid)


def test_market_baseline_returns_a_copy_not_a_view() -> None:
    """A caller mutating its forecast must not silently rewrite the benchmark."""
    mid = np.full(3, 0.5)
    out = baseline_market(mid)
    out[0] = 0.9
    assert mid[0] == 0.5


def test_market_baseline_scores_exactly_zero() -> None:
    """The null result the whole pipeline is calibrated against."""
    mid = RNG.uniform(0.02, 0.98, size=500)
    y = (RNG.uniform(size=500) < mid).astype(float)
    assert np.all(paired_diff(baseline_market(mid), mid, y) == 0.0)
    assert abs(bss(baseline_market(mid), mid, y)) < 1e-12


def test_shrink_pulls_toward_the_centre() -> None:
    mid = np.array([0.02, 0.5, 0.98])
    out = baseline_shrink(mid, alpha=0.9, centre=0.5)
    np.testing.assert_allclose(out, [0.068, 0.5, 0.932])
    assert out[1] == 0.5, "a price already at the centre is unmoved"


@pytest.mark.parametrize("alpha,expected", [(1.0, [0.2, 0.8]), (0.0, [0.5, 0.5])])
def test_shrink_endpoints(alpha: float, expected: list[float]) -> None:
    np.testing.assert_allclose(baseline_shrink(np.array([0.2, 0.8]), alpha=alpha), expected)


def test_shrink_wins_in_the_tails_while_knowing_nothing() -> None:
    """The trap §4.2 describes, reproduced. Longshots resolve NO more often than their
    price implies, so shrinking beats the market with zero information."""
    n = 4000
    mid = RNG.uniform(0.96, 0.999, size=n)
    # The market is overconfident: the true rate sits below the quoted price.
    y = (RNG.uniform(size=n) < mid - 0.04).astype(float)
    d = paired_diff(baseline_shrink(mid, alpha=0.9), mid, y)
    assert d.mean() > 0, "shrink should beat an overconfident market in the tail"


def test_base_rate_is_constant_at_the_prior_rate() -> None:
    prior = np.array([1.0, 1.0, 0.0, 0.0, 1.0])
    out = baseline_base_rate(prior, size=3)
    np.testing.assert_allclose(out, [0.6, 0.6, 0.6])


@pytest.mark.parametrize(
    "fn,args,message",
    [
        (baseline_market, (np.array([]),), "empty"),
        (baseline_market, (np.array([1.5]),), r"\[0, 1\]"),
        (baseline_market, (np.array([np.nan]),), "non-finite"),
        (baseline_shrink, (np.array([0.5]),), "alpha"),
        (baseline_base_rate, (np.array([]), 3), "empty"),
        (baseline_base_rate, (np.array([0.5]), 3), "binary"),
        (baseline_base_rate, (np.array([1.0]), 0), "size"),
    ],
)
def test_bad_input_raises(fn: object, args: tuple, message: str) -> None:
    kw = {"alpha": 1.5} if fn is baseline_shrink else {}
    with pytest.raises(ValueError, match=message):
        fn(*args, **kw)  # type: ignore[operator]
