"""Postgres access for the small relational state: catalogue, predictions, outcomes,
features. Book snapshots never come here — see parquet_store.

There is no update or delete method for predictions anywhere in this module, and the
database refuses both regardless (migration 0002). The Python side is the convenience;
the database is the guarantee.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict
from tenacity import (
    Retrying,
    before_sleep_log,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from src.ingest.base import Market, Resolution

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

log = logging.getLogger(__name__)

# Waking from S3 brings the link up before the resolver answers. Twice now the 05:30 tick
# has fired ~25 s after resume and died on `getaddrinfo failed` for the pooler, taking the
# band's first sweep with it (2026-09-13, 2026-10-01 -- the latter logged DNS-Client 1014,
# "no configured DNS server responded", while the link itself was up and identified).
#
# The budget is deliberately ~1 minute, not the 8-attempt ladder of src/ingest/http.py.
# Every collector is re-launched by its own scheduled task every 4-15 min, so a resolver
# still dead after a minute is the next tick's problem; holding this process open longer
# only delays the log line that says why it failed. The 2026-10-01 outage lasted ~2h50m
# and no startup budget would have saved it -- what this buys is the resume race, where
# the difference is a dead first tick and a slow one.
CONNECT_ATTEMPTS = 6
CONNECT_INITIAL_S = 2.0
CONNECT_MAX_S = 30.0


def _is_transient(exc: BaseException) -> bool:
    """True only for faults that never reached Postgres.

    `sqlstate` is set exactly when the server answered, so a bad password or a missing
    database fails on the first attempt instead of backing off for a minute and burying
    the real cause under retry noise.
    """
    return isinstance(exc, psycopg.OperationalError) and exc.sqlstate is None


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
    """Open the connection, waiting out a resolver that is not up yet. See
    CONNECT_ATTEMPTS for why the budget is short."""
    return Retrying(
        retry=retry_if_exception(_is_transient),
        stop=stop_after_attempt(CONNECT_ATTEMPTS),
        wait=wait_exponential_jitter(initial=CONNECT_INITIAL_S, max=CONNECT_MAX_S),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )(psycopg.connect, dsn, row_factory=dict_row)


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
                m.strike_type,
                m.floor_strike,
                m.cap_strike,
                m.settles_on,
                m.settlement_station,
            )
            for m in markets
        ]
        with self.conn.cursor() as cur:
            cur.executemany(
                """
                insert into markets (market_id, venue, slug, question, category,
                                     event_family, series_id, created_at, end_date,
                                     resolution_source, strike_type, floor_strike,
                                     cap_strike, settles_on, settlement_station)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (market_id) do update set
                    question = excluded.question,
                    category = excluded.category,
                    event_family = excluded.event_family,
                    end_date = excluded.end_date,
                    -- Refreshed, not left alone: every row catalogued before migration
                    -- 0004 has NULL strikes, and the next snapshot tick is the only
                    -- chance to fill them while the market is still open.
                    strike_type = excluded.strike_type,
                    floor_strike = excluded.floor_strike,
                    cap_strike = excluded.cap_strike,
                    settles_on = excluded.settles_on,
                    settlement_station = excluded.settlement_station
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
        """Catalogue metadata for the given markets. Missing ids are simply absent,
        which is how the watcher detects what still needs cataloguing.

        The strike columns come along because a forecaster needs the market's actual
        question — a bound on a number — and re-reading them per market at emit time
        would put a query inside the one job that cannot be re-run later.
        """
        if not market_ids:
            return {}
        with self.conn.cursor() as cur:
            cur.execute(
                "select market_id, category, end_date, event_family, series_id, "
                "strike_type, floor_strike, cap_strike, settles_on, settlement_station "
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

    def series_in_category(self, category: str) -> list[str]:
        """Series the catalogue has already seen in a category — the collector's work list.

        The venue-wide open-market sweep cannot be trusted to reach anything (see
        `KalshiSource.list_open_markets`), so admission is driven per series instead, and
        this is where the series come from. It means the universe is what the catalogue
        already knows: a series never catalogued is never collected. That is a real
        limitation and the right trade, because a universe that quietly grows or empties
        is worse than one that is fixed and stated.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                "select distinct series_id from markets where category = %s order by series_id",
                (category,),
            )
            return [r["series_id"] for r in cur.fetchall()]

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
                       m.category, m.event_family, m.end_date
                from predictions p
                join outcomes o on o.market_id = p.market_id
                join markets  m on m.market_id = p.market_id
                where (%s::text is null or m.category = %s)
                order by p.ts
                """,
                (category, category),
            )
            return list(cur.fetchall())
