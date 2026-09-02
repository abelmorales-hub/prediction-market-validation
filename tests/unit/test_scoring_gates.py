"""The three gates M5 does not pass without.

These are not ordinary unit tests. A scoring bug does not raise — it returns a plausible
number, and a plausible number gets believed and acted on. G2 in particular is the
cheapest bug-catcher in the whole build.
"""

import numpy as np
import pytest

from src.scoring.bootstrap import clustered_bootstrap
from src.scoring.metrics import auc, brier, bss, ece, log_loss, paired_diff

RNG = np.random.default_rng(20260902)


def _market_like(n: int = 500) -> tuple[np.ndarray, np.ndarray]:
    """A calibrated market and outcomes drawn from it, so the market is honestly good."""
    mid = RNG.uniform(0.02, 0.98, size=n)
    y = (RNG.uniform(size=n) < mid).astype(float)
    return mid, y


# ---------------------------------------------------------------- G2


def test_null_model_bss_is_exactly_zero() -> None:
    """G2. Feed the market mid in as the model's own prediction.

    BSS must be 0 to within 1e-12. Any deviation means the scorer is misaligning
    prediction timestamps with price timestamps.
    """
    mid, y = _market_like(1000)
    assert abs(bss(mid, mid, y)) < 1e-12


@pytest.mark.parametrize("n", [1, 2, 7, 100, 5000])
def test_null_model_bss_is_zero_at_any_size(n: int) -> None:
    mid = RNG.uniform(0.01, 0.99, size=n)
    y = (RNG.uniform(size=n) < mid).astype(float)
    assert abs(bss(mid, mid, y)) < 1e-12


def test_null_model_paired_difference_is_identically_zero() -> None:
    """Not just zero on average — zero on every single row."""
    mid, y = _market_like(500)
    d = paired_diff(mid, mid, y)
    assert np.all(d == 0.0)


def test_null_model_bootstrap_interval_contains_zero() -> None:
    mid, y = _market_like(400)
    d = paired_diff(mid, mid, y)
    clusters = RNG.integers(0, 40, size=400)
    res = clustered_bootstrap(d, clusters, n_iter=2000, seed=1)
    assert res.ci_low <= 0.0 <= res.ci_high
    assert not res.excludes_zero


def test_a_misaligned_price_is_caught_by_the_null_gate() -> None:
    """The failure G2 is designed to detect: the price compared against is taken one
    row out of step with the prediction. The scorer still returns a number."""
    mid, y = _market_like(1000)
    shifted = np.roll(mid, 1)
    assert abs(bss(shifted, mid, y)) > 1e-6, "a misalignment must not score as zero skill"


# ---------------------------------------------------------------- G2b


def test_permutation_shows_no_skill() -> None:
    """G2b. Shuffle y randomly, re-score, repeat 1000x.

    The distribution of BSS must be centred on zero and the observed value must not sit
    in the tail.
    """
    n = 600
    mid, y = _market_like(n)
    # A model with no information beyond the price: the price plus symmetric noise.
    noise = RNG.normal(0.0, 0.03, size=n)
    model = np.clip(mid + noise, 0.01, 0.99)

    rng = np.random.default_rng(7)
    null_scores = np.array([bss(model, mid, rng.permutation(y)) for _ in range(1000)])

    assert abs(float(null_scores.mean())) < 0.02, "permuted BSS must centre on zero"

    observed = bss(model, mid, y)
    percentile = float((null_scores < observed).mean())
    assert 0.01 < percentile < 0.99, (
        f"observed BSS sits at the {percentile:.1%} of the null distribution; "
        "a no-information model must not look skilful"
    )


