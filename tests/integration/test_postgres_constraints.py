"""The P3 proofs. These are the reason M2 exists.

Needs a Postgres. Set SUPABASE_DB_URL in .env; skipped otherwise. Every test runs against
a schema created fresh in a throwaway namespace, so it will not touch real data.
"""

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from src.config import get_settings
from src.ingest.base import Market, Resolution
from src.storage.supabase_repo import Prediction, Repo, apply_migrations, connect

pytestmark = pytest.mark.integration

CLOSE = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)


def _dsn() -> str:
    dsn = get_settings().supabase_db_url
    if not dsn:
        pytest.skip("SUPABASE_DB_URL not set")
    return dsn


@pytest.fixture
def conn() -> psycopg.Connection:
    """Fresh schema per test. Dropped afterwards whatever happens."""
    c = connect(_dsn())
    with c.cursor() as cur:
        cur.execute("drop schema if exists pmv_test cascade")
        cur.execute("create schema pmv_test")
        cur.execute("set search_path to pmv_test")
        # In production these tables live in `public`, where Supabase already grants the
        # three roles USAGE. Without mirroring that here the tables are simply invisible
        # to them, and the revoke test would pass because nothing was reachable rather
        # than because UPDATE was denied.
        cur.execute("grant usage on schema pmv_test to anon, authenticated, service_role")
    c.commit()
    try:
        yield c
    finally:
        c.rollback()
        with c.cursor() as cur:
            cur.execute("drop schema if exists pmv_test cascade")
        c.commit()
        c.close()


@pytest.fixture
def repo(conn: psycopg.Connection) -> Repo:
    applied = apply_migrations(conn)
    assert applied, "migrations should apply to a fresh schema"
    r = Repo(conn)
    r.upsert_markets([_market()])
    return r


def _market(market_id: str = "A-1") -> Market:
    return Market(
        market_id=market_id,
        event_family="A",
        series_id="A",
        question="does it?",
        category="Sports",
        status="active",
        close_time=CLOSE,
    )


def _prediction(ts: datetime, **kw: object) -> Prediction:
    base = dict(
        market_id="A-1",
        ts=ts,
        horizon_label="T-24h",
        p_hat=0.42,
        model_version="v0",
        git_sha="deadbeef",
        features_hash="cafe",
        market_mid=0.40,
        market_bid=0.39,
        market_ask=0.41,
    )
    return Prediction(**{**base, **kw})  # type: ignore[arg-type]


# -- migrations ----------------------------------------------------------


def test_migrations_apply_cleanly_and_are_idempotent(conn: psycopg.Connection) -> None:
    first = apply_migrations(conn)
    assert first == ["0001_schema.sql", "0002_append_only.sql"]
    assert apply_migrations(conn) == [], "re-running must be a no-op"

    with conn.cursor() as cur:
        cur.execute(
            "select table_name from information_schema.tables where table_schema = 'pmv_test'"
        )
        tables = {r["table_name"] for r in cur.fetchall()}
    assert {"markets", "predictions", "outcomes", "features_pit"} <= tables


# -- P3: append-only ------------------------------------------------------


def test_update_on_predictions_is_rejected_for_the_supabase_roles(repo: Repo) -> None:
    """The revoke. Proven by assuming the role the application would connect as."""
    repo.insert_prediction(_prediction(CLOSE - timedelta(hours=24)))
    for role in ("service_role", "authenticated", "anon"):
        with repo.conn.cursor() as cur:
            cur.execute(f"set local role {role}")
            # Prove the row is reachable first. Otherwise an UndefinedTable error would
            # look exactly like enforcement working.
            cur.execute("select count(*) from predictions")
            assert cur.fetchone()["count"] == 1, f"{role} cannot even see the table"
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("update predictions set p_hat = 0.99")
        repo.conn.rollback()


def test_update_is_rejected_even_for_the_table_owner(repo: Repo) -> None:
    """The trigger. The revoke does not bind the owner, so without this a migration run
    as owner could still rewrite history."""
    repo.insert_prediction(_prediction(CLOSE - timedelta(hours=24)))
    with repo.conn.cursor() as cur, pytest.raises(psycopg.errors.RaiseException) as exc:
        cur.execute("update predictions set p_hat = 0.99")
    assert "append-only" in str(exc.value)
    repo.conn.rollback()


def test_delete_is_rejected_even_for_the_table_owner(repo: Repo) -> None:
    repo.insert_prediction(_prediction(CLOSE - timedelta(hours=24)))
    with repo.conn.cursor() as cur, pytest.raises(psycopg.errors.RaiseException):
        cur.execute("delete from predictions")
    repo.conn.rollback()


