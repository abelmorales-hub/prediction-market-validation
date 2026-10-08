"""Amendment 11's sample definition, enforced rather than asserted in prose.

The failure these guard against is the one this project keeps repeating: a rule the
pre-declaration declares and no code checks, so the gate answers an easier question.
"""

from datetime import UTC, datetime, timedelta

from src.analysis.frame import build_frame, g3_sample, read_exclusions

CLOSE = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
TARGET = CLOSE - timedelta(hours=24)  # the T-24h horizon


def _row(market="M1", model="gefs_ensemble_raw", lag_min=2.5, horizon="T-24h", close=CLOSE):
    return {
        "market_id": market,
        "horizon_label": horizon,
        "model_version": model,
        "p_hat": 0.7,
        "market_mid": 0.6,
        "outcome": "yes",
        "was_disputed": False,
        "event_family": "F1",
        "category": "Climate and Weather",
        "end_date": close,
        "ts": (close - timedelta(hours=24)) - timedelta(minutes=lag_min),
    }


def test_a_snapshot_past_the_tolerance_is_not_admitted() -> None:
    """The property section 5's void rule protects: no forecast paired with a stale price."""
    kept, census = g3_sample([_row(lag_min=2.0), _row(market="M2", lag_min=9.0)])
    assert [r["market_id"] for r in kept] == ["M1"]
    assert census.dropped_stale == 1


def test_a_snapshot_after_the_horizon_is_not_admitted() -> None:
    """A later snapshot has seen trading the forecast had not -- a free look at the future."""
    kept, census = g3_sample([_row(lag_min=-1.0)])
    assert kept == []
    assert census.dropped_stale == 1


def test_a_close_that_moved_after_emission_invalidates_the_pairing() -> None:
    """`end_date` is refreshed on every catalogue pass, so this can happen retroactively."""
    row = _row(lag_min=2.0)
    row["end_date"] = CLOSE + timedelta(hours=3)  # venue moved the close
    kept, census = g3_sample([row])
    assert kept == []
    assert census.dropped_stale == 1


def test_the_window_is_half_open_so_the_cut_cannot_drift() -> None:
    early = _row(market="EARLY", close=CLOSE - timedelta(days=5))
    kept, census = g3_sample(
        [early, _row()], since=TARGET - timedelta(days=1), until=TARGET + timedelta(minutes=1)
    )
    assert [r["market_id"] for r in kept] == ["M1"]
    assert census.dropped_before == 1

    kept, census = g3_sample([_row()], until=_row()["ts"])
    assert kept == []
    assert census.dropped_after == 1


def test_excluded_markets_are_dropped_and_counted() -> None:
    kept, census = g3_sample([_row(market="M1"), _row(market="M2")], exclude={"M2"})
    assert [r["market_id"] for r in kept] == ["M1"]
    assert census.dropped_excluded == 1


def test_a_row_that_cannot_be_verified_fails_closed() -> None:
    no_close = _row()
    no_close["end_date"] = None
    bad_horizon = _row(market="M2", horizon="T-3h")
    kept, census = g3_sample([no_close, bad_horizon])
    assert kept == []
    assert census.dropped_unverifiable == 2


def test_the_census_reconciles() -> None:
    rows = [_row(), _row(market="M2", lag_min=9.0), _row(market="M3"), _row(market="M4")]
    _, c = g3_sample(rows, exclude={"M3"}, until=TARGET + timedelta(minutes=1))
    assert (
        c.rows_in
        - (
            c.dropped_before
            + c.dropped_after
            + c.dropped_excluded
            + c.dropped_unverifiable
            + c.dropped_stale
        )
        == c.kept
    )


def test_filtering_runs_before_the_pivot() -> None:
    """Filter after `build_frame` and the completeness rule would trim the frame to
    whatever survived; filtering first can only ever keep more rows."""
    rows = [
        _row(market="M1", model="baseline_market"),
        _row(market="M1", model="gefs_ensemble_raw"),
        _row(market="M2", model="baseline_market"),
        _row(market="M2", model="gefs_ensemble_raw", lag_min=9.0),  # stale: M2 loses a model
    ]
    kept, _ = g3_sample(rows)
    frame = build_frame(kept)
    assert frame.market_ids.tolist() == ["M1"]


def test_exclusion_file_ignores_comments_and_blanks(tmp_path) -> None:
    path = tmp_path / "excluded.txt"
    path.write_text("# 3 gaps over 2 markets\nKXHIGHDEN-26SEP30-T66\n\nKXHIGHLAX-26OCT04-B78.5\n")
    assert read_exclusions(path) == {"KXHIGHDEN-26SEP30-T66", "KXHIGHLAX-26OCT04-B78.5"}
