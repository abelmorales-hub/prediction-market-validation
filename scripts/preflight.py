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

    base = cfg.kalshi_base_url.rstrip("/")
    reports: list[HostReport] = [
        check_host(path, base + path, cfg.http_timeout_s) for path in cfg.preflight_paths
    ]

    # The weather feed is a second host and a second way to be silently dead.
    # Polymarket resolved in DNS and then reset every handshake; nothing says a
    # forecast provider cannot do the same, and finding out during an unattended run
    # costs features that cannot be backfilled.
    if cfg.gefs_base_url:
        probe = (
            f"{cfg.gefs_base_url.rstrip('/')}/v1/ensemble"
            f"?latitude=40.78&longitude=-73.97&models={cfg.gefs_model}"
            "&daily=temperature_2m_max&forecast_days=1"
        )
        reports.append(check_host("gefs", probe, cfg.http_timeout_s))

    print(format_report(reports))
    return 0 if all(r.ok for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
