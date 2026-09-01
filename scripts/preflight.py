"""Pre-flight gate. Run: uv run python -m scripts.preflight

Exits 1 if any endpoint is unreachable. That is a stop signal, not a retry signal.
"""

from __future__ import annotations

import sys

from src.config import get_settings
from src.ingest.preflight import HostReport, check_host, format_report


def main() -> int:
    try:
        cfg = get_settings()
    except Exception as exc:  # noqa: BLE001
        print(f"Config error: {exc}\nCopy .env.example to .env first.", file=sys.stderr)
        return 2

    targets = [
        ("gamma", cfg.gamma_base_url.rstrip("/") + cfg.gamma_preflight_path),
        ("clob", cfg.clob_base_url.rstrip("/") + cfg.clob_preflight_path),
    ]
    reports: list[HostReport] = [
        check_host(label, url, cfg.http_timeout_s) for label, url in targets
    ]
    print(format_report(reports))
    return 0 if all(r.ok for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
