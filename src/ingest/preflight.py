"""Read-only reachability check.

DNS, TLS and HTTP are probed as three separate stages so a failure names its own
layer: a DNS miss, a blocked handshake and a 403 geoblock are different problems
with different answers, and collapsing them into "connection failed" destroys the
only information worth having.

There is deliberately no proxy, retry-through-alternate-route or fallback host.
If a stage fails, it is reported and the caller exits non-zero.
"""

from __future__ import annotations

import socket
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

SKIPPED = "skipped (previous stage failed)"


@dataclass(frozen=True)
class StageResult:
    name: str
    ok: bool
    detail: str
    elapsed_ms: float


@dataclass(frozen=True)
class HostReport:
    label: str
    url: str
    stages: tuple[StageResult, ...]

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.stages)


def _timed(name: str, probe: Callable[[], str]) -> StageResult:
    start = time.perf_counter()
    try:
        detail = probe()
        ok = True
    except Exception as exc:  # noqa: BLE001 - the failure text is the whole point
        detail = f"{type(exc).__name__}: {exc}"
        ok = False
    return StageResult(name, ok, detail, (time.perf_counter() - start) * 1000)


def _dns(host: str, port: int) -> str:
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return ", ".join(sorted({str(info[4][0]) for info in infos}))


def _tls(host: str, port: int, timeout: float) -> str:
    # Default context: certificate and hostname verification on. A handshake that
    # only succeeds with verification disabled is a failure worth seeing.
    ctx = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            cipher = tls.cipher()
            return f"{tls.version()}, {cipher[0] if cipher else 'unknown cipher'}"


def _http(url: str, timeout: float) -> str:
    # trust_env=False: never pick up HTTP_PROXY/HTTPS_PROXY from the environment.
    # The check must measure the direct route or say that it could not.
    with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=True) as client:
        resp = client.get(url)
    body = resp.text[:120].replace("\n", " ")
    if not resp.is_success:
        raise RuntimeError(f"HTTP {resp.status_code} {resp.reason_phrase} - {body}")
    return f"HTTP {resp.status_code}, {len(resp.content)} bytes - {body}"


def check_host(label: str, url: str, timeout: float) -> HostReport:
    """Probe one endpoint. Later stages are skipped once one fails."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)

    dns = _timed("DNS", lambda: _dns(host, port))
    if not dns.ok:
        skipped = (
            StageResult("TLS", False, SKIPPED, 0.0),
            StageResult("HTTP", False, SKIPPED, 0.0),
        )
        return HostReport(label, url, (dns, *skipped))

    tls = _timed("TLS", lambda: _tls(host, port, timeout))
    if not tls.ok:
        return HostReport(label, url, (dns, tls, StageResult("HTTP", False, SKIPPED, 0.0)))

    http = _timed("HTTP", lambda: _http(url, timeout))
    return HostReport(label, url, (dns, tls, http))


def format_report(reports: list[HostReport]) -> str:
    lines = ["PRE-FLIGHT", ""]
    for r in reports:
        lines.append(f"{'PASS' if r.ok else 'FAIL'}  {r.label}  {r.url}")
        for s in r.stages:
            mark = "  ok  " if s.ok else " FAIL "
            lines.append(f"  [{mark}] {s.name:<4} {s.elapsed_ms:7.1f} ms  {s.detail}")
        lines.append("")
    failed = [r.label for r in reports if not r.ok]
    if failed:
        lines.append(f"UNREACHABLE: {', '.join(failed)}")
        lines.append("Stopping. Do not add a proxy or fallback route - that is a human decision.")
    else:
        lines.append("All endpoints reachable.")
    return "\n".join(lines)
