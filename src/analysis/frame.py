"""Turn stored predictions into the arrays the scorer takes.

`scored_pairs` returns one row per (market, horizon, model). The report wants one column
per model over a *common* set of rows, so the rows are pivoted and any (market, horizon)
not carrying every model is dropped. Scoring model A on rows model B never saw would
compare them on different events and call the difference skill.

Clustering is by `event_family`, per P5. Note that the same market at T-24h, T-6h and
T-1h is three rows of one event: they share an outcome and almost all of their
information. Treating them as independent would inflate n threefold for free, so the
cluster is the family, never the row and never the market.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from src.analysis.horizons import HORIZONS, TOLERANCE

FloatArray = NDArray[np.float64]

# Models whose score is a pre-committed single look, and so must never appear in a report
# by accident. H2 is frozen at 782bb44 and gets exactly one G3 reading; a "quick sanity
# check" that happens to include it spends that reading without anyone deciding to.
#
# Duplicated from `src.analysis.weather.MODEL_VERSION` on purpose: importing it here would
# drag `src.storage` and `src.models` into what is otherwise a pure pivot over dicts.
# `test_frozen_models_matches_the_declared_model_version` fails if the two ever drift.
FROZEN_MODELS = frozenset({"gefs_ensemble_raw"})


def resolve_models(rows: Sequence[dict[str, Any]], selected: Sequence[str] | None) -> set[str]:
    """Which model_versions a report may show, given an explicit `--model` selection.

    Default (`selected` empty) is every model present *except* the frozen ones, so the
    unguarded command is the safe one. Naming a frozen model is allowed -- that is what
    the G3 run does -- but it has to be typed, which makes it a decision and leaves it in
    the shell history.

    An unknown name raises rather than silently scoring nothing: a typo would otherwise
    produce an empty frame, which reads exactly like "nothing has resolved yet".
    """
    available = {str(r["model_version"]) for r in rows}
    if not selected:
        keep = available - FROZEN_MODELS
        if available and not keep:
            # Otherwise the caller gets an empty frame and reports "nothing has resolved
            # yet", which is false and is this project's recurring failure: a check
            # quietly answering an easier question than the one asked.
            raise ValueError(
                f"every model present is frozen ({', '.join(sorted(available))}); "
                "name one with --model to spend its look deliberately"
            )
        return keep
    unknown = sorted(set(selected) - available)
    if unknown:
        raise ValueError(
            f"unknown model_version {', '.join(unknown)}; present: {', '.join(sorted(available))}"
        )
    return set(selected)


@dataclass(frozen=True)
class Frame:
    forecasts: dict[str, FloatArray]
    market_mid: FloatArray
    y: FloatArray
    clusters: NDArray[np.str_]
    categories: NDArray[np.str_]
    horizons: NDArray[np.str_]
    market_ids: NDArray[np.str_]

    def __len__(self) -> int:
        return int(self.y.size)

    @property
    def n_clusters(self) -> int:
        return int(np.unique(self.clusters).size)

    def restricted_to(self, horizon: str) -> Frame:
        mask = self.horizons == horizon
        return Frame(
            forecasts={k: v[mask] for k, v in self.forecasts.items()},
            market_mid=self.market_mid[mask],
            y=self.y[mask],
            clusters=self.clusters[mask],
            categories=self.categories[mask],
            horizons=self.horizons[mask],
            market_ids=self.market_ids[mask],
        )


def build_frame(
    rows: Sequence[dict[str, Any]],
    drop_disputed: bool = True,
    models: Collection[str] | None = None,
) -> Frame:
    """Pivot `scored_pairs` output into aligned arrays.

    A disputed resolution is excluded by default: its outcome is exactly the thing under
    question, and scoring against a contested answer measures the dispute, not the
    forecast. The venue exposes no dispute field today, so this is currently a no-op that
    exists so the decision is recorded rather than rediscovered later.

    `models` restricts which model_versions are pivoted; `None` keeps every one present.
    The filter runs *before* the completeness rule below, not after, and that ordering is
    the whole point: dropping a model afterwards would leave the frame trimmed to the rows
    that model happened to cover, so excluding a sparse model would silently shrink the
    sample the remaining ones are scored on. Filtering first means a narrower selection can
    only ever keep more rows. Use `resolve_models` to build the set from a CLI flag.
    """
    keyed: dict[tuple[str, str], dict[str, Any]] = {}
    present: set[str] = set()
    for r in rows:
        if drop_disputed and r.get("was_disputed"):
            continue
        outcome = r["outcome"]
        if outcome is None:
            continue
        # Before the slot is created, so an excluded model cannot anchor the key's `ts`
        # and `market_mid`. The anchor must be the earliest emission among the models
        # actually being scored.
        if models is not None and str(r["model_version"]) not in models:
            continue
        key = (r["market_id"], r["horizon_label"])
        slot = keyed.setdefault(
            key,
            {
                "ts": r["ts"],
                "market_mid": float(r["market_mid"]),
                "y": _as_binary(outcome),
                "cluster": str(r["event_family"]),
                "category": str(r["category"]),
                "horizon": str(r["horizon_label"]),
                "market_id": str(r["market_id"]),
                "p": {},
            },
        )
        # A re-emitted horizon carries a *later* price. `market_mid` is taken from the
        # first row of the key, so accepting a p_hat from a different instant would pair
        # a forecast against a price it never saw -- and the null model, whose p_hat is
        # the mid itself, would score a spurious non-zero edge. Rows are ordered by ts,
        # so the earliest emission wins and later ones are dropped whole.
        if r["ts"] != slot["ts"]:
            continue
        slot["p"][str(r["model_version"])] = float(r["p_hat"])
        present.add(str(r["model_version"]))

    complete = [s for s in keyed.values() if set(s["p"]) == present]
    order = sorted(present)
    if not complete:
        return Frame(
            forecasts={m: _empty() for m in order},
            market_mid=_empty(),
            y=_empty(),
            clusters=_empty_str(),
            categories=_empty_str(),
            horizons=_empty_str(),
            market_ids=_empty_str(),
        )

    return Frame(
        forecasts={m: np.array([s["p"][m] for s in complete], dtype=np.float64) for m in order},
        market_mid=np.array([s["market_mid"] for s in complete], dtype=np.float64),
        y=np.array([s["y"] for s in complete], dtype=np.float64),
        clusters=np.array([s["cluster"] for s in complete], dtype=np.str_),
        categories=np.array([s["category"] for s in complete], dtype=np.str_),
        horizons=np.array([s["horizon"] for s in complete], dtype=np.str_),
        market_ids=np.array([s["market_id"] for s in complete], dtype=np.str_),
    )


@dataclass(frozen=True)
class SampleCensus:
    """Why rows left, rule by rule. Printed with every G3 report.

    A sample defined only in prose is the fifth gate answering an easier question
    (Amendment 10). This is the arithmetic that has to reconcile: `rows_in` minus every
    `dropped_*` equals `kept`.
    """

    rows_in: int
    kept: int
    dropped_before: int
    dropped_after: int
    dropped_excluded: int
    dropped_unverifiable: int
    dropped_stale: int

    def as_lines(self) -> list[str]:
        return [
            f"rows from scored_pairs           {self.rows_in}",
            f"  before the window start       -{self.dropped_before}",
            f"  at or after the cut           -{self.dropped_after}",
            f"  market on the exclusion list  -{self.dropped_excluded}",
            f"  horizon not verifiable        -{self.dropped_unverifiable}",
            f"  snapshot outside tolerance    -{self.dropped_stale}",
            f"rows admitted to the sample      {self.kept}",
        ]


def g3_sample(
    rows: Sequence[dict[str, Any]],
    since: datetime | None = None,
    until: datetime | None = None,
    exclude: Collection[str] = (),
    max_lag: timedelta = TOLERANCE,
) -> tuple[list[dict[str, Any]], SampleCensus]:
    """Amendment 11's declared G3 sample, as a row filter applied *before* the pivot.

    Three rules, in this order:

    1. **Window.** `since <= ts < until`. The cut is a fixed instant so the sample cannot
       quietly grow between the amendment and the run.
    2. **Exclusion list.** Markets carrying a CRITICAL gap in their own dense windows,
       read from a committed file rather than recomputed, so the sample stays reproducible
       after the parquet grows.
    3. **Integrity.** The snapshot standing for the horizon must sit at or before it and
       within `max_lag`. `pick` already guarantees this at emission time, but `end_date` is
       refreshed on every catalogue pass, so a venue that moved a close *after* the fact
       would retroactively turn a sound pairing into a stale one. This is the property
       section 5's void rule protects, and checking it per observation is stronger than
       inferring it from the absence of gaps.

    Fails closed: a row whose horizon offset or `end_date` is missing cannot be verified
    and is not admitted. Filtering before `build_frame` matters for the same reason its
    own docstring gives -- the completeness rule drops any (market, horizon) not carrying
    every model, so filtering afterwards would trim the frame to whatever survived.
    """
    offsets = dict(HORIZONS)
    excluded = frozenset(exclude)
    kept: list[dict[str, Any]] = []
    before = after = dropped_excluded = unverifiable = stale = 0

    for r in rows:
        ts = r["ts"]
        if since is not None and ts < since:
            before += 1
            continue
        if until is not None and ts >= until:
            after += 1
            continue
        if str(r["market_id"]) in excluded:
            dropped_excluded += 1
            continue
        offset = offsets.get(str(r["horizon_label"]))
        close = r.get("end_date")
        if offset is None or close is None:
            unverifiable += 1
            continue
        lag = (close - offset) - ts
        if not timedelta(0) <= lag <= max_lag:
            stale += 1
            continue
        kept.append(r)

    return kept, SampleCensus(
        rows_in=len(rows),
        kept=len(kept),
        dropped_before=before,
        dropped_after=after,
        dropped_excluded=dropped_excluded,
        dropped_unverifiable=unverifiable,
        dropped_stale=stale,
    )


def read_exclusions(path: Path) -> set[str]:
    """Market ids from a `--critical-markets` file. `#` comments and blanks ignored."""
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def _as_binary(outcome: Any) -> float:
    """Outcomes arrive as 'yes'/'no' or as a boolean depending on the driver."""
    if isinstance(outcome, bool):
        return 1.0 if outcome else 0.0
    text = str(outcome).strip().lower()
    if text in {"yes", "true", "1"}:
        return 1.0
    if text in {"no", "false", "0"}:
        return 0.0
    raise ValueError(f"unrecognised outcome {outcome!r}")


def _empty() -> FloatArray:
    return np.array([], dtype=np.float64)


def _empty_str() -> NDArray[np.str_]:
    return np.array([], dtype=np.str_)
