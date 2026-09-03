"""Runner: due-ness, crash resumption, and failures that get counted rather than lost."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.ingest.base import Book, Market, PricePoint, Resolution
from src.ingest.kalshi import build_book
from src.scheduler.runner import SnapshotRunner
from src.storage.parquet_store import read_snapshots

CLOSE = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


class FakeSource:
    """In-memory MarketDataSource. Books can be made to change or to fail on demand."""

    def __init__(self, markets: list[Market]) -> None:
        self.markets = markets
        self.calls: list[str] = []
        self.fail_for: set[str] = set()
        self.bid = 0.60

    def list_open_markets(
        self, *, closing_within_s: int | None = None, include_combos: bool = False
    ) -> list[Market]:
        return self.markets

    def get_market(self, market_id: str) -> Market:
        return next(m for m in self.markets if m.market_id == market_id)

    def get_book(self, market_id: str, *, depth: int = 100) -> Book:
        self.calls.append(market_id)
        if market_id in self.fail_for:
            raise ConnectionError("simulated venue failure")
        return build_book(market_id, [[f"{self.bid:.2f}", "10"]], [["0.35", "7"]])

    def get_price_history(
        self, market_id: str, start: datetime, end: datetime, *, interval_min: int = 1
    ) -> list[PricePoint]:
        return []

    def list_recently_resolved(self, since: datetime) -> list[Resolution]:
        return []


def _market(market_id: str = "A-1", close: datetime = CLOSE, volume: float = 5000.0) -> Market:
    return Market(
        market_id=market_id,
        event_family="A",
        series_id="A",
        question="q",
        status="active",
        close_time=close,
        volume=volume,
    )


def test_first_poll_of_an_unseen_market_is_always_due(tmp_path: Path) -> None:
    src = FakeSource([_market()])
    runner = SnapshotRunner(src, tmp_path)
    assert len(runner.poll(src.markets, now=CLOSE - timedelta(hours=48))) == 1


def test_second_poll_inside_the_interval_is_skipped(tmp_path: Path) -> None:
    """Sparse region: 30-minute cadence, so a poll a minute later is not due."""
    src = FakeSource([_market()])
    runner = SnapshotRunner(src, tmp_path)
    now = CLOSE - timedelta(hours=48)
    runner.poll(src.markets, now=now)
    assert runner.poll(src.markets, now=now + timedelta(minutes=1)) == []
    assert runner.stats.skipped_not_due == 1


def test_dense_window_polls_every_minute(tmp_path: Path) -> None:
    src = FakeSource([_market()])
    runner = SnapshotRunner(src, tmp_path)
    now = CLOSE - timedelta(hours=24)
    runner.poll(src.markets, now=now)
    src.bid += 0.01
    assert len(runner.poll(src.markets, now=now + timedelta(minutes=1))) == 1


def test_closed_markets_are_never_polled(tmp_path: Path) -> None:
    src = FakeSource([_market()])
    runner = SnapshotRunner(src, tmp_path)
    assert runner.poll(src.markets, now=CLOSE) == []
    assert runner.poll(src.markets, now=CLOSE + timedelta(hours=1)) == []
    assert src.calls == [], "a closed market must not even be fetched"


def test_a_venue_failure_is_counted_not_raised(tmp_path: Path) -> None:
    """A miss is data. Crashing the sweep would lose every other market on it."""
    src = FakeSource([_market("A-1"), _market("B-2")])
    src.fail_for = {"A-1"}
    runner = SnapshotRunner(src, tmp_path)
    captured = runner.poll(src.markets, now=CLOSE - timedelta(hours=48))
    assert [s.market_id for s in captured] == ["B-2"]
    assert runner.stats.failed == 1
    assert runner.stats.failures["ConnectionError"] == 1


def test_failed_market_stays_due_and_is_retried(tmp_path: Path) -> None:
    src = FakeSource([_market()])
    src.fail_for = {"A-1"}
    runner = SnapshotRunner(src, tmp_path)
    now = CLOSE - timedelta(hours=48)
    runner.poll(src.markets, now=now)
    src.fail_for.clear()
    assert len(runner.poll(src.markets, now=now + timedelta(seconds=1))) == 1


# -- resumption -----------------------------------------------------------


def test_resumes_after_an_abrupt_kill(tmp_path: Path) -> None:
    """Simulates kill -9: build a runner, flush, drop it entirely, build a new one over
    the same directory. The new one must know what the old one already recorded."""
    src = FakeSource([_market()])
    first = SnapshotRunner(src, tmp_path)
    now = CLOSE - timedelta(hours=48)
    first.poll(src.markets, now=now)
    first.flush()
    del first  # no graceful shutdown, no state handed over

    revived = SnapshotRunner(FakeSource([_market()]), tmp_path)
    assert revived._last["A-1"] is not None, "state must come back from disk"
    assert revived.poll(src.markets, now=now + timedelta(minutes=1)) == []
    assert revived.stats.skipped_not_due == 1


def test_unflushed_buffer_is_the_only_loss_on_a_kill(tmp_path: Path) -> None:
    src = FakeSource([_market()])
    runner = SnapshotRunner(src, tmp_path, buffer_size=1000)
    runner.poll(src.markets, now=CLOSE - timedelta(hours=48))
    del runner  # killed before flushing

    assert read_snapshots(tmp_path) == [], "unflushed work is genuinely lost"
    revived = SnapshotRunner(FakeSource([_market()]), tmp_path)
    assert len(revived.poll(src.markets, now=CLOSE - timedelta(hours=48))) == 1, "and re-polled"


def test_resumption_is_idempotent_over_repeated_restarts(tmp_path: Path) -> None:
    now = CLOSE - timedelta(hours=48)
    for _ in range(5):
        runner = SnapshotRunner(FakeSource([_market()]), tmp_path)
        runner.poll([_market()], now=now)
        runner.flush()
    assert len(read_snapshots(tmp_path)) == 1, "five restarts, one observation"


def test_polling_twice_at_the_same_instant_writes_one_row(tmp_path: Path) -> None:
    """The last-timestamp check makes a replay a no-op without needing a hash compare."""
    src = FakeSource([_market()])
    runner = SnapshotRunner(src, tmp_path)
    now = CLOSE - timedelta(hours=48)
    runner.poll(src.markets, now=now)
    runner.poll(src.markets, now=now)
    assert len(runner._buffer) == 1


def test_unchanged_book_still_records_a_row(tmp_path: Path) -> None:
    """Rows are never suppressed on an unchanged hash. A missing row must mean 'we did
    not observe', or the gap report cannot tell a still market from a stopped runner."""
    src = FakeSource([_market()])
    runner = SnapshotRunner(src, tmp_path)
    now = CLOSE - timedelta(hours=24)  # dense: due every minute
    first = runner.poll(src.markets, now=now)
    second = runner.poll(src.markets, now=now + timedelta(minutes=1))
    assert len(first) == len(second) == 1
    assert first[0].book_hash == second[0].book_hash, "book genuinely unchanged"
    assert first[0].ts != second[0].ts, "but both observations are recorded"