def test_permutation_destroys_a_genuinely_skilful_model() -> None:
    """The permutation test must have power: a model that really does know something
    should score well on real labels and badly on shuffled ones. Otherwise the test
    above passes vacuously."""
    n = 600
    mid = RNG.uniform(0.1, 0.9, size=n)
    y = (RNG.uniform(size=n) < mid).astype(float)
    cheat = np.clip(0.5 * mid + 0.5 * y, 0.02, 0.98)  # leaks the outcome

    observed = bss(cheat, mid, y)
    rng = np.random.default_rng(11)
    null_scores = np.array([bss(cheat, mid, rng.permutation(y)) for _ in range(300)])
    assert observed > np.quantile(null_scores, 0.99), "leakage must be visible"


# ---------------------------------------------------------------- analytic


def test_known_analytic_cases() -> None:
    """Hand-computed Brier and BSS on a 5-row table, asserted exactly."""
    p_model = np.array([0.9, 0.8, 0.5, 0.2, 0.1])
    p_market = np.array([0.7, 0.6, 0.5, 0.4, 0.3])
    y = np.array([1.0, 1.0, 0.0, 0.0, 0.0])

    # BS_model  = (0.9-1)^2, (0.8-1)^2, (0.5-0)^2, (0.2-0)^2, (0.1-0)^2
    #           = 0.01, 0.04, 0.25, 0.04, 0.01          sum = 0.35
    expected_model = np.array([0.01, 0.04, 0.25, 0.04, 0.01])
    np.testing.assert_allclose(brier(p_model, y), expected_model, rtol=0, atol=1e-15)
    assert abs(brier(p_model, y).sum() - 0.35) < 1e-15

    # BS_market = (0.7-1)^2, (0.6-1)^2, (0.5-0)^2, (0.4-0)^2, (0.3-0)^2
    #           = 0.09, 0.16, 0.25, 0.16, 0.09          sum = 0.75
    expected_market = np.array([0.09, 0.16, 0.25, 0.16, 0.09])
    np.testing.assert_allclose(brier(p_market, y), expected_market, rtol=0, atol=1e-15)
    assert abs(brier(p_market, y).sum() - 0.75) < 1e-15

    # d_i = BS_market - BS_model = 0.08, 0.12, 0.0, 0.12, 0.08   mean = 0.08
    np.testing.assert_allclose(
        paired_diff(p_model, p_market, y),
        np.array([0.08, 0.12, 0.0, 0.12, 0.08]),
        rtol=0,
        atol=1e-15,
    )
    assert abs(float(paired_diff(p_model, p_market, y).mean()) - 0.08) < 1e-15

    # BSS = 1 - 0.35/0.75 = 1 - 7/15 = 8/15
    assert abs(bss(p_model, p_market, y) - 8.0 / 15.0) < 1e-15


def test_known_analytic_log_loss() -> None:
    """Two rows, both certain-ish, hand-checked against -ln."""
    p = np.array([0.5, 0.25])
    y = np.array([1.0, 0.0])
    expected = np.array([-np.log(0.5), -np.log(0.75)])
    np.testing.assert_allclose(log_loss(p, y), expected, rtol=0, atol=1e-15)


def test_known_analytic_auc() -> None:
    """Perfect separation is 1.0; reversed is 0.0; a constant predictor is 0.5."""
    y = np.array([0.0, 0.0, 1.0, 1.0])
    assert auc(np.array([0.1, 0.2, 0.8, 0.9]), y) == 1.0
    assert auc(np.array([0.9, 0.8, 0.2, 0.1]), y) == 0.0
    assert auc(np.array([0.5, 0.5, 0.5, 0.5]), y) == 0.5


def test_known_analytic_ece() -> None:
    """Two populated bins, hand-weighted.

    Bin [0.0,0.1): four rows at p=0.05, one positive -> observed 0.25, gap 0.20
    Bin [0.9,1.0]: two rows at p=0.95, both positive -> observed 1.00, gap 0.05
    ECE = (4*0.20 + 2*0.05) / 6 = 0.9/6 = 0.15
    """
    p = np.array([0.05, 0.05, 0.05, 0.05, 0.95, 0.95])
    y = np.array([1.0, 0.0, 0.0, 0.0, 1.0, 1.0])
    assert abs(ece(p, y, n_bins=10) - 0.15) < 1e-12
