# Forecast Validation Pipeline — Prediction Markets

**Status:** design. Research phase, no capital.
**Stack:** Python · DuckDB/Parquet local · Supabase (Postgres) · Claude Code for implementation.

---

## 0. The question this pipeline answers

Not *"does my model make money in paper trading?"*

It is: **does my probability `p̂` beat the market price at the same instant, out of sample, with enough `n` to be distinguishable from luck?**

If you don't beat the price, there is no edge. Everything else — simulated PnL, win rate, equity curve — is decoration that can come out positive by chance for months.

---

## 1. Non-negotiable principles

| # | Principle | What it prevents |
|---|-----------|------------------|
| **P1** | The benchmark is the **price**, not the outcome | Confusing "predicting well" with "having an edge" |
| **P2** | **Point-in-time or it doesn't exist**: every feature must be reconstructible exactly as it was at its timestamp | Lookahead bias |
| **P3** | Predictions are **immutable and written before resolution** | Rewriting history, p-hacking |
| **P4** | Never execute against the midpoint | Backtests that don't survive the spread |
| **P5** | Observations **are correlated** | Overestimating effective `n` by 3-5× |
| **P6** | Every test is **pre-registered** before looking at results | Hidden multiple testing |

---

## 2. Why forward-test and (almost) never backtest

Backtesting is impossible to do honestly for most feature classes:

- **Knowledge-cutoff contamination.** An LLM predicting markets that resolved before its cutoff remembers the answer. That is recall, not forecasting.
- **No sentiment archive.** You cannot reconstruct what was being said on X eight months ago at 14:00 without a genuine historical archive. What you reconstruct today is already coloured by the outcome.
- **Market selection.** The markets that are easy to find in the API today are the ones that got volume. Survivorship bias in the universe itself.

**Exception — domains with verifiable point-in-time archives:**

| Domain | Archived source | Honest backtest |
|---|---|---|
| Weather | Archived NOAA GFS/GEFS runs, ERA5, historical ECMWF output | ✅ Yes |
| Sports | Historical bookmaker closing lines | ✅ Yes |
| Macro | Calendar + timestamped published consensus | ⚠️ Partial |
| Politics / news | — | ❌ No |
| Crypto | Timestamped OHLCV | ✅ Yes, but dubious edge |

**Operational conclusion:** start with forward-testing (record predictions live, wait for resolution) for everything, and allow yourself backtests only in the two green-column domains.

---

## 3. Architecture

```
  ┌──────────────┐
  │  Ingestion   │  Gamma API (metadata, resolution)
  │  (scheduler) │  CLOB API (book, prices-history)
  └──────┬───────┘  Domain sources (NOAA, odds, macro)
         │
         ├──────────────────────────────┐
         ▼                              ▼
  ┌─────────────────┐          ┌──────────────────┐
  │ Local Parquet   │          │ Supabase         │
  │ + DuckDB        │          │ (Postgres)       │
  │                 │          │                  │
  │ book_snapshots  │          │ markets          │
  │ (high volume)   │          │ predictions ⚠️   │
  │                 │          │ outcomes         │
  │                 │          │ features_pit     │
  └────────┬────────┘          │ scores (mat.view)│
           │                   └────────┬─────────┘
           └──────────┬─────────────────┘
                      ▼
              ┌───────────────┐
              │   Features    │  each builder declares its
              │  (versioned)  │  point-in-time guarantee
              └───────┬───────┘
                      ▼
              ┌───────────────┐
              │    Models     │  p̂ = f(features)
              │  (versioned)  │  stamped: model_version + git sha
              └───────┬───────┘
                      ▼
              ┌───────────────┐
              │    Scoring    │  resolved markets only
              │  (separate)   │  paired Brier vs market
              └───────────────┘
```

**Golden rule:** the scoring module must not import anything from the model module. Physical separation so you cannot "tune" while watching the score.

### Minimal schema (Supabase)