# -- flushing -------------------------------------------------------------


def test_buffer_flushes_mid_sweep_not_only_at_the_end(tmp_path: Path) -> None:
    """A sweep over a large universe takes minutes. Flushing only after the loop means
    an interruption loses every market on it — which is exactly what happened live."""
    markets = [_market(f"M-{i}") for i in range(5)]
    runner = SnapshotRunner(FakeSource(markets), tmp_path, buffer_size=3)
    runner.poll(markets, now=CLOSE - timedelta(hours=48))
    assert runner.stats.written == 3, "flushed as soon as the buffer filled"
    assert len(read_snapshots(tmp_path)) == 3, "already durable mid-sweep"
    runner.flush()
    assert len(read_snapshots(tmp_path)) == 5


def test_tick_absorbs_the_sweeps_own_duration(tmp_path: Path) -> None:
    """Regression, first overnight run: sleeping a fixed `tick_s` *after* the sweep makes
    every period tick_s + sweep_duration. A 60 s tick with a 10 s sweep sampled every
    70 s, so each dense sample landed progressively later than the horizon it brackets."""
    runner = SnapshotRunner(FakeSource([_market()]), tmp_path)
    # Loop top, then 10 s later when the sleep is computed; next tick starts at +60.
    clock = iter([0.0, 10.0, 60.0, 70.0, 120.0])
    slept: list[float] = []
    runner.run_forever(tick_s=60, max_ticks=3, sleep=slept.append, clock=lambda: next(clock))
    assert slept == [50.0, 50.0], "sleep must be shortened by the time the sweep took"


