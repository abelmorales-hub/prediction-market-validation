"""Stratified reporting of the paired difference.

Design doc §4.2: **almost all fake edge lives in the tails.** Prediction markets
overprice longshots, so a model that merely shrinks toward the centre scores well in the
[0, 0.05] and [0.95, 1] buckets while knowing nothing. If an improvement disappears once
the extreme buckets are dropped, there is no edge — G3 requires the result not be
concentrated there.

Each stratum gets its own clustered bootstrap. Strata are never pooled to make an
interval look tighter.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from src.scoring.bootstrap import BootstrapResult, clustered_bootstrap

PRICE_BUCKETS: tuple[float, ...] = (0.0, 0.05, 0.20, 0.80, 0.95, 1.0)
TAIL_BUCKETS = ("[0.00, 0.05]", "(0.95, 1.00]")


@dataclass(frozen=True)
class Stratum:
    name: str
    result: BootstrapResult

    @property
    def n(self) -> int:
        return self.result.n


def bucket_labels(edges: tuple[float, ...] = PRICE_BUCKETS) -> list[str]:
    labels = [f"[{edges[0]:.2f}, {edges[1]:.2f}]"]
    labels += [f"({lo:.2f}, {hi:.2f}]" for lo, hi in zip(edges[1:-1], edges[2:], strict=True)]
    return labels


def assign_price_bucket(
    market_mid: object, edges: tuple[float, ...] = PRICE_BUCKETS
) -> NDArray[np.int64]:
    """Bucket index per row. Left-closed on the first bucket, right-closed thereafter."""
    mid = np.asarray(market_mid, dtype=np.float64)
    if mid.size and (np.any(mid < 0.0) or np.any(mid > 1.0)):
        raise ValueError("market_mid outside [0, 1]")
    idx = np.digitize(mid, np.asarray(edges[1:-1]), right=True)
    return np.asarray(np.clip(idx, 0, len(edges) - 2), dtype=np.int64)


def _stratify(
    d: NDArray[np.float64],
    clusters: NDArray[np.generic],
    keys: list[str],
    labels: list[str],
    min_n: int,
    **kw: object,
) -> list[Stratum]:
    out = []
    keys_arr = np.asarray(keys)
    for label in labels:
        mask = keys_arr == label
        if int(mask.sum()) < min_n:
            # Too few rows for a bootstrap to mean anything. Omitted rather than
            # reported with an interval wide enough to contain any conclusion.
            continue
        out.append(
            Stratum(label, clustered_bootstrap(d[mask], clusters[mask], **kw))  # type: ignore[arg-type]
        )
    return out


def by_price_bucket(
    d: object,
    market_mid: object,
    clusters: object,
    edges: tuple[float, ...] = PRICE_BUCKETS,
    min_n: int = 20,
    **kw: object,
) -> list[Stratum]:
    dv = np.asarray(d, dtype=np.float64)
    cv = np.asarray(clusters)
    labels = bucket_labels(edges)
    idx = assign_price_bucket(market_mid, edges)
    if not (dv.shape == cv.shape == idx.shape):
        raise ValueError("d, clusters and market_mid must align one-to-one")
    return _stratify(dv, cv, [labels[i] for i in idx], labels, min_n, **kw)


def by_category(
    d: object,
    categories: object,
    clusters: object,
    min_n: int = 20,
    **kw: object,
) -> list[Stratum]:
    dv = np.asarray(d, dtype=np.float64)
    cv = np.asarray(clusters)
    cats = [str(c) for c in np.asarray(categories)]
    if not (dv.shape == cv.shape == np.asarray(cats).shape):
        raise ValueError("d, clusters and categories must align one-to-one")
    return _stratify(dv, cv, cats, sorted(set(cats)), min_n, **kw)


def excluding_tails(
    d: object,
    market_mid: object,
    clusters: object,
    edges: tuple[float, ...] = PRICE_BUCKETS,
    **kw: object,
) -> BootstrapResult:
    """The same statistic with the extreme price buckets dropped.

    G3's teeth: if mean(d) survives here, the improvement is not just longshot bias.
    """
    dv = np.asarray(d, dtype=np.float64)
    cv = np.asarray(clusters)
    labels = bucket_labels(edges)
    idx = assign_price_bucket(market_mid, edges)
    keep = np.array([labels[i] not in TAIL_BUCKETS for i in idx])
    if not keep.any():
        raise ValueError("every observation is in a tail bucket")
    return clustered_bootstrap(dv[keep], cv[keep], **kw)  # type: ignore[arg-type]
