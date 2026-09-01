"""DuckDB views over the snapshot Parquet.

Read-only analytics. The partition directory is exposed as a real `date` column via hive
partitioning, so a query for one day scans one directory instead of the whole history.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

SNAPSHOTS_VIEW = "book_snapshots"


def connect(root: Path, database: str = ":memory:") -> duckdb.DuckDBPyConnection:
    """Connection with the snapshot view registered. Safe on an empty tree: the view is
    created against the schema, so queries return no rows rather than failing."""
    con = duckdb.connect(database)
    register_views(con, root)
    return con


def register_views(con: duckdb.DuckDBPyConnection, root: Path) -> None:
    glob = (root / "date=*" / "*.parquet").as_posix()
    if not any(root.glob("date=*/*.parquet")):
        con.execute(
            f"""
            create or replace view {SNAPSHOTS_VIEW} as
            select
                null::varchar   as market_id,
                null::timestamp as ts,
                null::double    as best_bid,
                null::double    as best_ask,
                null::double    as mid,
                null::double    as spread,
                null::double    as bid_depth_1c,
                null::double    as ask_depth_1c,
                null::varchar   as book_hash,
                null::date      as date
            where false
            """
        )
        return
    con.execute(
        f"""
        create or replace view {SNAPSHOTS_VIEW} as
        select * from read_parquet('{glob}', hive_partitioning = true)
        """
    )


def gaps(
    con: duckdb.DuckDBPyConnection, max_gap_minutes: int = 15
) -> list[tuple[str, str, str, float]]:
    """Consecutive snapshots for one market further apart than `max_gap_minutes`.

    Backs scripts/gap_report.py in M3. Reported, never silently skipped: a gap is missing
    evidence, and evidence that quietly vanishes is how a dataset flatters a model.
    """
    rows = con.execute(
        f"""
        with ordered as (
            select market_id, ts,
                   lag(ts) over (partition by market_id order by ts) as prev_ts
            from {SNAPSHOTS_VIEW}
        )
        select market_id,
               cast(prev_ts as varchar),
               cast(ts as varchar),
               date_diff('second', prev_ts, ts) / 60.0 as gap_minutes
        from ordered
        where prev_ts is not null
          and date_diff('second', prev_ts, ts) > {max_gap_minutes * 60}
        order by gap_minutes desc
        """
    ).fetchall()
    return [(str(a), str(b), str(c), float(d)) for a, b, c, d in rows]
