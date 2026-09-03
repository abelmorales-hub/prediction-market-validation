"""Postgres access for the small relational state: catalogue, predictions, outcomes,
features. Book snapshots never come here — see parquet_store.

There is no update or delete method for predictions anywhere in this module, and the
database refuses both regardless (migration 0002). The Python side is the convenience;
the database is the guarantee.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict

from src.ingest.base import Market, Resolution

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


class Prediction(BaseModel):
    """Written once, before resolution, and never touched again (P3)."""

    model_config = ConfigDict(frozen=True)

    market_id: str
    ts: datetime
    horizon_label: str
    p_hat: float
    model_version: str
    git_sha: str
    features_hash: str
    market_mid: float
    market_bid: float
    market_ask: float


def connect(dsn: str) -> psycopg.Connection[Any]:
    return psycopg.connect(dsn, row_factory=dict_row)


def apply_migrations(conn: psycopg.Connection[Any], directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every .sql file in name order, once. Returns the names newly applied."""
    with conn.cursor() as cur:
        cur.execute(
            "create table if not exists schema_migrations ("
            " name text primary key, applied_at timestamptz not null default now())"
        )
        cur.execute("select name from schema_migrations")
        done = {r["name"] for r in cur.fetchall()}

        applied = []
        for path in sorted(directory.glob("*.sql")):
            if path.name in done:
                continue
            cur.execute(path.read_text(encoding="utf-8"))
            cur.execute("insert into schema_migrations (name) values (%s)", (path.name,))
            applied.append(path.name)
    conn.commit()
    return applied


