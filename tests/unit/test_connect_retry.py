"""connect() retry policy: wait out a resolver, fail fast on anything Postgres answered.
No network, no database."""

import psycopg
import pytest
from psycopg import errors

from src.storage import supabase_repo
from src.storage.supabase_repo import CONNECT_ATTEMPTS, _is_transient, connect

DSN = "postgresql://u:p@host/db"

# What a cold resume actually raises: resolution failed, the server was never reached.
DNS_DOWN = psycopg.OperationalError(
    "failed to resolve host 'aws-1-eu-west-1.pooler.supabase.com': [Errno 11001] getaddrinfo failed"
)


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the ~1 min ladder out of the test runtime."""
    monkeypatch.setattr(supabase_repo, "CONNECT_INITIAL_S", 0.0)
    monkeypatch.setattr(supabase_repo, "CONNECT_MAX_S", 0.0)


def _fake_connect(*excs: BaseException) -> tuple[list[int], object, object]:
    """psycopg.connect stand-in: raise each exc in turn, then return a sentinel."""
    calls: list[int] = []
    sentinel = object()

    def fake(dsn: str, **kwargs: object) -> object:
        calls.append(1)
        if len(calls) <= len(excs):
            raise excs[len(calls) - 1]
        return sentinel

    return calls, fake, sentinel


def test_sqlstate_separates_network_faults_from_server_answers() -> None:
    assert _is_transient(DNS_DOWN)
    # InvalidPassword subclasses OperationalError, so retrying on the type alone would
    # back off for a minute on a typo'd password. sqlstate is what tells them apart.
    assert issubclass(errors.InvalidPassword, psycopg.OperationalError)
    assert not _is_transient(errors.InvalidPassword("password authentication failed"))
    assert not _is_transient(ValueError("unrelated"))


def test_a_resolver_that_comes_up_late_is_waited_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake, sentinel = _fake_connect(DNS_DOWN, DNS_DOWN)
    monkeypatch.setattr(psycopg, "connect", fake)
    assert connect(DSN) is sentinel
    assert len(calls) == 3, "two failures then a success is one connection, not a crash"


def test_a_dead_resolver_gives_up_for_the_next_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, fake, _ = _fake_connect(*[DNS_DOWN] * (CONNECT_ATTEMPTS + 2))
    monkeypatch.setattr(psycopg, "connect", fake)
    with pytest.raises(psycopg.OperationalError):
        connect(DSN)
    assert len(calls) == CONNECT_ATTEMPTS, "the budget is bounded; the scheduler retries"


def test_bad_credentials_are_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, fake, _ = _fake_connect(errors.InvalidPassword("password authentication failed"))
    monkeypatch.setattr(psycopg, "connect", fake)
    with pytest.raises(psycopg.OperationalError):
        connect(DSN)
    assert len(calls) == 1, "the server answered; waiting cannot help"