def test_the_row_actually_survives_the_rejected_update(repo: Repo) -> None:
    """A rejection that silently dropped the row would pass the tests above."""
    repo.insert_prediction(_prediction(CLOSE - timedelta(hours=24)))
    with repo.conn.cursor() as cur, pytest.raises(psycopg.errors.RaiseException):
        cur.execute("update predictions set p_hat = 0.99")
    repo.conn.rollback()
    with repo.conn.cursor() as cur:
        cur.execute("select p_hat from predictions")
        assert [r["p_hat"] for r in cur.fetchall()] == [0.42]


# -- P3: written before resolution ---------------------------------------


def test_prediction_before_close_is_accepted(repo: Repo) -> None:
    assert repo.insert_prediction(_prediction(CLOSE - timedelta(seconds=1))) > 0


def test_prediction_at_the_close_instant_is_rejected(repo: Repo) -> None:
    """The boundary is the bug that matters: `>` instead of `>=` lets a prediction in at
    the exact moment of close, when the outcome may already be determined."""
    with pytest.raises(psycopg.errors.RaiseException) as exc:
        repo.insert_prediction(_prediction(CLOSE))
    assert "at or after market close" in str(exc.value)
    repo.conn.rollback()


def test_prediction_after_close_is_rejected(repo: Repo) -> None:
    with pytest.raises(psycopg.errors.RaiseException):
        repo.insert_prediction(_prediction(CLOSE + timedelta(hours=1)))
    repo.conn.rollback()


def test_python_cannot_bypass_the_trigger(repo: Repo) -> None:
    """Enforced in the database, not in Python: raw SQL is rejected the same way."""
    with repo.conn.cursor() as cur, pytest.raises(psycopg.errors.RaiseException):
        cur.execute(
            """insert into predictions (market_id, ts, horizon_label, p_hat, model_version,
                                        git_sha, features_hash, market_mid, market_bid,
                                        market_ask)
               values ('A-1', %s, 'T-0', 0.5, 'v0', 'sha', 'h', 0.5, 0.49, 0.51)""",
            (CLOSE + timedelta(days=9),),
        )
    repo.conn.rollback()


# -- other schema guarantees ---------------------------------------------


@pytest.mark.parametrize("bad_p", [0.0, 1.0, -0.1, 1.5])
def test_p_hat_must_be_a_strict_probability(repo: Repo, bad_p: float) -> None:
    """log_loss is undefined at 0 and 1; the constraint stops it reaching the scorer."""
    with pytest.raises(psycopg.errors.CheckViolation):
        repo.insert_prediction(_prediction(CLOSE - timedelta(hours=1), p_hat=bad_p))
    repo.conn.rollback()


def test_duplicate_prediction_for_the_same_slot_is_rejected(repo: Repo) -> None:
    ts = CLOSE - timedelta(hours=24)
    repo.insert_prediction(_prediction(ts))
    with pytest.raises(psycopg.errors.UniqueViolation):
        repo.insert_prediction(_prediction(ts))
    repo.conn.rollback()


def test_outcome_must_be_binary(repo: Repo) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        repo.upsert_outcome(
            Resolution(market_id="A-1", resolved_at=CLOSE, outcome=2, raw_result="?")
        )
    repo.conn.rollback()


def test_was_disputed_stays_null_when_unknown(repo: Repo) -> None:
    """M4 forbids inventing this flag. NULL must mean unknown, not false."""
    repo.upsert_outcome(Resolution(market_id="A-1", resolved_at=CLOSE, outcome=1, raw_result="yes"))
    with repo.conn.cursor() as cur:
        cur.execute("select was_disputed from outcomes where market_id = 'A-1'")
        assert cur.fetchone()["was_disputed"] is None


def test_features_require_a_declared_pit_guarantee(repo: Repo) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        repo.insert_features("A-1", CLOSE, "fs", "trust_me", {"x": 1})
    repo.conn.rollback()


def test_scored_pairs_joins_predictions_to_outcomes(repo: Repo) -> None:
    repo.insert_prediction(_prediction(CLOSE - timedelta(hours=24)))
    repo.upsert_outcome(Resolution(market_id="A-1", resolved_at=CLOSE, outcome=1, raw_result="yes"))
    rows = repo.scored_pairs()
    assert len(rows) == 1
    assert rows[0]["outcome"] == 1
    assert rows[0]["market_mid"] == 0.40, "the price recorded at prediction time, not now"
    assert rows[0]["event_family"] == "A"
