"""No network: DNS and TLS probes are patched out, HTTP is mocked with respx."""

import httpx
import pytest
import respx

from src.ingest import preflight
from src.ingest.preflight import SKIPPED, HostReport, StageResult, check_host, format_report

URL = "https://api.elections.kalshi.com/trade-api/v2/markets?limit=1"


@pytest.fixture(autouse=True)
def no_retry_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the retry backoff out of the unit-test runtime."""
    monkeypatch.setattr(preflight, "RETRY_PAUSE_S", 0.0)


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(preflight, "_dns", lambda host, port: "1.2.3.4")
    monkeypatch.setattr(preflight, "_tls", lambda host, port, timeout: "TLSv1.3, AES, issuer=X")


@respx.mock
def test_reachable_host_passes_all_three_stages(offline: None) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json={"markets": []}))
    report = check_host("markets", URL, 5.0)
    assert report.ok
    assert [s.name for s in report.stages] == ["DNS", "TLS", "HTTP"]


@respx.mock
def test_http_error_status_fails_without_masking_dns_and_tls(offline: None) -> None:
    """A 403 geoblock must stay distinguishable from a DNS failure."""
    respx.get(URL).mock(return_value=httpx.Response(403, text="blocked"))
    report = check_host("markets", URL, 5.0)
    assert not report.ok
    dns, tls, http = report.stages
    assert dns.ok and tls.ok, "network layers resolved; only HTTP failed"
    assert not http.ok
    assert "403" in http.detail


def test_dns_failure_skips_later_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(host: str, port: int) -> str:
        raise OSError("Name or service not known")

    monkeypatch.setattr(preflight, "_dns", boom)
    report = check_host("markets", URL, 5.0)
    assert not report.ok
    assert "Name or service not known" in report.stages[0].detail
    assert f"failed {preflight.ATTEMPTS} attempts" in report.stages[0].detail
    assert [s.detail for s in report.stages[1:]] == [SKIPPED, SKIPPED]


def test_tls_failure_skips_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(preflight, "_dns", lambda host, port: "1.2.3.4")

    def boom(host: str, port: int, timeout: float) -> str:
        raise ConnectionResetError("handshake failed")

    monkeypatch.setattr(preflight, "_tls", boom)
    report = check_host("markets", URL, 5.0)
    assert report.stages[0].ok
    assert not report.stages[1].ok
    assert report.stages[2].detail == SKIPPED


def test_transient_failure_is_retried_and_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kalshi resets back-to-back handshakes; one retry must not hide that it happened."""
    calls = {"n": 0}

    def flaky(host: str, port: int) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionResetError("WinError 10054")
        return "1.2.3.4"

    monkeypatch.setattr(preflight, "_dns", flaky)
    monkeypatch.setattr(preflight, "_tls", lambda host, port, timeout: "TLSv1.3, AES")
    with respx.mock:
        respx.get(URL).mock(return_value=httpx.Response(200, json={"markets": []}))
        report = check_host("markets", URL, 5.0)
    assert report.ok
    assert "passed on attempt 2" in report.stages[0].detail
    assert calls["n"] == 2


@pytest.mark.parametrize("ok,expected", [(True, "All endpoints"), (False, "UNREACHABLE")])
def test_format_report_states_the_verdict(ok: bool, expected: str) -> None:
    report = HostReport("markets", URL, (StageResult("DNS", ok, "1.2.3.4", 1.0),))
    assert expected in format_report([report])
