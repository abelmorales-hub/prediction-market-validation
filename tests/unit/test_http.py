"""Transport: retry policy, adaptive pacing, disk cache. No network."""

from pathlib import Path

import httpx
import pytest
import respx

from src.ingest.http import (
    MAX_ATTEMPTS,
    TICK_ATTEMPTS,
    DiskCache,
    PacedClient,
    TransientHTTPError,
)

BASE = "https://api.test/v2"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> PacedClient:
    monkeypatch.setattr("time.sleep", lambda _: None)  # keep pacing out of the runtime
    c = PacedClient(BASE, 5.0, min_interval_s=0.1, max_interval_s=8.0)
    c._wait = lambda *a, **k: 0  # type: ignore[assignment]
    return c


@respx.mock
def test_429_is_retried_and_widens_the_interval(client: PacedClient) -> None:
    route = respx.get(f"{BASE}/x").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json={"ok": True})]
    )
    before = client.interval_s
    assert client.get_json("/x") == {"ok": True}
    assert route.call_count == 2
    assert client.throttles == 1
    assert client.interval_s > before, "a throttle must slow the next request down"


@respx.mock
def test_connection_reset_is_retried(client: PacedClient) -> None:
    """The Kalshi TLS reset surfaces as ConnectError, not as a status code."""
    route = respx.get(f"{BASE}/x").mock(
        side_effect=[httpx.ConnectError("WinError 10054"), httpx.Response(200, json=1)]
    )
    assert client.get_json("/x") == 1
    assert route.call_count == 2


@respx.mock
def test_404_is_permanent_and_not_retried(client: PacedClient) -> None:
    route = respx.get(f"{BASE}/x").mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        client.get_json("/x")
    assert route.call_count == 1, "retrying a 404 just wastes the rate budget"


@respx.mock
def test_retries_are_bounded(client: PacedClient) -> None:
    route = respx.get(f"{BASE}/x").mock(return_value=httpx.Response(503))
    with pytest.raises(TransientHTTPError):
        client.get_json("/x")
    assert route.call_count == MAX_ATTEMPTS


@respx.mock
def test_a_tick_call_gives_up_long_before_the_default_ladder(client: PacedClient) -> None:
    """The budget belongs to the caller, not to the client.

    A book fetch on the dense sweep is retried by the next tick 60 s later, so spending
    the full ladder on it stalls every other market in the same sweep for minutes.
    """
    route = respx.get(f"{BASE}/x").mock(side_effect=httpx.ConnectError("WinError 10054"))
    with pytest.raises(httpx.ConnectError):
        client.get_json("/x", attempts=TICK_ATTEMPTS)
    assert route.call_count == TICK_ATTEMPTS
    assert TICK_ATTEMPTS < MAX_ATTEMPTS, "the short budget must actually be shorter"


@respx.mock
def test_clean_responses_narrow_the_interval_back_down(client: PacedClient) -> None:
    respx.get(f"{BASE}/x").mock(return_value=httpx.Response(200, json=1))
    client.interval_s = 4.0
    for _ in range(10):
        client.get_json("/x")
    assert client.interval_s < 4.0
    assert client.interval_s >= client.min_interval_s, "must not drop below the floor"


@respx.mock
def test_interval_is_capped(client: PacedClient) -> None:
    respx.get(f"{BASE}/x").mock(return_value=httpx.Response(429))
    with pytest.raises(TransientHTTPError):
        client.get_json("/x")
    assert client.interval_s <= client.max_interval_s


@respx.mock
def test_cache_serves_metadata_without_a_second_request(tmp_path: Path) -> None:
    route = respx.get(f"{BASE}/series").mock(return_value=httpx.Response(200, json={"n": 1}))
    with PacedClient(BASE, 5.0, cache=DiskCache(tmp_path, ttl_s=3600)) as c:
        assert c.get_json("/series", cacheable=True) == {"n": 1}
        assert c.get_json("/series", cacheable=True) == {"n": 1}
    assert route.call_count == 1


@respx.mock
def test_books_are_never_cached(tmp_path: Path) -> None:
    """The question text does not change; the book does."""
    route = respx.get(f"{BASE}/book").mock(return_value=httpx.Response(200, json={"n": 1}))
    with PacedClient(BASE, 5.0, cache=DiskCache(tmp_path, ttl_s=3600)) as c:
        c.get_json("/book")
        c.get_json("/book")
    assert route.call_count == 2


def test_expired_cache_entry_is_a_miss(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path, ttl_s=-1)
    cache.put("k", {"stale": True})
    assert cache.get("k") is None


def test_cache_distinguishes_params(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path, ttl_s=3600)
    cache.put("/m?" + '{"a": 1}', "one")
    assert cache.get("/m?" + '{"a": 2}') is None
