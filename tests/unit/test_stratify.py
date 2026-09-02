"""Price-bucket and category stratification.

§4.2: almost all fake edge lives in the tails. These tests exist to make sure the tails
are actually separable, because an edge that only survives when they are pooled in is
longshot bias wearing a model's clothes.
"""

import numpy as np
import pytest

from src.scoring.stratify import (
    PRICE_BUCKETS,
    assign_price_bucket,
    bucket_labels,
    by_category,
    by_price_bucket,
    excluding_tails,
)

RNG = np.random.default_rng(31337)


def test_bucket_labels_cover_the_documented_edges() -> None:
    assert bucket_labels() == [
        "[0.00, 0.05]",
        "(0.05, 0.20]",
        "(0.20, 0.80]",
        "(0.80, 0.95]",
        "(0.95, 1.00]",
    ]
    assert PRICE_BUCKETS == (0.0, 0.05, 0.20, 0.80, 0.95, 1.0)


@pytest.mark.parametrize(
    "mid,expected",
    [
        (0.00, 0),
        (0.05, 0),  # first bucket is closed on both ends
        (0.0500001, 1),
        (0.20, 1),
        (0.2000001, 2),
        (0.50, 2),
        (0.80, 2),
        (0.8000001, 3),
        (0.95, 3),
        (0.9500001, 4),
        (1.00, 4),
    ],
)
def test_bucket_boundaries(mid: float, expected: int) -> None:
    assert int(assign_price_bucket(np.array([mid]))[0]) == expected


def test_rejects_prices_outside_the_unit_interval() -> None:
    with pytest.raises(ValueError, match="market_mid"):
        assign_price_bucket(np.array([1.5]))


def test_tail_only_edge_is_visible_as_such() -> None:
    """A model that only wins in the tails must show a positive middle-free result and
    nothing once the tails are removed."""
    n = 1200
    mid = RNG.uniform(0.0, 1.0, size=n)
    clusters = RNG.integers(0, 120, size=n)
    in_tail = (mid <= 0.05) | (mid > 0.95)
    d = np.where(in_tail, 0.05, 0.0) + RNG.normal(0.0, 0.002, size=n)

    strata = {s.name: s for s in by_price_bucket(d, mid, clusters, n_iter=800)}
    assert strata["[0.00, 0.05]"].result.mean > 0.04
    assert strata["(0.95, 1.00]"].result.mean > 0.04
    assert abs(strata["(0.20, 0.80]"].result.mean) < 0.01

    trimmed = excluding_tails(d, mid, clusters, n_iter=800)
    assert not trimmed.excludes_zero, "removing the tails must remove the apparent edge"


def test_a_broad_edge_survives_removing_the_tails() -> None:
    n = 1200
    mid = RNG.uniform(0.0, 1.0, size=n)
    clusters = RNG.integers(0, 120, size=n)
    d = np.full(n, 0.03) + RNG.normal(0.0, 0.002, size=n)
    trimmed = excluding_tails(d, mid, clusters, n_iter=800)
    assert trimmed.excludes_zero and trimmed.ci_low > 0


def test_thin_strata_are_omitted_not_reported_with_a_useless_interval() -> None:
    n = 300
    mid = np.concatenate([RNG.uniform(0.2, 0.8, size=n - 3), np.array([0.01, 0.02, 0.03])])
    d = RNG.normal(0.0, 0.01, size=n)
    clusters = RNG.integers(0, 30, size=n)
    names = {s.name for s in by_price_bucket(d, mid, clusters, min_n=20, n_iter=400)}
    assert "(0.20, 0.80]" in names
    assert "[0.00, 0.05]" not in names, "3 rows cannot support a bootstrap"


def test_each_stratum_is_bootstrapped_on_its_own_clusters() -> None:
    n = 400
    mid = RNG.uniform(0.2, 0.8, size=n)
    d = RNG.normal(0.02, 0.01, size=n)
    clusters = RNG.integers(0, 8, size=n)
    (stratum,) = by_price_bucket(d, mid, clusters, n_iter=500)
    assert stratum.result.n == n
    assert stratum.result.n_clusters == 8, "clusters counted within the stratum"


def test_categories_are_reported_separately() -> None:
    cats = np.array(["Sports"] * 100 + ["Crypto"] * 100)
    d = np.concatenate([np.full(100, 0.05), np.full(100, -0.05)])
    d = d + RNG.normal(0.0, 0.001, size=200)
    clusters = RNG.integers(0, 20, size=200)
    out = {s.name: s.result.mean for s in by_category(d, cats, clusters, n_iter=500)}
    assert out["Sports"] > 0.04
    assert out["Crypto"] < -0.04


def test_misaligned_inputs_are_rejected() -> None:
    with pytest.raises(ValueError, match="align"):
        by_price_bucket(np.zeros(5), np.zeros(4), np.zeros(5))
    with pytest.raises(ValueError, match="align"):
        by_category(np.zeros(5), np.array(["a"] * 4), np.zeros(5))


def test_excluding_tails_refuses_an_all_tail_sample() -> None:
    mid = np.array([0.01, 0.99, 0.02])
    with pytest.raises(ValueError, match="tail"):
        excluding_tails(np.zeros(3), mid, np.arange(3))
