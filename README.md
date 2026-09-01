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

## Status: BLOCKED at C0

`*.polymarket.com` is unreachable from this machine. DNS resolves; the TLS handshake is
reset by the peer. Other Cloudflare-fronted hosts work, so it is hostname-specific and
remote, not a local TLS problem. See the C0 checkpoint report.

Working around a geoblock is out of scope by design. The ingest layer is defined against a
`MarketDataSource` protocol (M1) precisely so a different venue can be swapped in by a
human decision.
