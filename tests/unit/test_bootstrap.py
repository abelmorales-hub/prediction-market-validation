"""Clustered bootstrap. The property that matters is that clustering *widens* the
interval — resampling rows instead of clusters is what turns a null into a discovery."""

import numpy as np
import pytest

from src.scoring.bootstrap import clustered_bootstrap, required_n

RNG = np.random.default_rng(4242)


def _clustered_data(n_clusters: int, per_cluster: int, effect: float, icc: float):
    """Cluster-level shocks plus row noise: the correlation structure P5 describes."""
    shocks = RNG.normal(0.0, icc, size=n_clusters)
    d, clusters = [], []
    for c in range(n_clusters):
        d.append(effect + shocks[c] + RNG.normal(0.0, 0.02, size=per_cluster))
        clusters.append(np.full(per_cluster, c))
    return np.concatenate(d), np.concatenate(clusters)


def test_resamples_clusters_not_rows() -> None:
    """With strong cluster shocks, a row bootstrap would give a far tighter interval.
    Underestimating the interval by 3-5x is exactly how noise gets published."""
    d, clusters = _clustered_data(30, 40, effect=0.0, icc=0.05)
    clustered = clustered_bootstrap(d, clusters, n_iter=2000, seed=1)

    rows = np.arange(d.size)  # every row its own cluster == an i.i.d. bootstrap
    iid = clustered_bootstrap(d, rows, n_iter=2000, seed=1)

    clustered_width = clustered.ci_high - clustered.ci_low
    iid_width = iid.ci_high - iid.ci_low
    assert clustered_width > 3 * iid_width, (
        f"clustered {clustered_width:.4f} vs iid {iid_width:.4f}"
    )


def test_effective_n_falls_well_below_nominal_under_clustering() -> None:
    """Design doc §5 expects effective n at 1/3 to 1/5 of nominal."""
    d, clusters = _clustered_data(30, 40, effect=0.0, icc=0.05)
    res = clustered_bootstrap(d, clusters, n_iter=2000, seed=2)
    assert res.n == 1200
    assert res.n_clusters == 30
    assert res.design_effect > 1.0
    assert res.effective_n < res.n / 3


def test_effective_n_equals_n_when_every_row_is_its_own_cluster() -> None:
    d = RNG.normal(0.0, 0.05, size=500)
    res = clustered_bootstrap(d, np.arange(500), n_iter=2000, seed=3)
    assert res.design_effect == pytest.approx(1.0, abs=0.35)
    assert res.effective_n <= res.n, "clustering cannot manufacture information"


def test_mean_is_the_plain_sample_mean_not_a_bootstrap_estimate() -> None:
    d, clusters = _clustered_data(10, 10, effect=0.03, icc=0.01)
    res = clustered_bootstrap(d, clusters, n_iter=500, seed=4)
    assert res.mean == pytest.approx(float(d.mean()), abs=1e-12)


def test_a_real_effect_produces_an_interval_excluding_zero() -> None:
    d, clusters = _clustered_data(60, 20, effect=0.05, icc=0.005)
    res = clustered_bootstrap(d, clusters, n_iter=2000, seed=5)
    assert res.excludes_zero
    assert res.ci_low > 0


def test_no_effect_produces_an_interval_containing_zero() -> None:
    d, clusters = _clustered_data(60, 20, effect=0.0, icc=0.005)
    res = clustered_bootstrap(d, clusters, n_iter=2000, seed=6)
    assert not res.excludes_zero


def test_is_deterministic_for_a_given_seed() -> None:
    d, clusters = _clustered_data(20, 10, effect=0.01, icc=0.01)
    a = clustered_bootstrap(d, clusters, n_iter=500, seed=9)
    b = clustered_bootstrap(d, clusters, n_iter=500, seed=9)
    assert (a.ci_low, a.ci_high) == (b.ci_low, b.ci_high)


def test_unequal_cluster_sizes_are_weighted_by_membership() -> None:
    """One huge cluster and many small ones: the mean must stay a row-level mean."""
    d = np.concatenate([np.full(100, 1.0), np.full(4, 0.0)])
    clusters = np.concatenate([np.full(100, "big"), np.array(["a", "b", "c", "d"])])
    res = clustered_bootstrap(d, clusters, n_iter=500, seed=10)
    assert res.mean == pytest.approx(100 / 104)
    assert res.n == 104
    assert res.n_clusters == 5


def test_alpha_widens_the_interval() -> None:
    d, clusters = _clustered_data(40, 10, effect=0.0, icc=0.02)
    narrow = clustered_bootstrap(d, clusters, n_iter=2000, alpha=0.10, seed=12)
    wide = clustered_bootstrap(d, clusters, n_iter=2000, alpha=0.01, seed=12)
    assert (wide.ci_high - wide.ci_low) > (narrow.ci_high - narrow.ci_low)


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"n_iter": 0}, "n_iter"),
        ({"alpha": 0.0}, "alpha"),
        ({"alpha": 1.0}, "alpha"),
    ],
)
def test_rejects_nonsense_parameters(kwargs: dict, message: str) -> None:
    d, clusters = _clustered_data(5, 4, effect=0.0, icc=0.01)
    with pytest.raises(ValueError, match=message):
        clustered_bootstrap(d, clusters, **kwargs)


def test_rejects_misaligned_inputs() -> None:
    with pytest.raises(ValueError, match="shape mismatch"):
        clustered_bootstrap(np.zeros(5), np.zeros(4))


def test_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        clustered_bootstrap(np.array([0.1, np.nan]), np.array([0, 1]))


# -- power ---------------------------------------------------------------


def test_required_n_reproduces_the_design_doc_table() -> None:
    """§5, sigma_d = 0.06, alpha 0.05, 80% power."""
    assert required_n(0.020) == pytest.approx(71, abs=1)
    assert required_n(0.010) == pytest.approx(283, abs=1)
    assert required_n(0.005) == pytest.approx(1130, abs=2)
    assert required_n(0.002) == pytest.approx(7064, abs=5)


def test_required_n_grows_as_the_edge_shrinks() -> None:
    assert required_n(0.001) > required_n(0.01) > required_n(0.1)
