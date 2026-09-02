"""Snapshot runner: poll open markets on the cadence, append to Parquet.

Idempotent and resumable. All scheduling state lives in the Parquet already on disk —
the last recorded timestamp per market — so a kill -9 costs at most the unflushed
buffer, and a restart neither re-polls a market inside its current interval nor writes
a row it already has.

The milestone suggests deduplicating on `book_hash`. Idempotency here comes from the
last-timestamp check instead, which subsumes it: once a market has been recorded for
the current interval it is not fetched again, so no duplicate can be produced to
compare hashes against. `book_hash` is still stored, because downstream needs to tell
"the book did not move" from "we did not look" — but rows are never *suppressed* on an
unchanged hash. A missing row has to mean we failed to observe, or the gap report
cannot distinguish a dead market from a dead runner.

Failures are recorded, never swallowed. A market that could not be fetched leaves a
counted, logged miss; the gap report reads the same evidence from the data itself.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from src.ingest.base import Market, MarketDataSource
from src.scheduler.cadence import in_dense_window, interval_at
from src.storage.parquet_store import BookSnapshot, read_snapshots, write_snapshots
from src.storage.supabase_repo import Repo

log = logging.getLogger(__name__)


@dataclass
class RunStats:
    polled: int = 0
    written: int = 0
    skipped_not_due: int = 0
    failed: int = 0
    failures: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, int]:
        return {
            "polled": self.polled,
            "written": self.written,
            "skipped_not_due": self.skipped_not_due,
            "failed": self.failed,
        }


class SnapshotRunner:
    def __init__(
        self,
        source: MarketDataSource,
        root: Path,
        buffer_size: int = 100,
        min_volume: float = 1000.0,
        max_markets: int = 400,
        repo: Repo | None = None,
    ) -> None:
        self.source = source
        self.root = root
        self.buffer_size = buffer_size
        self.min_volume = min_volume
        self.max_markets = max_markets
        # Optional: mirror the tracked universe into the Supabase catalogue. Without it
        # `markets` stays empty, the resolution watcher has nothing to join against, and
        # reconciliation cannot tell which resolved markets we were even watching.
        self.repo = repo
        self.stats = RunStats()
        self._buffer: list[BookSnapshot] = []
        self._last: dict[str, datetime] = {}
        self._resume()

    def _resume(self) -> None:
        """Rebuild scheduling state from what is already on disk."""
        for snap in read_snapshots(self.root):
            prev = self._last.get(snap.market_id)
            if prev is None or snap.ts > prev:
                self._last[snap.market_id] = snap.ts
        if self._last:
            log.info("resumed with %d markets already recorded", len(self._last))

    @staticmethod
    def _by_urgency(markets: Sequence[Market], now: datetime) -> list[Market]:
        """Dense-window markets first, then soonest to close.

        A sweep can run longer than a tick. When it does, the markets that get sampled
        must be the ones on a prediction horizon, not whichever happened to sort first.
        """
        return sorted(
            markets,
            key=lambda m: (not in_dense_window(now, m.close_time), m.close_time),
        )

    def is_due(self, market: Market, now: datetime) -> bool:
        if now >= market.close_time:
            return False
        last = self._last.get(market.market_id)
        if last is None:
            return True
        return now - last >= interval_at(now, market.close_time)

    def poll(self, markets: Sequence[Market], now: datetime | None = None) -> list[BookSnapshot]:
        """One sweep. Returns what was captured this call, not what was flushed."""
        now = now or datetime.now(UTC)
        captured = []
        for market in self._by_urgency(markets, now):
            if not self.is_due(market, now):
                self.stats.skipped_not_due += 1
                continue
            try:
                book = self.source.get_book(market.market_id)
            except Exception as exc:  # noqa: BLE001 - a miss is data, not a crash
                self.stats.failed += 1
                self.stats.failures[type(exc).__name__] += 1
                log.warning("snapshot failed for %s: %s", market.market_id, exc)
                continue

            self.stats.polled += 1
            snap = BookSnapshot.from_book(book, market.close_time, observed_at=now)
            self._buffer.append(snap)
            self._last[market.market_id] = snap.ts
            captured.append(snap)

            # Inside the loop, not after it. A sweep over a large universe takes minutes,
            # and flushing only at the end means an interruption loses the whole sweep.
            if len(self._buffer) >= self.buffer_size:
                self.flush()
        return captured

    def flush(self) -> int:
        """Write the buffer out. Safe to call at any point, including on shutdown."""
        if not self._buffer:
            return 0
        write_snapshots(self._buffer, self.root)
        n = len(self._buffer)
        self.stats.written += n
        self._buffer.clear()
        log.info("flushed %d snapshots", n)
        return n

    def universe(self, closing_within_s: int) -> list[Market]:
        """The markets worth tracking.

        Unfiltered, a 30-hour window returns thousands — dominated by strike ladders
        where every rung is its own market and almost none ever trade. A sweep that
        large cannot finish inside a one-minute dense cadence, so the ladder rungs would
        crowd out the markets the study is actually about. The design doc's own estimate
        is 20-40 relevant markets a day.

        This is a research-universe decision, not a technical one: raising min_volume
        buys cadence fidelity and costs sample size, and both feed the power calculation.
        """
        markets = self.source.list_open_markets(closing_within_s=closing_within_s)
        liquid = [m for m in markets if m.volume >= self.min_volume]
        liquid.sort(key=lambda m: -m.volume)
        kept = liquid[: self.max_markets]
        log.info(
            "universe: %d open -> %d above volume %g -> tracking %d",
            len(markets),
            len(liquid),
            self.min_volume,
            len(kept),
        )
        if self.repo is not None and kept:
            try:
                self.repo.upsert_markets(kept)
            except Exception as exc:  # noqa: BLE001 - catalogue is secondary to capture
                self.stats.failures[f"catalogue:{type(exc).__name__}"] += 1
                log.warning("catalogue upsert failed: %s", exc)
        return kept

    def run_once(self, closing_within_s: int = 30 * 3600) -> list[BookSnapshot]:
        captured = self.poll(self.universe(closing_within_s))
        self.flush()
        return captured

    def run_forever(
        self,
        closing_within_s: int = 30 * 3600,
        tick_s: float = 60.0,
        max_ticks: int | None = None,
        sleep: Callable[[float], None] | None = None,
        refresh_every: int = 30,
    ) -> RunStats:
        """Poll on a fixed tick. The cadence decides which markets are due on each one.

        The buffer is flushed every tick rather than only when full, so an abrupt kill
        loses at most one tick of observations rather than up to `buffer_size`.
        """
        import time

        sleep = sleep or time.sleep
        markets: list[Market] = []
        tick = 0
        while max_ticks is None or tick < max_ticks:
            # Nothing inside a tick may end the run. An unattended collector that dies
            # on a transient block leaves a permanent hole in the dataset, and the whole
            # point of the exercise is that missing data is not recoverable later.
            if tick % refresh_every == 0:
                try:
                    markets = self.universe(closing_within_s)
                except Exception as exc:  # noqa: BLE001
                    self.stats.failures[f"universe:{type(exc).__name__}"] += 1
                    log.warning(
                        "universe refresh failed, keeping %d markets: %s", len(markets), exc
                    )
            try:
                self.poll(markets)
            except Exception as exc:  # noqa: BLE001
                self.stats.failures[f"sweep:{type(exc).__name__}"] += 1
                log.exception("sweep failed, continuing: %s", exc)
            finally:
                self.flush()
            tick += 1
            if max_ticks is None or tick < max_ticks:
                sleep(tick_s)
        return self.stats
