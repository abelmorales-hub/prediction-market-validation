"""Kalshi trade-api v2 implementation of MarketDataSource.

Every model below was derived from responses recorded in data/raw_samples/, not from
documentation. Things the docs and write-ups get wrong, verified live on 2026-09-01:

* Prices arrive as *strings of dollars* ("0.6900"), not integer cents. Sizes likewise,
  under `_fp` names ("188966.33").
* `GET /markets/{t}/orderbook` returns `orderbook_fp` with `yes_dollars` and
  `no_dollars`, and **both are bid stacks**. A NO bid at p is a YES ask at 1-p. Reading
  `no_dollars` as an ask ladder silently inverts the book.
* Levels arrive ascending by price, so the touch is the *last* element of each list.
* The `status` query value and the returned `status` differ: `?status=open` yields
  markets whose status reads `active`; `?status=active` is a 400.
* `category` is not on the market. It lives on the series (and event).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from src.ingest.base import Book, BookLevel, Market, PricePoint, Resolution
from src.ingest.http import TICK_ATTEMPTS, DiskCache, PacedClient
from src.ingest.stations import station_for

SETTLED_RESULTS = {"yes": 1, "no": 0}

# The settlement rule, not the title. `rules_primary` reads:
#
#   "If the maximum temperature recorded at Austin (CLIAUS) for Sep 5, 2026, is between
#    94-95 fahrenheit according to The Weather Company, then the market resolves to Yes."
#
# Both facts a temperature forecast needs -- which station, which local day -- appear
# there and nowhere else structurally. The title carries the same day in prose and the
# ticker encodes it as "26SEP05", but the ticker cannot distinguish a `less` rung from a
# `greater` one (T94 is "below 94", T101 is "102 or above"), so it is not a source of
# truth for anything. Parse the rule or decline.
_STATION_RE = re.compile(r"\((CLI[A-Z]{3})\)")
_TARGET_RE = re.compile(r"\bfor ([A-Z][a-z]{2}) (\d{1,2}), (\d{4})\b")

# Explicit rather than strptime("%b %d, %Y"): %b reads the process locale, and this
# process runs in Europe. A locale that spells September "sept" would silently stop
# every weather market cataloguing correctly, and predictions cannot be backfilled.
_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def station_from_rules(rules: str) -> str | None:
    """The CLI station code named in the settlement rule, if it is one we know.

    An unrecognised code returns None rather than being passed through: `stations.py`
    holds the coordinates and timezone, and a code absent from it has no grid point to
    forecast at. Silently accepting it would catalogue a market as forecastable that
    nothing can then forecast.
    """
    m = _STATION_RE.search(rules or "")
    if m is None:
        return None
    return m.group(1) if station_for(m.group(1)) else None


def settles_on_from_rules(rules: str) -> date | None:
    """The local calendar day the settlement rule measures."""
    m = _TARGET_RE.search(rules or "")
    if m is None:
        return None
    month = _MONTHS.get(m.group(1))
    if month is None:
        return None
    try:
        return date(int(m.group(3)), month, int(m.group(2)))
    except ValueError:
        return None


def _num(v: Any) -> float | None:
    """Kalshi sends numbers as strings and absence as an empty string."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class KalshiMarket(BaseModel):
    """Fields we actually use. `extra='ignore'` because the payload carries ~40 of them
    and pinning all would break on the next additive change."""

    model_config = ConfigDict(extra="ignore")

    ticker: str
    event_ticker: str
    title: str = ""
    status: str = ""
    result: str = ""
    open_time: datetime | None = None
    close_time: datetime
    expiration_time: datetime | None = None
    mve_collection_ticker: str = ""
    volume_fp: float = 0.0
    yes_bid_dollars: float | None = None
    yes_ask_dollars: float | None = None
    # Scalar settlement. Present on strike ladders, absent (or empty string) elsewhere.
    strike_type: str = ""
    floor_strike: float | None = None
    cap_strike: float | None = None
    rules_primary: str = ""

    @field_validator("volume_fp", mode="before")
    @classmethod
    def _vol(cls, v: Any) -> float:
        return _num(v) or 0.0

    @field_validator(
        "yes_bid_dollars", "yes_ask_dollars", "floor_strike", "cap_strike", mode="before"
    )
    @classmethod
    def _price(cls, v: Any) -> float | None:
        return _num(v)

    @property
    def series_ticker(self) -> str:
        return self.ticker.split("-", 1)[0]

    def to_market(self, category: str | None = None) -> Market:
        return Market(
            market_id=self.ticker,
            event_family=self.event_ticker,
            series_id=self.series_ticker,
            question=self.title,
            category=category,
            status=self.status,
            open_time=self.open_time,
            close_time=self.close_time,
            volume=self.volume_fp,
            yes_bid=self.yes_bid_dollars,
            yes_ask=self.yes_ask_dollars,
            is_combo=bool(self.mve_collection_ticker),
            strike_type=self.strike_type or None,
            floor_strike=self.floor_strike,
            cap_strike=self.cap_strike,
            settles_on=settles_on_from_rules(self.rules_primary),
            settlement_station=station_from_rules(self.rules_primary),
        )


