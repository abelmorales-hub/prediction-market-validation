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
from src.scheduler.cadence import in_dense_window, next_snapshot_at
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
        series_tickers: Sequence[str] | None = None,
    ) -> None:
        self.source = source
        self.root = root
        self.buffer_size = buffer_size
        self.min_volume = min_volume
        self.max_markets = max_markets
        # None means the venue-wide sweep, which `list_open_markets` documents as
        # unreliable. Every scheduled job passes an explicit list.
        self.series_tickers = series_tickers
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
    def retain(
        previous: Sequence[Market], fresh: Sequence[Market], now: datetime
    ) -> list[Market]:
        """Keep tracking anything already admitted until it closes.

        `universe()` re-ranks every open market by volume and keeps the top N. Without
        this, a market that slips to rank N+1 is dropped the instant it does — including
        one minutes away from its T-24h horizon with a partial series already recorded.
        Observed 2026-09-03: KXWTI-26SEP0414-T97.49 was sampled at 19:39 and 20:09, fell
        out of the ranking, and returned at 22:07, having skipped the whole dense window
        around its 20:30 horizon.

        That failure is silent in the way that matters. The market keeps a plausible
        series, missing only the instant the scorer needs, so the prediction is never
        emitted and nothing anywhere reports an error.

        Admission stays a research decision — `min_volume` and `max_markets` still
        choose what enters. Retention is not: dropping a market mid-life discards work
        already done and cannot be undone later, so it is always wrong.

        Closed markets are dropped here rather than left to accumulate; the cadence
        would never schedule them again, but they would still be swept and sorted.
        """
        merged = {m.market_id: m for m in previous if m.close_time > now}
        # Fresh metadata wins on conflict: volume and the touch move, close_time does not.
        merged.update({m.market_id: m for m in fresh if m.close_time > now})
        return list(merged.values())

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
        """Due once `now` has reached the instant the cadence scheduled from `last`.

        This goes through `next_snapshot_at` rather than comparing against
        `interval_at` directly, because only the former clamps a sparse step to a dense
        window it would otherwise step over. Comparing intervals here looks equivalent
        and is not: a market 29 minutes from a window opening gets a 30-minute step,
        lands inside the window, and the horizon it exists to bracket is never sampled.
        """
        if now >= market.close_time:
            return False
        last = self._last.get(market.market_id)
        if last is None:
            return True
        due = next_snapshot_at(last, market.close_time)
        return due is not None and now >= due

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
        markets = self.source.list_open_markets(
            closing_within_s=closing_within_s, series_tickers=self.series_tickers
        )
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
        # An empty sweep is not a quiet Sunday, it is the failure mode that cost
        # 2026-09-07: combo shards filled every page of the venue-wide listing and the
        # filter returned nothing. `retain` then keeps the already-admitted markets alive
        # until they close, so capture looks healthy for a day while admission is dead.
        # Counting it makes the supervisor's status say so on the day it happens.
        if not markets:
            self.stats.failures["universe:empty"] += 1
            log.error(
                "universe swept 0 open markets -- admission is dead, and capture will "
                "drain as the retained markets close"
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
        clock: Callable[[], float] | None = None,
        deadline: datetime | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> RunStats:
        """Poll on a fixed tick. The cadence decides which markets are due on each one.

        The buffer is flushed every tick rather than only when full, so an abrupt kill
        loses at most one tick of observations rather than up to `buffer_size`.

        Each tick sleeps until the next grid instant rather than for a fixed duration.
        Sleeping `tick_s` *after* the sweep adds the sweep's own cost to every period, so
        a 60-second tick with a 10-second sweep samples every 70 seconds — under the gap
        report's tolerance individually, but it compounds across a dense window and every
        sample lands progressively later than the horizon it is supposed to bracket.

        `deadline` is an absolute instant where `max_ticks` is a count, and the difference
        is the whole point: a count is only a stopping *time* if every tick takes exactly
        `tick_s`. Sweeps overrun and hosts suspend, so the budget outlives the window it
        was sized for — which is how a 2026-09-20 suspend left the 05:30 snapshotter alive
        at 12:00, and how a shift on an ephemeral runner would walk into the next one.
        Pass both: the deadline ends the window, the count is the safety net under a clock
        that steps backwards.
        """
        import time

        sleep = sleep or time.sleep
        clock = clock or time.monotonic
        now = now or (lambda: datetime.now(UTC))
        markets: list[Market] = []
        tick = 0
        while (max_ticks is None or tick < max_ticks) and (deadline is None or now() < deadline):
            started = clock()
            # Nothing inside a tick may end the run. An unattended collector that dies
            # on a transient block leaves a permanent hole in the dataset, and the whole
            # point of the exercise is that missing data is not recoverable later.
            if tick % refresh_every == 0:
                try:
                    fresh = self.universe(closing_within_s)
                    markets = self.retain(markets, fresh, datetime.now(UTC))
                    fresh_ids = {m.market_id for m in fresh}
                    held = sum(1 for m in markets if m.market_id not in fresh_ids)
                    if held:
                        log.info(
                            "retained %d market(s) that fell out of the ranking "
                            "(tracking %d)",
                            held,
                            len(markets),
                        )
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
            if (max_ticks is None or tick < max_ticks) and (
                deadline is None or now() < deadline
            ):
                # A sweep that overran its tick gets no sleep at all rather than a
                # negative one, and the next tick starts immediately. Falling behind is
                # reported by the gap report, not hidden by silently stretching the grid.
                sleep(max(0.0, tick_s - (clock() - started)))
        return self.stats
