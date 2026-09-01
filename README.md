# Prediction market forecast validation

Does a forecast `p̂` beat the market's own price at the same timestamp, out of sample,
with enough power to rule out luck?

Read-only. No orders, no wallet, no keys, no capital logic. See the milestone plan in
the build prompt; this repo currently contains **M0 only**.

## Setup

```bash
cp .env.example .env
uv sync
uv run python -m scripts.preflight   # exits 1 if an endpoint is unreachable
```

## Checks

```bash
uv run ruff check .
uv run mypy --strict src/
uv run pytest              # unit tests; never touches the network
uv run pytest -m integration
```

## Venue: Kalshi

Polymarket was the original target but is unreachable from here — DNS resolves, then the
peer resets the TLS handshake for every `*.polymarket.com` name, while other Cloudflare
hosts work fine. Routing around that is out of scope by design, so the venue was switched
to Kalshi, which pre-flights clean.

Kalshi serves metadata and order books from one host (`api.elections.kalshi.com`), where
Polymarket split them across gamma and clob. `MarketDataSource` (M1) is the seam that keeps
the rest of the pipeline venue-agnostic.

**Observed from here:** back-to-back TLS handshakes to that host start getting reset after
about two connections, and recover completely after a ~60 s pause (measured: 10/10 clean).
Pre-flight retries once for that reason. M1 must use a pooled `httpx.Client` with
keep-alive so it is not opening a fresh handshake per request — the connection rate, not
the request rate, is what trips it.
