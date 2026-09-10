"""Pivoting stored predictions into scorer input.

The failure this guards against is silent: pair two models on different rows and the
difference between them gets reported as skill.
"""

import numpy as np
import pytest

from src.analysis.frame import FROZEN_MODELS, build_frame, resolve_models


def _row(market="M1", horizon="T-24h", model="baseline_market", p=0.6, mid=0.6,
         outcome="yes", family="F1", category="Crypto", ts=1):
    return {
        "market_id": market, "horizon_label": horizon, "model_version": model,
        "p_hat": p, "market_mid": mid, "outcome": outcome, "was_disputed": False,
        "event_family": family, "category": category, "ts": ts,
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


def test_frozen_models_matches_the_declared_model_version() -> None:
    """`FROZEN_MODELS` copies the name rather than importing it, to keep frame.py pure.

    That copy is only safe if something fails when the two drift, which is this.
    """
    from src.analysis.weather import MODEL_VERSION

    assert MODEL_VERSION in FROZEN_MODELS


def test_the_default_withholds_the_frozen_model() -> None:
    """The unguarded command must be the safe one: H2's score is a single pre-committed
    look, and it used to be spent by anyone who ran the scorer."""
    rows = [_row(model=m) for m in ("baseline_market", "baseline_shrink", "gefs_ensemble_raw")]
    assert resolve_models(rows, None) == {"baseline_market", "baseline_shrink"}
    assert "gefs_ensemble_raw" not in build_frame(rows, models=resolve_models(rows, None)).forecasts


def test_naming_the_frozen_model_includes_it() -> None:
    """Withholding it is a guard against accident, not a lock -- G3 has to read it."""
    rows = [_row(model=m) for m in ("baseline_market", "gefs_ensemble_raw")]
    models = resolve_models(rows, ["gefs_ensemble_raw"])
    assert models == {"gefs_ensemble_raw"}
    assert sorted(build_frame(rows, models=models).forecasts) == ["gefs_ensemble_raw"]


def test_withholding_everything_raises_rather_than_reporting_an_empty_frame() -> None:
    """An all-frozen table must not come back as 'nothing has resolved yet'.

    That is false, and it is this repo's recurring failure mode -- a check answering an
    easier question than the one asked.
    """
    rows = [_row(model="gefs_ensemble_raw")]
    with pytest.raises(ValueError, match="every model present is frozen"):
        resolve_models(rows, None)


def test_no_rows_at_all_is_not_an_error() -> None:
    """Before anything resolves there is genuinely nothing to withhold."""
    assert resolve_models([], None) == set()


def test_an_unknown_model_raises_rather_than_scoring_nothing() -> None:
    """A typo would otherwise produce an empty frame, which reads as 'nothing resolved'."""
    rows = [_row(model="baseline_market")]
    with pytest.raises(ValueError, match="unknown model_version gefs_ensemble_ra"):
        resolve_models(rows, ["gefs_ensemble_ra"])


def test_excluding_a_sparse_model_can_only_keep_more_rows() -> None:
    """The filter runs before the completeness rule, not after.

    M2 carries both baselines but no H2 forecast -- `WeatherForecaster` refuses whenever
    the station or ensemble is missing, so this is the normal case, not an edge one.
    Filtering afterwards would drop M2 for lacking a column the caller had just excluded,
    silently shrinking the baseline sample to whatever H2 happened to cover.
    """
    rows = [
        _row(market="M1", model="baseline_market", family="F1"),
        _row(market="M1", model="baseline_shrink", family="F1"),
        _row(market="M1", model="gefs_ensemble_raw", family="F1"),
        _row(market="M2", model="baseline_market", family="F2"),
        _row(market="M2", model="baseline_shrink", family="F2"),
    ]
    assert len(build_frame(rows)) == 1  # unfiltered: only M1 carries every model
    kept = build_frame(rows, models=resolve_models(rows, None))
    assert len(kept) == 2
    assert sorted(kept.market_ids.tolist()) == ["M1", "M2"]


def test_an_excluded_model_cannot_anchor_the_price_instant() -> None:
    """`market_mid` and `ts` come from the key's first accepted row.

    If an excluded model could still anchor the slot, a frozen forecast emitted at an
    earlier instant would pin every baseline to a price none of them were scored against
    -- the same class of bug as the re-emission mix below, arriving by a different door.
    """
    rows = [
        _row(model="gefs_ensemble_raw", p=0.40, mid=0.40, ts=1),
        _row(model="baseline_market", p=0.72, mid=0.72, ts=2),
        _row(model="baseline_shrink", p=0.698, mid=0.72, ts=2),
    ]
    f = build_frame(rows, models=resolve_models(rows, None))
    assert len(f) == 1
    assert f.market_mid[0] == 0.72
    np.testing.assert_array_equal(f.forecasts["baseline_market"], f.market_mid)


def test_a_re_emitted_horizon_does_not_mix_two_instants() -> None:
    """The emitter can write the same (market, horizon) twice as the price moves.

    Taking `market_mid` from the first row and `p_hat` from the last pairs a forecast
    with a price it never saw. It broke G2 on live data: the null model, whose p_hat is
    the mid itself, scored a non-zero edge out of nothing.
    """
    rows = [
        _row(model="baseline_market", p=0.60, mid=0.60, ts=1),
        _row(model="baseline_shrink", p=0.59, mid=0.60, ts=1),
        _row(model="baseline_market", p=0.72, mid=0.72, ts=2),  # re-emitted later
        _row(model="baseline_shrink", p=0.698, mid=0.72, ts=2),
    ]
    f = build_frame(rows)
    assert len(f) == 1
    assert f.market_mid[0] == 0.60
    np.testing.assert_array_equal(f.forecasts["baseline_market"], f.market_mid)
