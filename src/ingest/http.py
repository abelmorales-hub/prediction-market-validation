"""Pooled, self-pacing, read-only HTTP transport for the Kalshi API.

Kalshi returns 429 with no `Retry-After` and no `X-RateLimit-*` headers, and it resets
TLS handshakes that arrive back to back. There is nothing to parse, so the pacing is
driven by what we observe: throttles and connection resets widen the inter-request
interval, clean fast responses narrow it back toward the floor (AIMD).

Connection reuse matters more than request spacing here — the resets track handshake
rate, not request rate — so one pooled client with a long keepalive is the main defence.

GET only. This module has no way to write.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

# A reset handshake surfaces as ConnectError, not as a status code.
TRANSIENT_EXC = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
)


class TransientHTTPError(Exception):
    """429 or 5xx: worth retrying. 4xx is not and is raised as HTTPStatusError."""


class DiskCache:
    """Metadata cache. The question text does not change; the book does, so only
    long-lived metadata is ever cached."""

    def __init__(self, root: Path, ttl_s: float) -> None:
        self.root = root
        self.ttl_s = ttl_s
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / f"{hashlib.sha256(key.encode()).hexdigest()[:32]}.json"

    def get(self, key: str) -> Any | None:
        p = self._path(key)
        if not p.exists() or (time.time() - p.stat().st_mtime) > self.ttl_s:
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return None

    def put(self, key: str, value: Any) -> None:
        self._path(key).write_text(json.dumps(value), encoding="utf-8")


class PacedClient:
    """One pooled httpx client with adaptive spacing and bounded retries."""

    def __init__(
        self,
        base_url: str,
        timeout: float,
        *,
        min_interval_s: float = 0.2,
        max_interval_s: float = 10.0,
        slow_latency_s: float = 2.0,
        cache: DiskCache | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            trust_env=False,  # never inherit a proxy from the environment
            headers={"Accept": "application/json"},
            limits=httpx.Limits(
                max_keepalive_connections=4, max_connections=8, keepalive_expiry=60.0
            ),
        )
        self.min_interval_s = min_interval_s
        self.max_interval_s = max_interval_s
        self.slow_latency_s = slow_latency_s
        self.interval_s = min_interval_s
        self.cache = cache
        self._last_sent = 0.0
        self.throttles = 0

    def __enter__(self) -> PacedClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- adaptive interval ------------------------------------------------

    def _widen(self) -> None:
        self.throttles += 1
        self.interval_s = min(self.max_interval_s, max(self.interval_s * 2, 1.0))

    def _narrow(self, latency_s: float) -> None:
        if latency_s > self.slow_latency_s:
            self.interval_s = min(self.max_interval_s, self.interval_s * 1.5)
        else:
            self.interval_s = max(self.min_interval_s, self.interval_s * 0.8)

    def _pace(self) -> None:
        wait = self.interval_s - (time.monotonic() - self._last_sent)
        if wait > 0:
            time.sleep(wait)
        self._last_sent = time.monotonic()

    # -- requests ---------------------------------------------------------

    @retry(
        retry=retry_if_exception_type((*TRANSIENT_EXC, TransientHTTPError)),
        stop=stop_after_attempt(5),
        wait=wait_exponential_jitter(initial=1.0, max=30.0),
        reraise=True,
    )
    def _send(self, path: str, params: dict[str, Any] | None) -> httpx.Response:
        self._pace()
        started = time.perf_counter()
        try:
            resp = self._client.get(path, params=params)
        except TRANSIENT_EXC:
            self._widen()
            raise
        if resp.status_code == 429 or resp.status_code >= 500:
            self._widen()
            raise TransientHTTPError(f"HTTP {resp.status_code} on {path}")
        resp.raise_for_status()  # 4xx: permanent, not retried
        self._narrow(time.perf_counter() - started)
        return resp

    def get_json(
        self, path: str, params: dict[str, Any] | None = None, *, cacheable: bool = False
    ) -> Any:
        key = path + "?" + json.dumps(params or {}, sort_keys=True)
        if cacheable and self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                return hit
        body = self._send(path, params).json()
        if cacheable and self.cache is not None:
            self.cache.put(key, body)
        return body