def test_a_sweep_that_overruns_its_tick_never_sleeps_negative(tmp_path: Path) -> None:
    """Falling behind is for the gap report to surface, not for the grid to absorb."""
    runner = SnapshotRunner(FakeSource([_market()]), tmp_path)
    clock = iter([0.0, 95.0, 95.0])
    slept: list[float] = []
    runner.run_forever(tick_s=60, max_ticks=2, sleep=slept.append, clock=lambda: next(clock))
    assert slept == [0.0]


def test_a_sparse_step_does_not_step_over_a_dense_window(tmp_path: Path) -> None:
    """`next_snapshot_at` holds the clamp, but the runner compared intervals directly and
    never called it — a fully-tested function that production never reached."""
    src = FakeSource([_market()])
    runner = SnapshotRunner(src, tmp_path)
    opening = CLOSE - timedelta(hours=24, minutes=15)  # the T-24h window opens here
    runner.poll(src.markets, now=opening - timedelta(minutes=29))
    assert runner.poll(src.markets, now=opening) != [], "must sample at the window opening"


def test_run_forever_stops_after_max_ticks_and_flushes_each_one(tmp_path: Path) -> None:
    src = FakeSource([_market(volume=5000)])
    runner = SnapshotRunner(src, tmp_path)
    slept: list[float] = []
    stats = runner.run_forever(tick_s=60, max_ticks=3, sleep=slept.append)
    assert stats.polled >= 1
    assert len(slept) == 2, "no sleep after the final tick"
    assert read_snapshots(tmp_path), "each tick flushes, so a kill costs one tick"


# -- universe -------------------------------------------------------------


def test_universe_applies_the_liquidity_floor(tmp_path: Path) -> None:
    markets = [_market("thin", volume=10), _market("liquid", volume=50_000)]
    runner = SnapshotRunner(FakeSource(markets), tmp_path, min_volume=1000)
    assert [m.market_id for m in runner.universe(30 * 3600)] == ["liquid"]


def test_universe_is_capped_and_keeps_the_most_liquid(tmp_path: Path) -> None:
    markets = [_market(f"M-{i}", volume=1000 + i) for i in range(10)]
    runner = SnapshotRunner(FakeSource(markets), tmp_path, min_volume=0, max_markets=3)
    kept = runner.universe(30 * 3600)
    assert [m.market_id for m in kept] == ["M-9", "M-8", "M-7"]


def test_dense_window_markets_are_swept_first(tmp_path: Path) -> None:
    """If a sweep runs past its tick, the markets that got sampled must be the ones on
    a prediction horizon rather than whichever sorted first."""
    far = _market("far", close=CLOSE)
    near = _market("near", close=CLOSE + timedelta(hours=24))  # in its T-24h window now
    ordered = SnapshotRunner._by_urgency([far, near], now=CLOSE)
    assert [m.market_id for m in ordered] == ["near", "far"]


def test_a_failed_universe_refresh_does_not_end_the_run(tmp_path: Path) -> None:
    """An unattended collector must not die on a transient block: a hole in the data is
    not recoverable later, which is the entire premise of forward-testing."""

    class Flaky(FakeSource):
        def list_open_markets(self, **kw: object) -> list[Market]:
            raise ConnectionError("reset")

    runner = SnapshotRunner(Flaky([_market()]), tmp_path)
    stats = runner.run_forever(tick_s=1, max_ticks=3, refresh_every=1, sleep=lambda _: None)
    assert stats.failures["universe:ConnectionError"] == 3, "all three ticks tried and failed"
    assert stats.failed == 0, "no market was reachable to fail on"


def test_a_sweep_failure_still_flushes_and_continues(tmp_path: Path) -> None:
    src = FakeSource([_market()])
    runner = SnapshotRunner(src, tmp_path)
    calls = {"n": 0}
    original = runner.poll

    def exploding(markets, now=None):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return original(markets, now)

    runner.poll = exploding  # type: ignore[method-assign]
    stats = runner.run_forever(tick_s=1, max_ticks=2, sleep=lambda _: None)
    assert stats.failures["sweep:RuntimeError"] == 1
    assert calls["n"] == 2, "the loop kept going after the failed sweep"
