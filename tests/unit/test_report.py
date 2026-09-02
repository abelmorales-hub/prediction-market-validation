"""The report's job is to make a weak result look weak. These tests check that it does,
not merely that it renders."""

import numpy as np
import pytest

from src.baselines.simple import baseline_market, baseline_shrink
from src.scoring.report import build_report, score

RNG = np.random.default_rng(2026)


def _sample(n: int = 600, n_clusters: int = 60):
    mid = RNG.uniform(0.02, 0.98, size=n)
    y = (RNG.uniform(size=n) < mid).astype(float)
    clusters = RNG.integers(0, n_clusters, size=n)
    return mid, y, clusters


# -- the null result ------------------------------------------------------


def test_market_against_itself_is_flat_through_the_whole_report_path() -> None:
    """Same guarantee as G2, but through `score` rather than the raw metrics — the
    wiring is where a pairing bug would hide."""
    mid, y, clusters = _sample()
    row = score("market", baseline_market(mid), mid, y, clusters, n_iter=500)
    assert row.mean_d == 0.0
    assert row.ci_low == 0.0 and row.ci_high == 0.0
    assert abs(row.brier_skill) < 1e-12


def test_a_zero_effect_reports_no_required_n_rather_than_a_huge_one() -> None:
    """n needed to detect an effect of zero is undefined, not enormous."""
    mid, y, clusters = _sample()
    row = score("market", baseline_market(mid), mid, y, clusters, n_iter=500)
    assert row.required_effective_n is None
    assert row.verdict == "reference"
    assert not row.underpowered


# -- power labelling ------------------------------------------------------


def test_a_tiny_effect_on_a_small_sample_is_labelled_underpowered() -> None:
    n = 150
    mid = RNG.uniform(0.3, 0.7, size=n)
    y = (RNG.uniform(size=n) < mid).astype(float)
    clusters = RNG.integers(0, 15, size=n)
    p = np.clip(mid + RNG.normal(0.0, 0.05, size=n), 0.01, 0.99)
    row = score("noisy", p, mid, y, clusters, n_iter=500)
    assert row.required_effective_n is not None
    assert row.effective_n < row.required_effective_n
    assert row.underpowered and row.verdict == "UNDERPOWERED"


def test_underpowered_shows_in_the_rendered_table() -> None:
    """The label has to reach the page. A verdict computed and not printed is useless."""
    n = 150
    mid = RNG.uniform(0.3, 0.7, size=n)
    y = (RNG.uniform(size=n) < mid).astype(float)
    clusters = RNG.integers(0, 15, size=n)
    p = np.clip(mid + RNG.normal(0.0, 0.05, size=n), 0.01, 0.99)
    md = build_report({"noisy": p}, mid, y, clusters, n_iter=300)
    assert "UNDERPOWERED" in md


def test_effective_n_is_reported_below_nominal_under_clustering() -> None:
    n, k = 800, 20
    mid = RNG.uniform(0.2, 0.8, size=n)
    y = (RNG.uniform(size=n) < mid).astype(float)
    clusters = np.repeat(np.arange(k), n // k)
    shock = np.repeat(RNG.normal(0.0, 0.08, size=k), n // k)
    p = np.clip(mid + shock, 0.01, 0.99)
    row = score("clustered", p, mid, y, clusters, n_iter=1000)
    assert row.n == n
    assert row.effective_n < row.n, "clustering must cost effective sample size"


# -- structure ------------------------------------------------------------


def test_report_contains_every_required_column() -> None:
    mid, y, clusters = _sample()
    md = build_report({"market": baseline_market(mid)}, mid, y, clusters, n_iter=300)
    for col in ("mean(d)", "95% CI", "clusters", "eff. n", "BSS", "ECE", "AUC", "need eff. n"):
        assert col in md, col


def test_report_has_a_row_per_forecast_in_every_section() -> None:
    mid, y, clusters = _sample()
    forecasts = {"market": baseline_market(mid), "shrink": baseline_shrink(mid)}
    md = build_report(forecasts, mid, y, clusters, n_iter=300)
    assert md.count("| market |") == md.count("| shrink |") > 1


def test_sections_are_present() -> None:
    mid, y, clusters = _sample()
    cats = np.array(["Sports"] * 300 + ["Crypto"] * 300)
    md = build_report(
        {"market": baseline_market(mid)}, mid, y, clusters, categories=cats, n_iter=300
    )
    assert "## Overall" in md
    assert "## Excluding tail price buckets (G3)" in md
    assert "## By price bucket" in md
    assert "## By category" in md
    assert "### Sports" in md and "### Crypto" in md


def test_thin_strata_are_named_not_silently_dropped() -> None:
    """Which slices the data cannot speak to is itself a finding."""
    mid, y, clusters = _sample()
    cats = np.array(["Sports"] * 597 + ["Rare"] * 3)
    md = build_report(
        {"market": baseline_market(mid)}, mid, y, clusters, categories=cats, n_iter=300
    )
    assert "### Rare" in md
    assert "below the 20-row floor" in md


def test_tail_exclusion_section_drops_the_tail_rows() -> None:
    n = 900
    mid = np.concatenate([RNG.uniform(0.3, 0.7, size=600), RNG.uniform(0.96, 0.999, size=300)])
    y = (RNG.uniform(size=n) < mid).astype(float)
    clusters = RNG.integers(0, 90, size=n)
    md = build_report({"market": baseline_market(mid)}, mid, y, clusters, n_iter=300)
    section = md.split("## Excluding tail price buckets")[1].split("##")[0]
    assert "| 600 |" in section, "the 300 tail rows should be gone"


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({}, "no forecasts"),
        ({"forecasts": {"a": np.zeros(5)}, "y": np.zeros(4)}, "align"),
    ],
)
def test_rejects_malformed_input(kwargs: dict, message: str) -> None:
    base = {
        "forecasts": {},
        "p_market": np.full(5, 0.5),
        "y": np.zeros(5),
        "clusters": np.arange(5),
    }
    base.update(kwargs)
    with pytest.raises(ValueError, match=message):
        build_report(**base)


def test_rejects_misaligned_categories() -> None:
    mid, y, clusters = _sample(n=100, n_clusters=10)
    with pytest.raises(ValueError, match="align"):
        build_report(
            {"market": baseline_market(mid)},
            mid,
            y,
            clusters,
            categories=np.array(["a"] * 99),
            n_iter=200,
        )
