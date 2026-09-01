"""Hits the live Kalshi API. Excluded from the default run.

Kept out of tests/unit/ deliberately: the unit module zeroes RETRY_PAUSE_S, and this
test needs the real backoff, because Kalshi resets back-to-back TLS handshakes.
"""

import pytest

from src.config import get_settings
from src.ingest.preflight import check_host, format_report

pytestmark = pytest.mark.integration


def test_live_endpoints_reachable() -> None:
    cfg = get_settings()
    base = cfg.kalshi_base_url.rstrip("/")
    reports = [check_host(p, base + p, cfg.http_timeout_s) for p in cfg.preflight_paths]
    assert all(r.ok for r in reports), format_report(reports)
