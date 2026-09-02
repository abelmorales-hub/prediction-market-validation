"""Pivoting stored predictions into scorer input.

The failure this guards against is silent: pair two models on different rows and the
difference between them gets reported as skill.
"""

import numpy as np
import pytest

from src.analysis.frame import build_frame


def _row(market="M1", horizon="T-24h", model="baseline_market", p=0.6, mid=0.6,
         outcome="yes", family="F1", category="Crypto"):
    return {
        "market_id": market, "horizon_label": horizon, "model_version": model,
        "p_hat": p, "market_mid": mid, "outcome": outcome, "was_disputed": False,
        "event_family": family, "category": category,
    }


def test_models_become_aligned_columns() -> None:
    rows = [
        _row(model="baseline_market", p=0.6),
        _row(model="baseline_shrink", p=0.59),
    ]
    f = build_frame(rows)
    assert len(f) == 1
    assert sorted(f.forecasts) == ["baseline_market", "baseline_shrink"]
    assert f.forecasts["baseline_market"][0] == 0.6
    assert f.forecasts["baseline_shrink"][0] == 0.59
    assert f.market_mid[0] == 0.6


def test_a_row_missing_one_model_is_dropped_entirely() -> None:
    """Scoring model A on rows model B never saw compares them on different events."""
    rows = [
        _row(market="M1", model="baseline_market"),
        _row(market="M1", model="baseline_shrink"),
        _row(market="M2", model="baseline_market"),  # no shrink counterpart
    ]
    f = build_frame(rows)
    assert len(f) == 1
    assert f.market_ids.tolist() == ["M1"]


def test_every_column_has_the_same_length() -> None:
    rows = []
    for m in ("M1", "M2"):
        for mv in ("baseline_market", "baseline_shrink"):
            rows.append(_row(market=m, model=mv, family=f"F{m}"))
    f = build_frame(rows)
    n = len(f)
    assert all(v.size == n for v in f.forecasts.values())
    assert f.market_mid.size == f.y.size == f.clusters.size == n
    assert f.categories.size == f.horizons.size == n


def test_horizons_of_one_market_are_one_cluster() -> None:
    """Three horizons of the same market share an outcome and nearly all information.
    Counting them as independent would inflate n threefold for free."""
    rows = []
    for h in ("T-24h", "T-6h", "T-1h"):
        for mv in ("baseline_market", "baseline_shrink"):
            rows.append(_row(horizon=h, model=mv, family="F1"))
    f = build_frame(rows)
    assert len(f) == 3
    assert f.n_clusters == 1


@pytest.mark.parametrize(
    "outcome,expected", [("yes", 1.0), ("no", 0.0), ("YES", 1.0), (True, 1.0), (False, 0.0)]
)
def test_outcome_encodings(outcome: object, expected: float) -> None:
    rows = [_row(model=m, outcome=outcome) for m in ("baseline_market", "baseline_shrink")]
    assert build_frame(rows).y[0] == expected


def test_an_unrecognised_outcome_raises_rather_than_guessing() -> None:
    rows = [_row(model=m, outcome="maybe") for m in ("baseline_market", "baseline_shrink")]
    with pytest.raises(ValueError, match="unrecognised outcome"):
        build_frame(rows)


def test_unresolved_rows_are_excluded() -> None:
    rows = [_row(model=m, outcome=None) for m in ("baseline_market", "baseline_shrink")]
    assert len(build_frame(rows)) == 0


def test_disputed_rows_are_excluded_by_default() -> None:
    rows = [_row(model=m) for m in ("baseline_market", "baseline_shrink")]
    for r in rows:
        r["was_disputed"] = True
    assert len(build_frame(rows)) == 0
    assert len(build_frame(rows, drop_disputed=False)) == 1


def test_empty_input_gives_an_empty_frame_not_a_crash() -> None:
    f = build_frame([])
    assert len(f) == 0 and f.n_clusters == 0


def test_restricting_to_a_horizon_keeps_columns_aligned() -> None:
    rows = []
    for h in ("T-24h", "T-1h"):
        for mv in ("baseline_market", "baseline_shrink"):
            rows.append(_row(horizon=h, model=mv))
    sub = build_frame(rows).restricted_to("T-1h")
    assert len(sub) == 1
    assert sub.horizons.tolist() == ["T-1h"]
    assert all(v.size == 1 for v in sub.forecasts.values())


def test_the_market_baseline_reproduces_the_null_through_the_frame() -> None:
    """End to end: whatever the pivot does, baseline_market must still equal market_mid."""
    rng = np.random.default_rng(3)
    rows = []
    for i in range(40):
        mid = float(rng.uniform(0.05, 0.95))
        for mv, p in (("baseline_market", mid), ("baseline_shrink", 0.9 * mid + 0.05)):
            rows.append(_row(market=f"M{i}", model=mv, p=p, mid=mid, family=f"F{i % 5}",
                             outcome="yes" if rng.uniform() < mid else "no"))
    f = build_frame(rows)
    np.testing.assert_array_equal(f.forecasts["baseline_market"], f.market_mid)
