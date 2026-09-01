"""The seam that keeps the pipeline venue-agnostic.

Everything downstream depends on `MarketDataSource` and the models here, never on a
concrete venue module. Polymarket was the original target and proved unreachable; that
substitution cost nothing structural precisely because of this boundary.

Prices are YES-side probabilities in [0, 1] throughout. Venue quirks — Kalshi quoting
the NO side as its own book, prices arriving as dollar strings — are resolved in the
venue module, never leaked past this file.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class Market(BaseModel):
    """One binary market. `event_family` is the clustering key the bootstrap needs
    (design doc §5): markets sharing it are not independent observations."""

    model_config = ConfigDict(frozen=True)

    market_id: str
    event_family: str
    series_id: str
    question: str
    category: str | None = None
    status: str
    open_time: datetime | None = None
    close_time: datetime
    volume: float = 0.0
    yes_bid: float | None = None
    yes_ask: float | None = None
    is_combo: bool = False
    """Auto-generated multi-leg parlay. Dominates the raw listing and is almost
    certainly not the universe you want to study — filtered, not hidden."""


class BookLevel(BaseModel):
    model_config = ConfigDict(frozen=True)

    price: float
    size: float


class Book(BaseModel):
    """Both sides expressed as YES probabilities, best price last.

    P4 in the design doc says never execute against the midpoint, so `mid` is provided
    for scoring only — the market's own forecast at a timestamp — and the depth helpers
    exist because that is what actually caps size.
    """

    model_config = ConfigDict(frozen=True)

    market_id: str
    ts: datetime
    yes_bids: tuple[BookLevel, ...] = ()
    yes_asks: tuple[BookLevel, ...] = ()

    @property
    def best_bid(self) -> float | None:
        return self.yes_bids[-1].price if self.yes_bids else None

    @property
    def best_ask(self) -> float | None:
        return self.yes_asks[-1].price if self.yes_asks else None

    @property
    def mid(self) -> float | None:
        bid, ask = self.best_bid, self.best_ask
        return None if bid is None or ask is None else (bid + ask) / 2

    @property
    def spread(self) -> float | None:
        bid, ask = self.best_bid, self.best_ask
        return None if bid is None or ask is None else ask - bid

    def depth_within(self, delta: float = 0.01) -> tuple[float, float]:
        """Cumulative size within `delta` of each touch. Zero when a side is empty."""
        bid, ask = self.best_bid, self.best_ask
        bid_depth = (
            0.0 if bid is None else sum(lv.size for lv in self.yes_bids if lv.price >= bid - delta)
        )
        ask_depth = (
            0.0 if ask is None else sum(lv.size for lv in self.yes_asks if lv.price <= ask + delta)
        )
        return bid_depth, ask_depth


class PricePoint(BaseModel):
    """One historical bar. Bid and ask are carried because a mid reconstructed from
    trade prices alone is not the same quantity the scorer compares against."""

    model_config = ConfigDict(frozen=True)

    ts: datetime
    yes_bid_close: float | None = None
    yes_ask_close: float | None = None
    price_close: float | None = None
    volume: float = 0.0

    @property
    def mid(self) -> float | None:
        if self.yes_bid_close is None or self.yes_ask_close is None:
            return None
        return (self.yes_bid_close + self.yes_ask_close) / 2


class Resolution(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_id: str
    resolved_at: datetime
    outcome: int
    raw_result: str


@runtime_checkable
class MarketDataSource(Protocol):
    """Read-only. No method here can place, sign, or cancel anything."""

    def list_open_markets(
        self, *, closing_within_s: int | None = None, include_combos: bool = False
    ) -> list[Market]: ...

    def get_market(self, market_id: str) -> Market: ...

    def get_book(self, market_id: str, *, depth: int = 100) -> Book: ...

    def get_price_history(
        self, market_id: str, start: datetime, end: datetime, *, interval_min: int = 1
    ) -> list[PricePoint]: ...

    def list_recently_resolved(self, since: datetime) -> list[Resolution]: ...
