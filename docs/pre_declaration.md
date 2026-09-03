# Pre-declaration

**Written 2026-09-03, against 65 scored observations.** Everything below is fixed
before the data that will test it exists. That is the only property that makes it
worth anything: a hypothesis chosen after looking at the rows it is tested on is not
a hypothesis, it is a description.

Amendments are allowed and must be dated, justified, and appended at the bottom —
never edited in place. An undated change to this file invalidates every result that
cites it.

---

## 1. What the current sample may and may not be used for

The 65 observations collected on 2026-09-01/02 are **spent**. They were used to build
and debug the scorer (G1, G2, G2b), and they have been looked at. They may be used
for:

- pipeline correctness — gates, calibration of the machinery, sanity checks
- design decisions: universe filters, cadence, which categories the collector reaches

They may **not** be used as evidence for or against any hypothesis below, and they are
excluded from the primary analysis window, which starts at the timestamp in §5.

## 2. Pre-declared target category

**Climate and Weather.**

Chosen on universe composition, not on outcomes. Of 721 catalogued markets across 145
event families:

| category | markets | families |
|---|--:|--:|
| **Climate and Weather** | 172 | **45** |
| Crypto | 195 | 18 |
| Commodities | 144 | 16 |
| Economics | 72 | 18 |
| Entertainment | 52 | 8 |
| Financials | 48 | 16 |
| Sports | 17 | 11 |
| Politics | 11 | 4 |
| Science and Technology | 7 | 7 |
| Elections | 3 | 2 |

Families, not markets, are the unit of evidence (P5): Crypto has more markets but
fewer than half the families, because its markets are strike ladders on a handful of
underlyings that share every shock. Climate and Weather has the most independent
clusters by a factor of 2.5, and its markets have a genuine public information source
that is not the market price (§3, H2). No other category has both.

G3 permits exactly one pre-declared category. This is it. A result in any other
category is exploratory and must be labelled as such.

## 3. Candidate hypotheses

Each names its source. **None comes from inspecting the collected rows** — that is the
disqualifying origin.

### H1 — Favourite-longshot bias
*Source: design doc §7; standard result in the prediction-market literature.*

Prediction markets overprice tails. `p̂ = 0.9·m + 0.05` should beat the market on
Brier with zero domain knowledge.

- Already implemented as `baseline_shrink`.
- **Status: this is the bar, not the edge.** Doc §8: a model that does not beat this
  has done nothing. Its current −0.0096 BSS on the spent sample is uninformative.

### H2 — Public numerical weather prediction beats a thin weather market
*Source: design doc §8, mandatory baseline 5. Not derived from our data.*

GEFS ensemble output is public, updates on a fixed cycle, and is the actual generating
process for the events these markets settle on. A market with a wide spread and hours
between trades cannot be incorporating each ensemble run promptly.

- Prediction: at T-24h, a forecast read directly off the GEFS ensemble, with **no
  adjustment**, beats the market price on Brier in Climate and Weather.
- This is the primary hypothesis.
- Requires a GEFS ingestion path that does not exist yet, and it must be point-in-time:
  the ensemble run used must have been published before the prediction timestamp. Any
  shortcut here is leakage and G2b will not necessarily catch it (§4).

### H3 — Category base rate
*Source: design doc §8, mandatory baseline 3.*

`p̂ = historical base rate for the category`. A control, not a candidate edge. Exists
so that a model beating the market can be checked against the possibility that it is
merely exploiting an unconditional imbalance.

### H4 — Stale mid on untraded markets
*Source: venue mechanics observed while building the snapshotter, not from outcomes.*

The universe sweep found ~4,600 open markets of which ~870 clear a 1,000-unit volume
floor. A market whose book has not moved for hours has a mid that reflects older
information than the clock does.

- Prediction: paired difference is larger on markets whose `book_hash` is unchanged
  across the preceding N snapshots than on markets that have traded.
- **Secondary, and structurally suspect**: illusory liquidity (§7) means precisely
  these markets may be untradeable at the quoted price. Even if the statistical edge
  is real, G4 is where it should die. It is recorded here so that if it appears later
  it is not mistaken for a discovery.

### Explicitly out of scope
Anything found by re-running the report with different filters, any category selected
because it looked good, and any horizon chosen after seeing which one worked.

## 4. Primary endpoint and stopping rule

**Primary endpoint.** Mean paired Brier difference `d = BS_market − BS_model` for H2,
in Climate and Weather, at the T-24h horizon, with a clustered bootstrap 95% CI by
`event_family`.

**Success (G3).** `mean(d) > 0`; CI excludes zero; effective n sufficient per §5 of the
design doc; and the improvement survives excluding the tail price buckets. All four,
or it is not a pass.

**Failure.** CI includes zero at the target effective n, or the improvement is
concentrated in `[0, 0.05] ∪ [0.95, 1]`. Failure is a publishable result and closes
H2. It does not license a search for a different category.

**Stopping rule.** The analysis is run **once**, when the pre-committed effective n is
reached — not continuously as data accrues. Repeatedly testing a growing sample until
the CI excludes zero is how a null becomes a discovery. Interim runs are permitted only
for the health gates (G0, G1, G2, G2b), which say nothing about H2.

**Abandonment.** If G0 cannot be held for 14 consecutive days within a month, the
constraint is operational, not statistical, and the project pauses rather than
proceeding on a broken window.