class Repo:
    def __init__(self, conn: psycopg.Connection[Any], venue: str = "kalshi") -> None:
        self.conn = conn
        self.venue = venue

    # -- catalogue --------------------------------------------------------

    def upsert_markets(self, markets: Sequence[Market]) -> int:
        if not markets:
            return 0
        rows = [
            (
                m.market_id,
                self.venue,
                m.market_id,
                m.question,
                m.category,
                m.event_family,
                m.series_id,
                m.open_time,
                m.close_time,
                None,
            )
            for m in markets
        ]
        with self.conn.cursor() as cur:
            cur.executemany(
                """
                insert into markets (market_id, venue, slug, question, category,
                                     event_family, series_id, created_at, end_date,
                                     resolution_source)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (market_id) do update set
                    question = excluded.question,
                    category = excluded.category,
                    event_family = excluded.event_family,
                    end_date = excluded.end_date
                """,
                rows,
            )
        self.conn.commit()
        return len(rows)

    # -- predictions (insert only) ---------------------------------------

    def insert_prediction(self, p: Prediction) -> int:
        """The only write path. The pre-resolution trigger may reject this, which is the
        intended behaviour, not an error to swallow."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into predictions (market_id, ts, horizon_label, p_hat,
                                         model_version, git_sha, features_hash,
                                         market_mid, market_bid, market_ask)
                values (%(market_id)s, %(ts)s, %(horizon_label)s, %(p_hat)s,
                        %(model_version)s, %(git_sha)s, %(features_hash)s,
                        %(market_mid)s, %(market_bid)s, %(market_ask)s)
                returning id
                """,
                p.model_dump(),
            )
            row = cur.fetchone()
        self.conn.commit()
        assert row is not None
        return int(row["id"])

    # -- outcomes ---------------------------------------------------------

    def upsert_outcome(
        self, r: Resolution, was_disputed: bool | None = None, notes: str | None = None
    ) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into outcomes (market_id, resolved_at, outcome, was_disputed, notes)
                values (%s, %s, %s, %s, %s)
                on conflict (market_id) do update set
                    resolved_at = excluded.resolved_at,
                    outcome = excluded.outcome,
                    was_disputed = excluded.was_disputed,
                    notes = excluded.notes
                """,
                (r.market_id, r.resolved_at, r.outcome, was_disputed, notes),
            )
        self.conn.commit()

    # -- weather ----------------------------------------------------------

    def insert_ensemble(self, payload: dict[str, Any]) -> bool:
        """Record one point-in-time ensemble. False if this exact fetch is already
        stored, which happens when a run is retried, not when a forecast is updated:
        a later fetch has a later `fetched_at` and is a new row by design."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into gefs_ensemble (station, target_date, fetched_at, model,
                                           is_pure_forecast, members, tmax_f, tmin_f, grid)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (station, target_date, fetched_at) do nothing
                """,
                (
                    payload["station"],
                    payload["target_date"],
                    payload["fetched_at"],
                    payload["model"],
                    payload["is_pure_forecast"],
                    payload["members"],
                    payload["tmax_f"],
                    payload["tmin_f"],
                    json.dumps(payload["grid"]),
                ),
            )
            written = cur.rowcount == 1
        self.conn.commit()
        return written

    def latest_ensemble_before(
        self, station: str, target_date: date, before: datetime
    ) -> dict[str, Any] | None:
        """The most recent forecast that existed at `before`.

        The whole point-in-time guarantee lives in this WHERE clause: a forecast issued
        after the prediction it feeds is a look-ahead leak, and it would still return a
        plausible number.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select * from gefs_ensemble
                where station = %s and target_date = %s and fetched_at < %s
                order by fetched_at desc limit 1
                """,
                (station, target_date, before),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    # -- features ---------------------------------------------------------

    def insert_features(
        self,
        market_id: str,
        ts: datetime,
        feature_set: str,
        pit_guarantee: str,
        payload: dict[str, Any],
    ) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into features_pit (market_id, ts, feature_set, pit_guarantee, payload)
                values (%s, %s, %s, %s, %s)
                on conflict (market_id, ts, feature_set) do nothing
                """,
                (market_id, ts, feature_set, pit_guarantee, json.dumps(payload)),
            )
        self.conn.commit()

    # -- reads ------------------------------------------------------------

    def market_meta(self, market_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """category and end_date for the given markets. Missing ids are simply absent,
        which is how the watcher detects what still needs cataloguing."""
        if not market_ids:
            return {}
        with self.conn.cursor() as cur:
            cur.execute(
                "select market_id, category, end_date, event_family "
                "from markets where market_id = any(%s)",
                (list(market_ids),),
            )
            return {r["market_id"]: dict(r) for r in cur.fetchall()}

    def unresolved_market_ids(self, before: datetime) -> list[str]:
        """Catalogued markets whose end_date has passed and that have no outcome yet.

        This is the watcher's work list when it is driven by the catalogue rather than by
        a venue-wide scan of what settled.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                "select m.market_id from markets m "
                "left join outcomes o using (market_id) "
                "where m.end_date < %s and o.market_id is null "
                "order by m.end_date",
                (before,),
            )
            return [r["market_id"] for r in cur.fetchall()]

    def outcome_ids(self, since: datetime, until: datetime) -> set[str]:
        """Markets we have an outcome for, by resolution time."""
        with self.conn.cursor() as cur:
            cur.execute(
                "select market_id from outcomes where resolved_at >= %s and resolved_at < %s",
                (since, until),
            )
            return {r["market_id"] for r in cur.fetchall()}

    def scored_pairs(self, category: str | None = None) -> list[dict[str, Any]]:
        """Resolved markets joined to their predictions: the scorer's input.

        Emits `market_mid` as recorded on the prediction row, never a price looked up
        now — the whole benchmark depends on the two being the same instant (G2).
        """
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select p.market_id, p.ts, p.horizon_label, p.p_hat, p.market_mid,
                       p.market_bid, p.market_ask, p.model_version,
                       o.outcome, o.was_disputed,
                       m.category, m.event_family
                from predictions p
                join outcomes o on o.market_id = p.market_id
                join markets  m on m.market_id = p.market_id
                where (%s::text is null or m.category = %s)
                order by p.ts
                """,
                (category, category),
            )
            return list(cur.fetchall())