```sql
-- Catalogue
create table markets (
  condition_id      text primary key,
  slug              text,
  question          text not null,
  category          text,              -- weather | sports | macro | politics | crypto
  event_family      text,              -- clustering key (see §5)
  created_at        timestamptz,
  end_date          timestamptz,
  resolution_source text,
  yes_token_id      text,
  no_token_id       text
);

-- Predictions: APPEND-ONLY, always written before resolution
create table predictions (
  id             bigserial primary key,
  condition_id   text not null references markets,
  ts             timestamptz not null,          -- instant of the prediction
  horizon_label  text not null,                 -- 'T-24h' | 'T-6h' | 'T-1h'
  p_hat          double precision not null check (p_hat > 0 and p_hat < 1),
  model_version  text not null,
  git_sha        text not null,
  features_hash  text not null,
  market_mid     double precision not null,     -- price at the SAME ts
  market_bid     double precision not null,
  market_ask     double precision not null,
  created_at     timestamptz not null default now(),
  unique (condition_id, ts, model_version, horizon_label)
);

-- Resolutions
create table outcomes (
  condition_id  text primary key references markets,
  resolved_at   timestamptz not null,
  outcome       smallint not null check (outcome in (0,1)),
  was_disputed  boolean not null default false,  -- UMA
  notes         text
);

-- Point-in-time features, versioned
create table features_pit (
  condition_id   text not null references markets,
  ts             timestamptz not null,
  feature_set    text not null,
  pit_guarantee  text not null,   -- 'archived' | 'live_captured' | 'reconstructed_unsafe'
  payload        jsonb not null,
  primary key (condition_id, ts, feature_set)
);
```

**Enforcing P3 in Postgres** (this is what turns discipline into architecture):

```sql
revoke update, delete on predictions from authenticated, anon, service_role;

-- Plus a trigger rejecting predictions written after market close
create function check_pre_resolution() returns trigger as $$
begin
  if new.ts >= (select end_date from markets where condition_id = new.condition_id) then
    raise exception 'Prediction timestamp is after market close';
  end if;
  return new;
end;
$$ language plpgsql;
```

### API notes

- **Gamma** (`gamma-api.polymarket.com`) — discovery and metadata. No auth. `/markets`, `/events`. This is where `clobTokenIds` come from.
- **CLOB** (`clob.polymarket.com`) — order book and historical price curve. *Read* endpoints are public; only order writing requires wallet + signature.
- **Data API** — positions and trades per wallet.
- Note: **CLOB V2 shipped in April 2026** — collateral moved to pUSD, order signing changed, and the legacy `py-clob-client` was retired. Verify current docs before writing the client; this barely affects reads but affects execution entirely.
- Rate limits are Cloudflare-driven on 10s windows, generous for polling but with no `Retry-After` headers. Cache Gamma metadata aggressively — the book changes, the question does not.

**Pre-flight check:** verify the endpoints are reachable from your connection. The DGOJ block is ISP-level against the websites; its reach over the API domains may differ. If they are not reachable, do not route around it — the design is portable to other sources and we revisit.

---

## 4. Metrics

### 4.1 Core — paired difference

For each resolved market `i`, with outcome `y_i ∈ {0,1}`:

```
BS_model,i   = (p̂_i − y_i)²
BS_market,i  = (m_i − y_i)²        # m_i = mid at the SAME timestamp
d_i          = BS_market,i − BS_model,i
```

`d_i > 0` means your model did better than the market on that case.

**The statistic:** `mean(d)` with a confidence interval from a **block bootstrap**, clustering on `event_family`. Not a plain t-test.

**Brier Skill Score:** `BSS = 1 − Σ BS_model / Σ BS_market`. Reads as % improvement over the market. A sustained BSS of 0.03-0.05 would be exceptional.

### 4.2 Secondary diagnostics

- **Calibration**: reliability diagram with 10 bins + ECE. A model can beat the market on Brier while being badly calibrated (lucky in the tail). If it isn't calibrated, you cannot size positions.
- **Discrimination**: AUC. Separates "knows how to rank" from "knows how to put numbers on it".
- **Price-bucket stratification**: report `mean(d)` per bucket of `m_i`: [0-0.05], [0.05-0.20], [0.20-0.80], [0.80-0.95], [0.95-1]. **Almost all fake edge lives in the tails.** If your improvement disappears once you drop the extreme buckets, you have nothing.

### 4.3 Economic (only after passing the statistical gate)

- Buy at the **ask**, sell at the **bid**. Never mid.
- Size capped by the depth visible in the snapshot, not by what you'd like.
- No-trade zone: only trade when `|p̂ − m| > spread/2 + margin`. Report what fraction of your signals survives that filter.
- Adverse selection: you get filled when you're wrong. Approximate it by assuming fills only at the touch and only for displayed size.

---

## 5. Statistical power — how many markets you need

Paired-difference formula:

```
n = (z_{α/2} + z_β)² · σ_d² / δ²        # α=0.05, 80% power → 7.85
```

