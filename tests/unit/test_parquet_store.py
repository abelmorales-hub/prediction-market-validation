"""Parquet round-trip and the book hash the scheduler dedupes on. No network, no DB."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.ingest.kalshi import build_book
from src.storage.duckdb_views import connect, gaps
from src.storage.parquet_store import (
    BookSnapshot,
    book_hash,
    read_snapshots,
    write_snapshots,
)

TS = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _snap(market_id: str = "A-1", ts: datetime = TS, **kw: object) -> BookSnapshot:
    book = build_book(market_id, [["0.60", "10"], ["0.62", "4"]], [["0.35", "7"]], ts=ts)
    return BookSnapshot.from_book(book).model_copy(update=kw)


def test_round_trip_preserves_every_field(tmp_path: Path) -> None:
    original = _snap()
    write_snapshots([original], tmp_path)
    (restored,) = read_snapshots(tmp_path)
    assert restored == original


def test_round_trip_preserves_none_rather_than_zero(tmp_path: Path) -> None:
    """An empty book has no mid. Zero is a real price and must not be invented."""
    empty = BookSnapshot.from_book(build_book("A-1", [], [], ts=TS))
    write_snapshots([empty], tmp_path)
    (restored,) = read_snapshots(tmp_path)
    assert restored.mid is None and restored.best_bid is None and restored.best_ask is None
    assert restored.bid_depth_1c == 0.0


def test_snapshot_derives_the_fields_the_scheduler_records(tmp_path: Path) -> None:
    snap = _snap()
    assert snap.best_bid == 0.62
    assert round(snap.best_ask, 4) == 0.65
    assert round(snap.mid, 4) == 0.635
    assert round(snap.spread, 4) == 0.03
    assert snap.bid_depth_1c == 4.0, "only 0.62 sits within a cent of the touch"
    assert snap.ask_depth_1c == 7.0


def test_partitions_by_utc_date(tmp_path: Path) -> None:
    rows = [_snap(ts=TS), _snap(ts=TS + timedelta(days=1)), _snap(ts=TS + timedelta(hours=1))]
    write_snapshots(rows, tmp_path)
    dirs = sorted(p.name for p in tmp_path.glob("date=*"))
    assert dirs == ["date=2026-09-01", "date=2026-09-02"]


def test_repeated_writes_add_parts_and_never_overwrite(tmp_path: Path) -> None:
    write_snapshots([_snap()], tmp_path)
    write_snapshots([_snap(market_id="B-2")], tmp_path)
    parts = sorted(p.name for p in (tmp_path / "date=2026-09-01").glob("part-*.parquet"))
    assert parts == ["part-00000.parquet", "part-00001.parquet"]
    assert len(read_snapshots(tmp_path)) == 2


def test_writing_nothing_creates_nothing(tmp_path: Path) -> None:
    assert write_snapshots([], tmp_path) == []
    assert list(tmp_path.iterdir()) == []


def test_book_hash_ignores_the_timestamp(tmp_path: Path) -> None:
    """Dedup is the whole purpose: an unchanged book must hash the same an hour later."""
    same = build_book("A-1", [["0.60", "10"]], [["0.35", "7"]], ts=TS)
    later = build_book("A-1", [["0.60", "10"]], [["0.35", "7"]], ts=TS + timedelta(hours=1))
    assert book_hash(same) == book_hash(later)


def test_book_hash_changes_with_size_not_only_price() -> None:
    """A size-only change is a real book change; hashing prices alone would drop it."""
    base = build_book("A-1", [["0.60", "10"]], [["0.35", "7"]], ts=TS)
    resized = build_book("A-1", [["0.60", "11"]], [["0.35", "7"]], ts=TS)
    repriced = build_book("A-1", [["0.61", "10"]], [["0.35", "7"]], ts=TS)
    assert book_hash(base) != book_hash(resized)
    assert book_hash(base) != book_hash(repriced)


def test_duckdb_view_reads_the_partitions(tmp_path: Path) -> None:
    write_snapshots([_snap(), _snap(ts=TS + timedelta(days=1))], tmp_path)
    con = connect(tmp_path)
    rows = con.execute("select market_id, date from book_snapshots order by ts").fetchall()
    assert len(rows) == 2
    assert {str(r[1]) for r in rows} == {"2026-09-01", "2026-09-02"}


def test_duckdb_view_is_queryable_when_empty(tmp_path: Path) -> None:
    """A fresh checkout has no snapshots; queries must return nothing, not blow up."""
    con = connect(tmp_path)
    assert con.execute("select count(*) from book_snapshots").fetchone()[0] == 0


def test_gap_report_finds_only_gaps_over_the_threshold(tmp_path: Path) -> None:
    rows = [
        _snap(ts=TS),
        _snap(ts=TS + timedelta(minutes=1)),  # fine
        _snap(ts=TS + timedelta(minutes=45)),  # 44 min hole
    ]
    write_snapshots(rows, tmp_path)
    found = gaps(connect(tmp_path), max_gap_minutes=15)
    assert len(found) == 1
    assert found[0][0] == "A-1"
    assert round(found[0][3]) == 44


def test_gaps_are_not_reported_across_different_markets(tmp_path: Path) -> None:
    """Two markets an hour apart is not a gap in either of them."""
    write_snapshots([_snap("A-1", ts=TS), _snap("B-2", ts=TS + timedelta(hours=1))], tmp_path)
    assert gaps(connect(tmp_path), max_gap_minutes=15) == []
