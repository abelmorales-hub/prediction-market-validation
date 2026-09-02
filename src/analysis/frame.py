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

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


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


def build_frame(rows: Sequence[dict[str, Any]], drop_disputed: bool = True) -> Frame:
    """Pivot `scored_pairs` output into aligned arrays.

    A disputed resolution is excluded by default: its outcome is exactly the thing under
    question, and scoring against a contested answer measures the dispute, not the
    forecast. The venue exposes no dispute field today, so this is currently a no-op that
    exists so the decision is recorded rather than rediscovered later.
    """
    keyed: dict[tuple[str, str], dict[str, Any]] = {}
    models: set[str] = set()
    for r in rows:
        if drop_disputed and r.get("was_disputed"):
            continue
        outcome = r["outcome"]
        if outcome is None:
            continue
        key = (r["market_id"], r["horizon_label"])
        slot = keyed.setdefault(
            key,
            {
                "market_mid": float(r["market_mid"]),
                "y": _as_binary(outcome),
                "cluster": str(r["event_family"]),
                "category": str(r["category"]),
                "horizon": str(r["horizon_label"]),
                "market_id": str(r["market_id"]),
                "p": {},
            },
        )
        slot["p"][str(r["model_version"])] = float(r["p_hat"])
        models.add(str(r["model_version"]))

    complete = [s for s in keyed.values() if set(s["p"]) == models]
    order = sorted(models)
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
