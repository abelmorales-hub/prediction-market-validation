"""Storage shape -> frame -> report, in one pass.

Every component is tested alone elsewhere. This exists because the failure that would
actually reach a conclusion is a wiring one: a pivot that misaligns a column, a horizon
that pairs a forecast with a later price. Those produce plausible numbers, and plausible
wrong numbers are the only kind that get believed.
"""

import numpy as np

from src.analysis.frame import build_frame
from src.baselines.simple import baseline_market, baseline_shrink
from src.scoring.report import build_report, score

RNG = np.random.default_rng(11)


def _stored_rows(n: int = 200):
    """Rows shaped exactly as Repo.scored_pairs returns them."""
    rows = []
    for i in range(n):
        mid = float(RNG.uniform(0.05, 0.95))
        y = "yes" if RNG.uniform() < mid else "no"
        family = f"F{i % 20}"
        category = ("Crypto", "Politics", "Weather")[i % 3]
        for model, p in (
            ("baseline_market", float(baseline_market([mid])[0])),
            ("baseline_shrink", float(baseline_shrink([mid])[0])),
        ):
            rows.append(
                {
                    "market_id": f"M{i}", "horizon_label": "T-24h", "model_version": model,
                    "p_hat": p, "market_mid": mid, "outcome": y, "was_disputed": False,
                    "event_family": family, "category": category, "ts": i,
                }
            )
    return rows


def test_the_null_survives_the_whole_chain() -> None:
    """baseline_market must still score exactly zero after a round trip through the
    storage row shape and the pivot. Any misalignment shows up here as a nonzero mean."""
    frame = build_frame(_stored_rows())
    row = score(
        "baseline_market",
        frame.forecasts["baseline_market"],
        frame.market_mid,
        frame.y,
        frame.clusters,
        n_iter=400,
    )
    assert row.mean_d == 0.0
    assert row.ci_low == 0.0 and row.ci_high == 0.0
    assert abs(row.brier_skill) < 1e-12
    assert row.verdict == "reference"


def test_a_misaligned_pivot_would_be_caught() -> None:
    """Proves the test above has teeth: shift one column by a row and the null breaks."""
    frame = build_frame(_stored_rows())
    shifted = np.roll(frame.forecasts["baseline_market"], 1)
    row = score("shifted", shifted, frame.market_mid, frame.y, frame.clusters, n_iter=400)
    assert row.mean_d != 0.0


def test_clusters_come_through_as_event_families_not_rows() -> None:
    frame = build_frame(_stored_rows(n=200))
    assert len(frame) == 200
    assert frame.n_clusters == 20, "20 families, not 200 rows"


def test_report_renders_from_stored_rows() -> None:
    frame = build_frame(_stored_rows())
    md = build_report(
        frame.forecasts,
        frame.market_mid,
        frame.y,
        frame.clusters,
        categories=frame.categories,
        n_iter=300,
    )
    assert "| baseline_market |" in md and "| baseline_shrink |" in md
    assert "### Crypto" in md and "### Politics" in md
    assert "## Excluding tail price buckets (G3)" in md
    # The null must read as the reference row, not as a suspiciously tiny edge.
    market_line = next(x for x in md.splitlines() if x.startswith("| baseline_market |"))
    assert "reference" in market_line
    assert "+0.0000" in market_line