With `σ_d ≈ 0.06` (working assumption — **replace it with your empirical σ after the first 100 markets**):

| Real edge (δ = mean Brier improvement) | Interpretation | Nominal `n` |
|---|---|---|
| 0.020 | Large edge, barely credible | ~71 |
| 0.010 | Strong edge | ~283 |
| 0.005 | Moderate, realistic edge | ~1,130 |
| 0.002 | Marginal edge | ~7,064 |

**Clustering correction:** markets are not independent. All of a given day's MLB games share shocks; "NOAA names the Nth storm" markets are the same bet sliced up. Effective `n` is typically **1/3 to 1/5** of nominal.

> **Number to internalise:** detecting a realistic edge needs on the order of **3,000-5,000 resolved markets**. At 20-40 relevant markets per day, that is **4-8 months of forward-testing**. Any conclusion drawn from 200 observations is noise — including the video bot's 223.

---

## 6. Gates

None are skipped. Each is a binary condition written down before running it.

**G0 — Stable ingestion**
Snapshots accumulating ≥14 days with no gaps >15 min. Daily reconciliation: number of resolved markets captured vs. listed in Gamma. Coverage ≥98%.

**G1 — Data sanity**
Compute the *market's own* Brier at T-24h per category. It must land in a plausible range (~0.10-0.18). If it comes out at 0.25 you have timestamp misalignment; if it comes out at 0.02 you are reading post-resolution prices.

**G2 — Null model (the gate that catches the most bugs)**
Feed `p̂ = m` (the mid itself) into the scorer. **`BSS` must come out exactly 0.000.** If it doesn't, your scoring code is broken — usually timestamp misalignment between the prediction and the price you compare it against. This test costs 20 minutes and saves you months.

**G2b — Permutation test**
Shuffle the `y_i` randomly and re-run. The model must show no skill. If it does, there is leakage.

**G3 — Statistical gate**
In **one pre-declared category**: `mean(d) > 0` with a clustered bootstrap 95% CI excluding zero, effective `n` sufficient per §5, and the improvement **not concentrated in the tail buckets**.

**G4 — Economic gate**
Simulation against real book snapshots: positive PnL net of spread and capped by depth, with a pre-declared maximum acceptable drawdown. Survives cutting half the signals via the no-trade filter.

**G5 — Capital**
Only here. And with two further conditions: (a) sizing comes from **fractional** Kelly (¼ Kelly maximum, never full), because Kelly assumes your `p` is correct and yours isn't; (b) an automatic, non-negotiable drawdown kill switch.

---

## 7. Prediction-market-specific traps

- **Disputed resolutions (UMA).** Exclude or flag via `was_disputed`. A market that resolved against the obvious reading contaminates the score.
- **Wording ambiguity.** Part of the apparent edge is reading the resolution criteria's fine print better, not predicting the world better. That is a real edge but it doesn't scale and it isn't the one you think you're measuring.
- **Favourite-longshot bias.** Prediction markets systematically overprice the tails. A model that merely shrinks toward the centre (`p̂ = 0.9·m + 0.05`) beats the market on Brier with zero domain knowledge. **Include it as a mandatory baseline** — if your sophisticated model doesn't beat this one, you have done nothing.
- **Markets resolving before `end_date`.** They break the T-24h horizon. Detect and handle separately.
- **Illusory liquidity.** The book can show depth that vanishes when you hit it.

---

## 8. Mandatory baselines

Your model competes against these, not against 0.25:

1. `p̂ = m` (market price) — BSS = 0 by definition
2. `p̂ = 0.9·m + 0.05` (shrink to centre) — captures the longshot bias
3. `p̂ = historical base rate for the category`
4. Sports: `p̂` derived from bookmaker closing lines
5. Weather: `p̂` derived directly from the GEFS ensemble with no adjustment

If the LLM-based model doesn't beat baseline 2 and baseline 5, the honest conclusion is that the LLM adds nothing.

---

## 9. Suggested implementation order

1. Read-only Gamma + CLOB client, with caching and rate-limit handling
2. Snapshot scheduler with adaptive cadence (dense at T-24h ±1h and T-1h, sparse otherwise)
3. Writes to date-partitioned Parquet + Supabase tables
4. Resolution watcher
5. **Scoring module and G2 before writing any model**
6. Baselines 1-3
7. First domain feature builder
8. Clustered bootstrap and reporting

Step 5 comes before any model deliberately. A scorer that works and that you trust is worth more than ten models.