class KalshiSource:
    """Read-only client. Metadata is cached on disk with a long TTL; books never are."""

    def __init__(
        self,
        base_url: str,
        timeout: float,
        cache_dir: Path | None = None,
        metadata_ttl_s: float = 86_400.0,
    ) -> None:
        cache = DiskCache(cache_dir, metadata_ttl_s) if cache_dir is not None else None
        self._http = PacedClient(base_url, timeout, cache=cache)
        self._categories: dict[str, str] | None = None

    def __enter__(self) -> KalshiSource:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # -- categories -------------------------------------------------------

    def series_categories(self) -> dict[str, str]:
        """series_ticker -> category. One request returns the whole catalogue
        (~13.7k series), so it is fetched once and cached rather than joined per market."""
        if self._categories is None:
            body = self._http.get_json("/series", {"limit": 1000}, cacheable=True)
            self._categories = {
                s["ticker"]: s.get("category", "")
                for s in body.get("series", [])
                if s.get("ticker")
            }
        return self._categories

    # -- MarketDataSource -------------------------------------------------

    def list_open_markets(
        self,
        *,
        closing_within_s: int | None = None,
        include_combos: bool = False,
        series_tickers: Sequence[str] | None = None,
    ) -> list[Market]:
        """Open markets, either venue-wide or one series at a time.

        **Pass `series_tickers` for anything that must not silently return nothing.**
        The venue-wide sweep is not reliable: `/markets?status=open` is ordered such that
        `KXMVECROSSCATEGORY-SHARD1-*` combo rows can occupy every page, and on 2026-09-07
        they occupied at least 12,000 consecutive rows — far past `_paged`'s 25-page cap,
        which is itself there because the listing is unbounded. The combo filter below
        then removes all of them and returns an empty list. Nothing errors: the caller
        sees a venue with no open markets.

        `/events?with_nested_markets=true` is not a way out. It ignores `min_close_ts` /
        `max_close_ts` entirely, and 6,000 events contained 9 KXHIGH markets.

        Filtering by `series_ticker` server-side is the only form of this query that
        cannot be crowded out, which is the same lesson as `list_recently_resolved`:
        drive the query from what the study is looking for, never from the whole venue
        and a hope that it fits.
        """
        params: dict[str, Any] = {"limit": 200, "status": "open"}
        if closing_within_s is not None:
            now = int(datetime.now(UTC).timestamp())
            params["min_close_ts"] = now
            params["max_close_ts"] = now + closing_within_s

        if series_tickers is None:
            rows = self._paged(params)
        else:
            rows = []
            for ticker in series_tickers:
                rows += self._paged({**params, "series_ticker": ticker})

        cats = self.series_categories()
        out = []
        for row in rows:
            m = KalshiMarket.model_validate(row)
            if m.mve_collection_ticker and not include_combos:
                continue
            out.append(m.to_market(cats.get(m.series_ticker) or None))
        return out

    def _paged(self, params: dict[str, Any], max_pages: int = 25) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor = ""
        for _ in range(max_pages):
            page = {**params, "cursor": cursor} if cursor else params
            body = self._http.get_json("/markets", page)
            rows += body.get("markets", [])
            cursor = body.get("cursor") or ""
            if not cursor:
                break
        return rows

    def get_market(self, market_id: str) -> Market:
        body = self._http.get_json(f"/markets/{market_id}")
        m = KalshiMarket.model_validate(body["market"])
        return m.to_market(self.series_categories().get(m.series_ticker) or None)

    def get_book(self, market_id: str, *, depth: int = 100) -> Book:
        # The one call on the dense sweep's critical path, so it takes the short retry
        # budget: the sweep's own cadence is the retry, and a market that hogs the long
        # ladder starves every other market in the same sweep. See TICK_ATTEMPTS.
        body = self._http.get_json(
            f"/markets/{market_id}/orderbook", {"depth": depth}, attempts=TICK_ATTEMPTS
        )
        ob = body.get("orderbook_fp") or {}
        return build_book(market_id, ob.get("yes_dollars"), ob.get("no_dollars"))

    def get_price_history(
        self, market_id: str, start: datetime, end: datetime, *, interval_min: int = 1
    ) -> list[PricePoint]:
        series = market_id.split("-", 1)[0]
        body = self._http.get_json(
            f"/series/{series}/markets/{market_id}/candlesticks",
            {
                "start_ts": int(start.timestamp()),
                "end_ts": int(end.timestamp()),
                "period_interval": interval_min,
            },
        )
        return [_to_price_point(c) for c in body.get("candlesticks", [])]

    def get_resolution(self, market_id: str) -> Resolution | None:
        """Settlement for one known ticker, or None if it has not settled.

        The venue-wide scan below cannot reach a specific market: ~5k combo shards settle
        per day and they fill every page of `?status=settled` in natural order. Asking
        about the markets we catalogued inverts the query — one request each, no
        pagination, no shard flood.
        """
        body = self._http.get_json(f"/markets/{market_id}")
        m = KalshiMarket.model_validate(body["market"])
        outcome = SETTLED_RESULTS.get(m.result.lower())
        if outcome is None:
            return None  # still open, void, or unrecognised: never guessed into a 0 or 1
        return Resolution(
            market_id=m.ticker,
            resolved_at=_settled_at(m, datetime.now(UTC)),
            outcome=outcome,
            raw_result=m.result,
        )

    def list_recently_resolved(self, since: datetime) -> list[Resolution]:
        rows = self._paged(
            {"limit": 200, "status": "settled", "min_close_ts": int(since.timestamp())}
        )
        now = datetime.now(UTC)
        out = []
        for row in rows:
            m = KalshiMarket.model_validate(row)
            outcome = SETTLED_RESULTS.get(m.result.lower())
            if outcome is None:
                continue  # void / unrecognised: never guessed into a 0 or 1
            out.append(
                Resolution(
                    market_id=m.ticker,
                    resolved_at=_settled_at(m, now),
                    outcome=outcome,
                    raw_result=m.result,
                )
            )
        return out


