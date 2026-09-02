# Prediction market forecast validation

Does a forecast `p̂` beat the market's own price at the same timestamp, out of sample,
with enough power to rule out luck?

Read-only. No orders, no wallet, no keys, no capital logic. See the milestone plan in
the build prompt; this repo contains **M0–M6**, the complete data and scoring layers.

No forecasting model exists here, by design. The scorer was built before any model and
cannot import one — `src/scoring/` is forbidden from seeing `src/baselines/`,
`src/models/` or `src/analysis/`, enforced in CI by import-linter rather than by
discipline.

## Setup

```bash
cp .env.example .env                        # then add SUPABASE_DB_URL
uv sync
uv run python -m scripts.preflight          # exits 1 if an endpoint is unreachable
uv run python -m scripts.dump_raw_samples   # re-record data/raw_samples/
```

## Collecting

```bash
uv run python -m scripts.snapshot --minutes 60     # 0 = until interrupted
uv run python -m scripts.gap_report --days 1       # exits 1 on a CRITICAL gap
uv run python -m scripts.watch_resolutions --hours 24
```

## Scoring

```bash
uv run python -m scripts.emit_baselines            # must run while markets are OPEN
uv run python -m scripts.score --out reports/validation.md
```

`emit_baselines` writes one prediction per (market, horizon, baseline) into the
append-only `predictions` table, stamped with the market price observed at that same
instant. **It cannot be backfilled**: the database refuses a prediction stamped at or
after the market's close, so a horizon that passes without this job running is gone. Run
it alongside the snapshotter.

A horizon is represented by the nearest snapshot *at or before* it, within five minutes,
and by nothing otherwise — never a later one. A snapshot taken after T-1h has seen
trading the forecast had not, and scoring it as contemporaneous is a look-ahead leak that
inflates every number downstream while leaving them all plausible.

The snapshotter is resumable: all scheduling state is the last recorded timestamp per
market, read back from Parquet on start. A hard kill costs at most one buffer.

**Universe sizing.** A 30-hour window returns ~4,400 open markets, overwhelmingly strike
ladders where each rung is a separate market that never trades. Sweeping all of them
takes far longer than the one-minute dense cadence, so `--min-volume` (default 1000)
and `--max-markets` (default 400) bound it — 105 markets at the time of writing, a ~21 s
sweep. That is a *research* choice as much as a technical one: raising the floor buys
cadence fidelity and costs sample size, and both feed the power calculation in §5 of the
design doc.

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
Pre-flight retries once for that reason, and `PacedClient` holds one pooled connection with
a 60 s keepalive — the connection rate, not the request rate, is what trips it.

## What the API actually does

Derived from `data/raw_samples/`, recorded 2026-09-01. Documentation and write-ups
disagree with several of these:

- Prices are **strings of dollars** (`"0.6900"`), not integer cents. Sizes too, under
  `_fp` names.
- `GET /markets/{t}/orderbook` returns `yes_dollars` and `no_dollars`, and **both are bid
  stacks**. A NO bid at `p` is a YES ask at `1-p`. Reading `no_dollars` as an ask ladder
  silently inverts the book — every price downstream would be wrong but plausible.
  `tests/integration/` checks the derived touch against the venue's own quote for exactly
  this reason.
- Query `status` values and returned `status` values differ: `?status=open` yields markets
  reading `active`, and `?status=active` is a 400.
- `category` is **not** on the market — it lives on the series. One `/series` call returns
  all ~13.7k of them (~21 MB), cached to `data/cache/` for a day.
- Unfiltered `/markets` is dominated by auto-generated MVE combo shards; 2,600 scanned in
  natural order were all combos. `list_open_markets` filters them and windows on close
  time, which is the universe the T-24h horizon actually needs.
