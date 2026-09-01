"""No network: DNS and TLS probes are patched out, HTTP is mocked with respx."""

import httpx
import pytest
import respx

from src.ingest import preflight
from src.ingest.preflight import SKIPPED, HostReport, StageResult, check_host, format_report

URL = "https://gamma-api.polymarket.com/markets?limit=1"


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(preflight, "_dns", lambda host, port: "1.2.3.4")
    monkeypatch.setattr(preflight, "_tls", lambda host, port, timeout: "TLSv1.3, AES, issuer=X")


@respx.mock
def test_reachable_host_passes_all_three_stages(offline: None) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json=[{"id": "1"}]))
    report = check_host("gamma", URL, 5.0)
    assert report.ok
    assert [s.name for s in report.stages] == ["DNS", "TLS", "HTTP"]


@respx.mock
def test_http_error_status_fails_without_masking_dns_and_tls(offline: None) -> None:
    """A 403 geoblock must stay distinguishable from a DNS failure."""
    respx.get(URL).mock(return_value=httpx.Response(403, text="blocked"))
    report = check_host("gamma", URL, 5.0)
    assert not report.ok
    dns, tls, http = report.stages
    assert dns.ok and tls.ok, "network layers resolved; only HTTP failed"
    assert not http.ok
    assert "403" in http.detail


def test_dns_failure_skips_later_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(host: str, port: int) -> str:
        raise OSError("Name or service not known")

    monkeypatch.setattr(preflight, "_dns", boom)
    report = check_host("gamma", URL, 5.0)
    assert not report.ok
    assert "Name or service not known" in report.stages[0].detail
    assert [s.detail for s in report.stages[1:]] == [SKIPPED, SKIPPED]


def test_tls_failure_skips_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(preflight, "_dns", lambda host, port: "1.2.3.4")

    def boom(host: str, port: int, timeout: float) -> str:
        raise ConnectionResetError("handshake failed")

    monkeypatch.setattr(preflight, "_tls", boom)
    report = check_host("gamma", URL, 5.0)
    assert report.stages[0].ok
    assert not report.stages[1].ok
    assert report.stages[2].detail == SKIPPED


@pytest.mark.parametrize("ok,expected", [(True, "All endpoints"), (False, "UNREACHABLE")])
def test_format_report_states_the_verdict(ok: bool, expected: str) -> None:
    report = HostReport("gamma", URL, (StageResult("DNS", ok, "1.2.3.4", 1.0),))
    assert expected in format_report([report])


@pytest.mark.integration
def test_live_endpoints_reachable() -> None:
    from src.config import get_settings

    cfg = get_settings()
    url = cfg.gamma_base_url.rstrip("/") + cfg.gamma_preflight_path
    assert check_host("gamma", url, cfg.http_timeout_s).ok
