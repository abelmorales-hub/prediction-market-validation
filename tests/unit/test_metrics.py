"""Metric properties and the edge cases where a wrong answer would be plausible."""

import numpy as np
import pytest

from src.scoring.metrics import (
    auc,
    brier,
    bss,
    ece,
    log_loss,
    paired_diff,
    reliability_curve,
)

RNG = np.random.default_rng(99)


# -- properties -----------------------------------------------------------


def test_brier_is_bounded_in_unit_interval() -> None:
    p = RNG.uniform(0.0, 1.0, size=10_000)
    y = RNG.integers(0, 2, size=10_000).astype(float)
    scores = brier(p, y)
    assert scores.min() >= 0.0 and scores.max() <= 1.0


def test_brier_is_zero_only_for_a_perfect_prediction() -> None:
    y = np.array([1.0, 0.0])
    assert np.all(brier(y, y) == 0.0)
    assert np.all(brier(np.array([0.9, 0.1]), y) > 0.0)


def test_bss_of_an_identical_predictor_is_zero() -> None:
    p = RNG.uniform(0.05, 0.95, size=200)
    y = RNG.integers(0, 2, size=200).astype(float)
    assert abs(bss(p, p, y)) < 1e-12


def test_bss_is_positive_when_the_model_is_better_and_negative_when_worse() -> None:
    y = np.array([1.0, 1.0, 0.0, 0.0])
    market = np.array([0.5, 0.5, 0.5, 0.5])
    better = np.array([0.8, 0.8, 0.2, 0.2])
    worse = np.array([0.2, 0.2, 0.8, 0.8])
    assert bss(better, market, y) > 0
    assert bss(worse, market, y) < 0


def test_paired_diff_is_antisymmetric_in_its_two_forecasts() -> None:
    a = RNG.uniform(0.05, 0.95, size=100)
    b = RNG.uniform(0.05, 0.95, size=100)
    y = RNG.integers(0, 2, size=100).astype(float)
    np.testing.assert_allclose(paired_diff(a, b, y), -paired_diff(b, a, y), atol=1e-15)


def test_log_loss_is_finite_at_the_asymptotes() -> None:
    """Clipped, so a p of exactly 0 or 1 cannot produce inf and poison a sum."""
    assert np.all(np.isfinite(log_loss(np.array([0.0, 1.0]), np.array([1.0, 0.0]))))


def test_log_loss_rewards_confidence_only_when_correct() -> None:
    y = np.array([1.0])
    assert log_loss(np.array([0.9]), y) < log_loss(np.array([0.6]), y)
    assert log_loss(np.array([0.1]), y) > log_loss(np.array([0.4]), y)


# -- undefined rather than misleading -------------------------------------


def test_bss_is_nan_when_the_market_was_perfect() -> None:
    """Skill over a flawless forecaster is unanswerable. Returning 0 would read as
    'no edge', which is a different claim."""
    y = np.array([1.0, 0.0])
    assert np.isnan(bss(np.array([0.9, 0.1]), y, y))


def test_auc_is_nan_with_a_single_class() -> None:
    """Discrimination is undefined, not 0.5, when nothing happened."""
    assert np.isnan(auc(np.array([0.2, 0.8]), np.array([0.0, 0.0])))
    assert np.isnan(auc(np.array([0.2, 0.8]), np.array([1.0, 1.0])))


def test_auc_handles_ties_with_average_ranks() -> None:
    """A market price quantised to the tick produces many ties; naive ranking biases."""
    p = np.array([0.5, 0.5, 0.5, 0.5])
    assert auc(p, np.array([0.0, 1.0, 0.0, 1.0])) == 0.5


def test_reliability_reports_empty_bins_as_nan_not_zero() -> None:
    """An unpopulated bin has no observed rate; plotting it at 0 invents a miscalibration."""
    p = np.array([0.05, 0.05, 0.95])
    y = np.array([0.0, 1.0, 1.0])
    curve = reliability_curve(p, y, n_bins=10)
    assert curve.counts.sum() == 3
    assert np.isnan(curve.observed_rate[5]), "nothing predicted near 0.55"
    assert not np.isnan(curve.observed_rate[0])


def test_reliability_puts_p_of_exactly_one_in_the_last_bin() -> None:
    curve = reliability_curve(np.array([1.0]), np.array([1.0]), n_bins=10)
    assert curve.counts[-1] == 1
    assert curve.counts[:-1].sum() == 0


def test_a_perfectly_calibrated_forecaster_has_near_zero_ece() -> None:
    p = RNG.uniform(0.02, 0.98, size=200_000)
    y = (RNG.uniform(size=200_000) < p).astype(float)
    assert ece(p, y, n_bins=10) < 0.01


def test_a_systematically_overconfident_forecaster_has_large_ece() -> None:
    y = np.concatenate([np.ones(500), np.zeros(500)])
    p = np.concatenate([np.full(500, 0.99), np.full(500, 0.9)])
    assert ece(p, y, n_bins=10) > 0.4


# -- validation at the trust boundary -------------------------------------


@pytest.mark.parametrize(
    "p,y,message",
    [
        (np.array([0.5, 0.5]), np.array([1.0]), "shape mismatch"),
        (np.array([]), np.array([]), "empty"),
        (np.array([1.5]), np.array([1.0]), r"\[0, 1\]"),
        (np.array([-0.1]), np.array([1.0]), r"\[0, 1\]"),
        (np.array([np.nan]), np.array([1.0]), "non-finite"),
        (np.array([0.5]), np.array([2.0]), "binary"),
        (np.array([0.5]), np.array([0.5]), "binary"),
    ],
)
def test_bad_input_raises_rather_than_returning_a_number(
    p: np.ndarray, y: np.ndarray, message: str
) -> None:
    """A scorer that returns a plausible number for malformed input is worse than one
    that crashes: the number gets believed."""
    with pytest.raises(ValueError, match=message):
        brier(p, y)
