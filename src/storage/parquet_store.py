"""Date-partitioned Parquet for book snapshots.

These do not go to Supabase. At one snapshot per minute per market the volume would
exhaust the tier in weeks, and none of it is relational state — it is an append-only
time series that DuckDB reads directly off disk.

Layout: data/snapshots/date=YYYY-MM-DD/part-<n>.parquet
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict

from src.ingest.base import Book

SCHEMA = pa.schema(
    [
        ("market_id", pa.string()),
        ("ts", pa.timestamp("us", tz="UTC")),
        # Not in the milestone's row list. Added because the gap report cannot otherwise
        # tell a missed sample inside a dense window from an ordinary 30-minute sparse
        # interval, which is the one thing it exists to detect. It also makes each row
        # self-describing about its horizon.
        ("close_time", pa.timestamp("us", tz="UTC")),
        ("best_bid", pa.float64()),
        ("best_ask", pa.float64()),
        ("mid", pa.float64()),
        ("spread", pa.float64()),
        ("bid_depth_1c", pa.float64()),
        ("ask_depth_1c", pa.float64()),
        ("book_hash", pa.string()),
    ]
)


class BookSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_id: str
    ts: datetime
    close_time: datetime
    best_bid: float | None = None
    best_ask: float | None = None
    mid: float | None = None
    spread: float | None = None
    bid_depth_1c: float = 0.0
    ask_depth_1c: float = 0.0
    book_hash: str

    @classmethod
    def from_book(
        cls,
        book: Book,
        close_time: datetime,
        observed_at: datetime | None = None,
        depth_delta: float = 0.01,
    ) -> BookSnapshot:
        """`observed_at` overrides the book's own stamp so the scheduler records against
        the same clock it schedules against. Mixing the two makes due-ness incoherent."""
        bid_depth, ask_depth = book.depth_within(depth_delta)
        return cls(
            market_id=book.market_id,
            ts=observed_at or book.ts,
            close_time=close_time,
            best_bid=book.best_bid,
            best_ask=book.best_ask,
            mid=book.mid,
            spread=book.spread,
            bid_depth_1c=bid_depth,
            ask_depth_1c=ask_depth,
            book_hash=book_hash(book),
        )


def book_hash(book: Book) -> str:
    """Content hash of the full ladder, deliberately excluding the timestamp.

    The scheduler dedupes on this, so a hash that folded in `ts` would differ on every
    poll and dedupe nothing — an unchanged book must hash the same an hour later.
    """
    parts = [
        f"{side}:{lv.price:.6f}x{lv.size:.6f}"
        for side, levels in (("b", book.yes_bids), ("a", book.yes_asks))
        for lv in levels
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def _partition(root: Path, ts: datetime) -> Path:
    return root / f"date={ts.astimezone(UTC).date().isoformat()}"


def write_snapshots(rows: Sequence[BookSnapshot], root: Path) -> list[Path]:
    """Append one part file per UTC date touched. Never rewrites an existing part, so a
    crashed run leaves whole files behind rather than a truncated one."""
    if not rows:
        return []
    by_date: dict[Path, list[BookSnapshot]] = defaultdict(list)
    for row in rows:
        by_date[_partition(root, row.ts)].append(row)

    written = []
    for directory, chunk in by_date.items():
        directory.mkdir(parents=True, exist_ok=True)
        part = directory / f"part-{len(list(directory.glob('part-*.parquet'))):05d}.parquet"
        table = pa.Table.from_pylist(
            [r.model_dump() for r in chunk],
            schema=SCHEMA,
        )
        pq.write_table(table, part, compression="zstd")
        written.append(part)
    return written


def read_snapshots(root: Path, market_id: str | None = None) -> list[BookSnapshot]:
    """Read every partition back. Convenience for tests and small analyses; anything
    larger should query through the DuckDB views instead of materialising in Python."""
    files = sorted(root.glob("date=*/part-*.parquet"))
    if not files:
        return []
    table = pq.read_table(files, schema=SCHEMA)
    rows: Iterable[dict[str, object]] = table.to_pylist()
    out = [BookSnapshot.model_validate(r) for r in rows]
    return [r for r in out if market_id is None or r.market_id == market_id]