## 5. Analysis window

> **SUPERSEDED BY AMENDMENT 1 (2026-09-03).** The instant below is in the wrong
> timezone and lands in a period containing CRITICAL gaps. Original text kept as
> written; see the amendment for the operative start.

Opens at the first snapshot after the supervisor was installed: **2026-09-03 18:33 UTC**.
Observations before that timestamp are excluded (§1).

The window is void for any period in which `gap_report` reports a CRITICAL gap. Because
the collectors run only while the machine is awake and logged in, this must be checked
before the analysis is run, not assumed.

## 6. Power, and a trap in the σ estimate

Design doc §5 assumes `σ_d ≈ 0.06` and says to replace it with the empirical value
after the first 100 markets. **Do not do that with the number currently available.**

Measured on the spent sample: `σ_d = 0.0176` for `baseline_shrink`. Taken at face value
it would cut the required n by a factor of ~12:

| δ (real edge) | n at σ=0.06 (doc) | n at σ=0.0176 (measured) |
|---|--:|--:|
| 0.020 | 71 | 7 |
| 0.010 | 283 | 25 |
| 0.005 | 1,131 | 98 |
| 0.002 | 7,064 | 611 |

That right-hand column is an artifact and must not be used for planning. `σ_d` is a
property of *the model being tested*, not of the data: `baseline_shrink` is a
deterministic near-identity map of the market price (`0.9m + 0.05`), so its paired
difference is mechanically tiny. A model with genuinely independent information — H2
certainly — will have a far larger `σ_d`, and planning against 0.0176 would leave the
study underpowered by an order of magnitude while looking rigorous.

**Planning assumption: σ_d = 0.06**, revisited only once H2 has produced predictions
that actually differ from the market's, and revised in a dated amendment below.

Applying the doc's clustering correction (effective n is 1/3 to 1/5 of nominal), a
strong edge (δ = 0.010) needs roughly **850–1,400 observations** in the target
category.

## 7. When there will be enough

Two independent clocks, and the later one governs.

**G0 — trustworthiness of the window.** Fourteen consecutive days without a gap >15
min, from 2026-09-03. Earliest completion **2026-09-17**, and only if nothing sleeps,
logs out, or crashes past a 15-minute tick.

**Accrual — enough independent clusters.** The first cohort produced 65 scored
observations and 19 clusters from 14 hours while tracking 105 markets; Climate and
Weather was 42 of those observations and 13 of the clusters. The collector now tracks
400. Even without assuming the extra markets scale linearly into families, the 850–1,400
observations that δ = 0.010 requires are **weeks of clean collection, not months** —
Kalshi lists far more short-dated markets than the 20–40/day the design doc assumed
when it projected 4–8 months.

So the binding constraint is not sample size. It is (a) holding G0 for two weeks, and
(b) H2 not existing yet — there is no GEFS ingestion path, and building one point-in-time
is the real work.

**Realistic sequence:**

| when | what |
|---|---|
| now → 2026-09-17 | G0 accrues. Build H2's point-in-time GEFS ingestion. No analysis. |
| 2026-09-17 | G0 checked. If broken, restart the clock — do not proceed on a partial window. |
| after G0 passes | H2 emits alongside the baselines. Accrual toward the pre-committed n begins **here**, not earlier. |
| ~2–6 weeks later | Single G3 run, per §4. |

Anything described as a hypothesis before H2 has a working point-in-time feed is a
hypothesis about code that does not exist.

---

## Amendments

### Amendment 1 — 2026-09-03, analysis window start

**Supersedes §5.** A pointer to this amendment was inserted at §5; that insertion and
this entry are the only changes, and §5's original text is left as written.

Two faults, one clerical and one operational.

**The stated instant was in the wrong timezone.** "2026-09-03 18:33 UTC" was read off a
local clock running CEST (+02:00). The supervisor was installed at 18:33 local, which is
**16:33 UTC**. As written, the window opened at an instant that had not yet occurred when
the document was committed — 18:33 UTC is 20:33 local.

**The period immediately after that instant is not clean.** `gap_report --since
2026-09-03T16:33Z` finds **4 CRITICAL gaps** — 35.9, 12.7 and 2.8 minutes — all between
16:33 and 17:07 UTC, and all self-inflicted: re-registering the scheduled tasks with
`-Force` stopped a running snapshotter, and a task action missing `-WindowStyle Hidden`
died with `STATUS_CONTROL_C_EXIT` when the shell that started it exited. §4 makes the
window void for any period containing a CRITICAL gap.

**The window therefore opens at 2026-09-03 17:07:40 UTC**, the last clean collector
restart. `gap_report --since 2026-09-03T17:07Z` reports 0 CRITICAL gaps and a 94.3%
dense-window capture rate.

Moving a window start is exactly the manoeuvre this document exists to prevent, so the
grounds are recorded explicitly: **nothing in the excluded period has resolved.** The
earliest tracked weather market settles the following day, no outcome from 16:33–17:07
UTC is known to anyone, and the exclusion is therefore made on operational evidence that
existed before any result. It also makes the study strictly smaller, never larger. Had a
single observation in that period already resolved, the honest move would have been to
keep the original instant and declare the window void.

`scripts/gap_report.py` gained `--since` in the same change, because a rolling `--days`
window cannot evaluate a fixed analysis window: it fails on holes that predate the study
and are already excluded from it.
