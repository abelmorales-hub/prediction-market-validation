"""Record raw Kalshi responses to data/raw_samples/ so pydantic models are derived
from what the API actually returns rather than from documentation.

Read-only GETs on one pooled client, paced: Kalshi resets back-to-back handshakes.
Run: uv run python -m scripts.dump_raw_samples
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from src.config import get_settings
from src.ingest.http import PacedClient

ROWS_KEPT = 5


def _trim(body: Any) -> Any:
    """Cap repeated rows in list responses. Shape is preserved exactly; only repetition
    is dropped, because /series alone returns 13.7k entries and 21 MB.
    """
    if not isinstance(body, dict):
        return body
    return {
        k: (v[:ROWS_KEPT] if isinstance(v, list) and len(v) > ROWS_KEPT else v)
        for k, v in body.items()
    }


def _dump(client: PacedClient, out: Path, name: str, path: str) -> tuple[int, Any]:
    try:
        body: Any = client.get_json(path)
        status = 200
    except httpx.HTTPStatusError as exc:
        body, status = exc.response.text, exc.response.status_code
    (out / f"{name}.json").write_text(json.dumps(_trim(body), indent=2), encoding="utf-8")
    print(f"  {status}  {path}")
    return status, body


def main() -> int:
    cfg = get_settings()
    out = cfg.data_dir / "raw_samples"
    out.mkdir(parents=True, exist_ok=True)
    index: dict[str, dict[str, Any]] = {}

    with PacedClient(cfg.kalshi_base_url, cfg.http_timeout_s) as client:

        def grab(name: str, path: str) -> Any:
            status, body = _dump(client, out, name, path)
            index[name] = {"path": path, "status": status}
            return body if 200 <= status < 300 else None

        now = int(time.time())
        print("phase 1: endpoints needing no identifier")
        grab("exchange_status", "/exchange/status")
        # Window on close time. Unfiltered /markets is dominated by auto-generated MVE
        # combo shards, and the pipeline only cares about markets approaching close.
        markets = grab(
            "markets_open",
            f"/markets?limit=200&status=open&min_close_ts={now}&max_close_ts={now + 172800}",
        )
        grab("markets_settled", "/markets?limit=100&status=settled")
        grab("events", "/events?limit=3")
        grab("series_list", "/series?limit=3")

        # Sample the most liquid *non-combo* market: a dead market shows an empty book
        # and teaches nothing about the shape we actually have to parse.
        rows: list[dict[str, Any]] = list((markets or {}).get("markets", []))
        real = [m for m in rows if not m.get("mve_collection_ticker")]
        print(f"  {len(rows)} markets closing <48h, {len(real)} non-MVE")
        liquid = max(real or rows, key=lambda m: float(m.get("volume_fp") or 0), default={})
        ticker = liquid.get("ticker")
        event_ticker = liquid.get("event_ticker")
        series_ticker = ticker.split("-")[0] if isinstance(ticker, str) else None
        print(f"\nphase 2: ticker={ticker} volume={liquid.get('volume_fp')}")
        print(f"         event={event_ticker} series={series_ticker}")

        if ticker:
            grab("market_single", f"/markets/{ticker}")
            grab("market_orderbook", f"/markets/{ticker}/orderbook?depth=10")
            grab("market_trades", f"/markets/trades?ticker={ticker}&limit=5")
        if event_ticker:
            grab("event_single", f"/events/{event_ticker}")
        if series_ticker:
            grab("series_single", f"/series/{series_ticker}")
            now = int(time.time())
            grab(
                "candlesticks",
                f"/series/{series_ticker}/markets/{ticker}/candlesticks"
                f"?start_ts={now - 86400}&end_ts={now}&period_interval=60",
            )

    ok = sum(1 for v in index.values() if 200 <= int(v["status"]) < 300)
    total = len(index)
    written = {
        "_note": {
            "recorded": datetime.now(UTC).isoformat(),
            "trimmed": f"list fields capped at {ROWS_KEPT} rows; object shape is verbatim",
        },
        **index,
    }
    (out / "_index.json").write_text(json.dumps(written, indent=2), encoding="utf-8")
    print(f"\n{ok}/{total} endpoints returned 2xx -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