def _settled_at(m: KalshiMarket, now: datetime) -> datetime:
    """Best available settlement timestamp. Kalshi exposes none directly.

    `expiration_time` is a *scheduled* expiry, not the moment of settlement, and for
    markets that close early it can sit days in the future while `status` already reads
    'finalized'. Using it unguarded put resolution timestamps after `now`, which silently
    excluded every such market from any window query — reconciliation reported zero
    resolutions while the venue had settled thousands.

    So: use it when it is in the past, otherwise fall back to `close_time`. Both are real
    venue timestamps and both are lower bounds on when the outcome became known. Neither
    is the settlement instant, and nothing downstream should treat it as one.
    """
    if m.expiration_time is not None and m.expiration_time <= now:
        return m.expiration_time
    return m.close_time


def build_book(
    market_id: str,
    yes_levels: list[list[str]] | None,
    no_levels: list[list[str]] | None,
    ts: datetime | None = None,
) -> Book:
    """Fold Kalshi's two bid stacks into one YES-side book.

    `no_levels` are NO *bids*: a NO bid at p is a YES ask at 1-p. Bids end ascending and
    asks descending, so on both sides the last element is the touch — highest bid,
    lowest ask — which is what Book.best_bid / best_ask read.
    """
    bids = tuple(sorted((_level(lv) for lv in yes_levels or []), key=lambda x: x.price))
    asks = tuple(
        sorted(
            (BookLevel(price=1.0 - lv.price, size=lv.size) for lv in map(_level, no_levels or [])),
            key=lambda x: x.price,
            reverse=True,
        )
    )
    return Book(market_id=market_id, ts=ts or datetime.now(UTC), yes_bids=bids, yes_asks=asks)


def _level(raw: list[str]) -> BookLevel:
    return BookLevel(price=float(raw[0]), size=float(raw[1]))


def _to_price_point(c: dict[str, Any]) -> PricePoint:
    price = c.get("price") or {}
    return PricePoint(
        ts=datetime.fromtimestamp(int(c["end_period_ts"]), tz=UTC),
        yes_bid_close=_num((c.get("yes_bid") or {}).get("close_dollars")),
        yes_ask_close=_num((c.get("yes_ask") or {}).get("close_dollars")),
        price_close=_num(price.get("close_dollars")),
        volume=_num(c.get("volume_fp")) or 0.0,
    )
