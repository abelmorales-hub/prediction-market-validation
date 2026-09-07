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

### Amendment 2 — 2026-09-03, T-24h dense window narrowed to ±15 min

**Changes what G0 measures. Read the retroactive note below before citing any G0 result.**

The T-24h dense half-width went from ±1h to ±15 min at approximately 19:36 UTC.
Permitted by §1, which reserves the spent sample for design decisions including cadence.

**Why.** The sweep must finish inside its own dense interval. Tracking 400 markets put a
projected **224** in a dense window simultaneously, which at a measured 0.21 s per book
is a ~47 s sweep against a 60 s cadence — 78% of budget, before any retry. The venue's
TLS resets take ~60 s to recover, so a single reset inside a dense window would overrun
and produce exactly the CRITICAL gap G0 fails on. After the change the projected peak is
**127 markets, ~27 s, 44% of budget**.

**What it costs.** Book depth far from the horizon, which a later economic simulation
(G4) may want. It costs nothing the pre-declared endpoint uses: a horizon is represented
by the nearest snapshot at or before it within five minutes, so fifteen minutes of
one-minute samples brackets it three times over. `test_the_t24_window_stays_wider_than_
the_emit_tolerance` pins that relationship so the window cannot later be narrowed below
what the scorer needs.

**This is mitigation, not immunity.** A full 60 s TLS recovery landing inside a dense
window still overruns a 60 s cadence. The change buys slack — roughly 33 s instead of
13 s — not a guarantee.

**Retroactive effect, and a decision not to benefit from it.** `WINDOWS` is also what
`gap_report` uses to decide which gaps are dense, so this reclassifies history. The
16:33–17:07 UTC period that Amendment 1 excluded for containing 4 CRITICAL gaps now
measures **0 CRITICAL**, because those holes fell in the T-24h ±1h region that is no
longer dense.

**The window start is not moved back to 16:33 UTC.** The exclusion stands on the
definition in force when it was made; the period genuinely had degraded collection
(72.8% dense capture against 99.1% since); and reversing an exclusion so as to *add*
data, after changing the measurement that justified it, is the precise manoeuvre this
document exists to prevent. Exclusions may be tightened by a definition change. They are
not loosened by one.

**Consequence for reporting.** G0 results computed before and after this change are not
comparable, and any G0 claim must name the cadence definition in force when it was
measured. Current status, measured under the new definition: window open since
2026-09-03 17:07:40 UTC, **0 CRITICAL, 6 MISSED (sparse only), 99.1% dense capture.**

### Amendment 3 — 2026-09-03, daily collection band; G0 and the universe restricted

**This is the largest change to the study so far. It narrows what can be claimed.**

**Why.** The collector runs on a desktop that will not be left on for fourteen
consecutive days. G0 as written — no gap over fifteen minutes, continuously — is
therefore unachievable, and a criterion nobody can meet is not a criterion, it is an
excuse to ignore the gate.

**The band.** Collection runs **05:30–11:00 Europe/Madrid, daily**, and the machine is
woken from sleep for it. This is not an arbitrary convenience window. Every Kalshi daily
temperature market closes just after midnight US local, so its T-24h horizon lands at
~01:00 US local, which is 07:00–10:00 in Madrid across the four US timezones:

| horizon, Madrid | US local | cities |
|---|---|---|
| 07:00 | 01:00 EDT | Miami, New York, Philadelphia, Atlanta, Boston, Washington DC |
| 08:00 | 01:00 CDT | Chicago, Austin, Dallas, Houston, Minneapolis, Oklahoma City, San Antonio |
| 09:00 | 00:00 MST/01:00 MDT | Denver, Phoenix |
| 10:00 | 01:00 PDT | Los Angeles, Las Vegas, Seattle, San Francisco |

A 05:30–11:00 band brackets **188 of 189** observed weather horizons, with margin for
the dense window opening fifteen minutes ahead of each.

**The band width is a research parameter, not a convenience.** Shrinking it does not
thin the sample evenly, it deletes whole timezones: an 08:00 start loses every East
Coast city, a 09:00 start loses the Central states too. For a study about forecasting
weather, biasing the sample by climate region is close to the worst available bias. The
band must not be narrowed for convenience.

**G0 is redefined.** From "no gap over fifteen minutes for fourteen consecutive days" to
**"no CRITICAL gap inside the collection band, for fourteen consecutive days."**
Implemented as `gap_report --band 05:30-11:00 --tz Europe/Madrid`, which measures dense
and sparse coverage only over in-band minutes. Being switched off outside the band is a
declared scope limit; a hole *inside* it is still a missed horizon and still voids the
window. Stopping early — last sample at 09:00 with the band open until 11:00 — is a
fault, not a scope limit, and is reported as one.

**The universe is now conditional**: markets whose T-24h horizon falls inside the band.
This is selection on close time, a market attribute fixed before listing and independent
of any outcome, so it does not bias the comparison between a forecast and the market
price at the same instant. It does restrict *what population the result generalises to*,
and that restriction must appear in any statement of a result.

**What is given up.** Non-weather categories are largely abandoned: 413 of the 914
tracked markets have T-24h horizons clustered at 23:00 Madrid, entirely outside the
band, and they will no longer be sampled at their horizon. The study becomes, in
practice, a weather study — which is where the pre-declared category and H2 already
were, but the option of a second category has now been spent rather than merely unused.

**Operational limits, stated because they will be the cause of the next gap.** Wake-to-
run resumes from sleep, not from a full shutdown; a machine powered off overnight needs
BIOS wake-on-RTC instead, and nothing in the pipeline can detect the difference. The
snapshotter holds a Windows execution-state lock while it runs, which prevents idle
sleep but cannot prevent a lid close, a manual sleep, or a shutdown.

### Amendment 4 — 2026-09-04, the band ends at 11:30, not 11:00

**Supersedes the times in Amendment 3.** Amendment 3 was written against a 5.5-hour
band. Task Scheduler rounds a repetition duration up to the hour, so the collectors
actually re-fired until 11:30 while `--until` and the sleep task assumed 11:00. That was
reconciled in code the same evening by making the band a whole **six hours** — the
document was not updated with it, and this amendment is that correction.

**Operative band: 05:30–11:30 Europe/Madrid.** The G0 command in Amendment 3 is
therefore wrong as written and must be run as:

```bash
gap_report --since <window start> --band 05:30-11:30 --tz Europe/Madrid
```

Judged with the documented `05:30-11:00`, every sample between 11:00 and 11:30 falls
outside the declared window: coverage would be measured against a band the collector was
not keeping.

**No effect on what is captured.** The last horizon is 10:00 local and its dense window
closes at 10:15, so the extra thirty minutes is slack, not coverage — the 188-of-189
figure in Amendment 3 stands unchanged. This corrects the record, not the sample.

**How it was found:** an audit of the repository against the project's own review rules,
not by a failing run. A band that disagrees with its own specification produces no error
anywhere; it just quietly answers a different question than the one that was declared.

### Amendment 5 — 2026-09-05, H2 exists and is frozen

**Supersedes the "no forecasting model exists" premise of §7.** The point-in-time GEFS
ingestion §7 named as the remaining work has been running since 2026-09-03, and the model
that consumes it now exists.

**The model.** `model_version = gefs_ensemble_raw`, frozen at commit **`782bb44`**
(`src/models/weather.py`, `src/analysis/weather.py`). It is §4's baseline 5 and nothing
more: the share of GEFS members whose forecast maximum settles the rung YES, unadjusted
for the station. It answers the market's own strike, read structurally from `strike_type`
+ `floor_strike`/`cap_strike`, never from the question text.

**Freezing is a claim about two files, not about HEAD.** Predictions are stamped with the
`git_sha` at run time, so rows emitted after this amendment will carry a later sha than
`782bb44`. That is not drift as long as the model itself has not moved, which is
checkable rather than asserted:

```bash
git log 782bb44..HEAD -- src/models/weather.py src/analysis/weather.py   # must be empty
```

A non-empty result means the frozen model changed mid-study. If it ever needs to change,
that is a **new `model_version` and a new amendment**, not an edit — the append-only
`predictions` table would otherwise hold two different models under one name.

**Three modelling choices, declared here because they are arguable and because declaring
them after seeing a score is worthless.**

1. *Members are rounded half-up before comparison.* The market settles on a whole-degree
   observation, so the rounding is part of the question rather than presentation. Python's
   bankers' rounding would send 94.5 down and 95.5 up, biasing alternate buckets in
   opposite directions up a ladder — invisible in any aggregate.
2. *Per-rung Laplace smoothing, `(k + 1) / (n + 2)`.* A 31-member ensemble votes
   unanimously on most rungs of a six-rung ladder, and the raw share is then exactly 0 or
   1, which the `0 < p_hat < 1` constraint refuses. Dropping those rows would leave a
   sample consisting only of near-the-money rungs — where the market is sharpest — which
   is a selection effect that would present as a result. The consequence is that a ladder
   sums to `(n + rungs) / (n + 2)`, not 1. That is correct for a study which scores each
   rung as its own binary market against its own price; normalising across the ladder
   would couple observations the bootstrap treats as independent.
3. *No filter on `is_pure_forecast`, and `KXHIGH` only.* That flag marks records fetched
   after the target day had begun locally, and T-24h for these markets lands near 01:00
   local **on** the target day — so filtering on it would reject essentially every T-24h
   forecast and leave H2 with no sample. At 01:00 the day's maximum lies entirely in the
   future, so no observed weather can have entered the number. This argument is about the
   maximum only; `KXLOW` is therefore declined rather than forecast, and counted as
   declined.

**What the window before G0 is for, and what it is not for.** §7 says accrual toward the
pre-committed n begins when G0 passes, and that is unchanged: rows stamped before
**2026-09-17** are excluded from the G3 sample. Emitting now is not accrual, it buys three
things that cannot be bought later, because predictions cannot be backfilled:

* a measured `σ_d` for a model that actually differs from the market, which §6 requires
  before the power calculation means anything;
* bug shakeout on throwaway data rather than on the counted sample;
* an **in-time training split** for the station-bias correction. That correction will be a
  separate `model_version`, fitted only on data stamped before 2026-09-17 and frozen in
  its own amendment before that date. Fitting it on the counted window would be leakage.

**`σ_d` remains 0.06 for planning.** The measured 0.0176 of §6 is still an artifact of
`baseline_shrink`. `gefs_ensemble_raw` disagrees with the market by 10–20 points per rung
on the first live comparison, so its `σ_d` will be a real quantity — but it is not
measured yet, and the planning assumption does not move until it is, in a dated amendment.

**What must not happen before the single G3 run.** H2's Brier score, BSS, or any
comparison of it against the market must not be inspected. The pre-G0 window is for the
three items above and for nothing that looks like an answer. §4's single run at the
pre-committed n stands.

### Amendment 6 — 2026-09-07, σ_d measured without outcomes; n pre-committed

**Supersedes the planning assumption of §6 and the accrual arithmetic of §7.**
Amendment 5 promised σ_d "in a dated amendment" once H2 produced predictions that
actually differ from the market's. It has produced 65 of them, all at T-24h, across 25
event families, between 2026-09-05 04:59 UTC and 2026-09-07 07:59 UTC. This is that
amendment.

**σ_d = 0.2020.** Not 0.06, and not the 0.0176 artifact §6 warned about. It is 3.4× the
planning assumption, and since n scales with σ², the required sample is **11× larger
than §7 projected**.

**How it was measured without touching an outcome.** Scoring the paired differences on
resolved markets would have produced σ_d and, in the same breath, mean(d) — which is the
G3 answer, and spending it early is precisely what §4's stopping rule forbids. So σ_d is
derived from the (p̂, m) pairs alone, under the null that the market is calibrated,
y ~ Bernoulli(m). With `d = (p̂ − y)² − (m − y)²` and `D = p̂ − m`:

    E[d | m]   = D²                     a market that is exactly right costs H2 D²
    Var[d | m] = 4 D² m (1 − m)         since d(y=1) − d(y=0) = −2D

and the law of total variance gives σ_d = √(mean(Var[d|m]) + var(E[d|m])). The
computation is `scripts/sigma_d.py`, whose query joins `predictions` to `markets` and
**never to `outcomes`**; its `--selfcheck` verifies the closed form against a Monte Carlo
of its own definition. The null supplies the outcome distribution, which is what a power
calculation is entitled to assume. If the market is not calibrated the true σ_d differs —
but that is the alternative, and planning under the null is the conservative direction.

**Planning assumption is now σ_d = 0.20**, and the §6 effect-size grid is retired:

| δ | nominal n | after ×3–×5 clustering |
|---|--:|--:|
| 0.020 | 801 | 2,403 – 4,005 |
| 0.010 | 3,204 | 9,612 – 16,020 |
| 0.005 | 12,816 | 38,448 – 64,080 |
| **0.047** | **143** | **429 – 715** |

**The primary effect size is 0.047, and the §7 target of δ = 0.010 is abandoned as
unreachable.** 0.047 is `mean(D²)`: what H2 loses per observation if the market is
exactly right. It is a property of the model's own disagreement — mean |p̂ − m| is 0.165,
max 0.549 — not a number chosen from results, and it is the least this model can
plausibly be moving. A model that bets 16 points away from the price is not testing a
0.005 hypothesis, and §6's grid was written before any model with real disagreement
existed. At 0.010, the honest answer is that Kalshi's daily weather ladders do not
produce 10,000–16,000 T-24h observations on any timeline this project has; declaring that
now, blind, is the point of a pre-declaration.

**Pre-committed n: 715 T-24h observations in Climate and Weather, stamped on or after
2026-09-17.** Nominal, not effective. Effective n is derived from the scored differences
themselves (`clustered_bootstrap.effective_n` is a function of `d`), so triggering the
single run on it would require looking at `d` to decide whether to look at `d`. A nominal
count is outcome-blind: it can be checked daily without spending anything. 715 is the ×5
end of the range, so it clears effective n = 143 even under worse clustering than the 2.6
observations per family seen so far.

**Accrual, measured rather than assumed.** 34, 21 and 10 observations on 2026-09-05/06/07
— roughly 20 a day, in a collection band of six hours, from 113 tracked markets. 715
observations is therefore about **36 collection days after G0 opens**, putting the single
G3 run near **2026-10-23** rather than the "2–6 weeks" of §7. Nothing about that timeline
is at risk from σ_d being wrong in the optimistic direction; it is at risk from days lost
to sleep, which `gap_report` already counts.

**Open, and to be settled before 2026-09-17, not after.** Accrual is the binding
constraint now, and the universe filter is the only lever that is not a model change: the
collector tracks 113 of 1,231 open markets after Amendment 3's volume ≥ 1,000 cut, and
H2 declines a further 6–9 series per pass as unsupported. Widening either would raise
accrual. Widening the *universe* is a collection decision and may be amended; widening
what H2 *forecasts* is a new `model_version` under Amendment 5's rule. Whichever is
chosen must be declared before G0 opens, because a universe that grows mid-window is a
different study.

### Amendment 7 — 2026-09-07, the universe is series-driven, and G0 restarts

Written hours after Amendment 6, which asked how to raise accrual. The answer turned out
to be that accrual was never limited by the volume cut. It was limited by a broken
sweep, and by the time it was found the sweep had stopped returning anything at all.

**What broke.** `/markets?status=open` is ordered such that `KXMVECROSSCATEGORY-SHARD1-*`
combo rows can occupy every page. On 2026-09-07 they occupied at least 12,000 consecutive
rows — past the 25-page cap the client applies because the listing is unbounded. Every
row the cap did fetch was a combo, the combo filter dropped all of them, and
`list_open_markets` returned `[]`. No exception, no failure counter: the collector saw a
venue with no open markets. `retain()` then held the already-admitted markets until they
closed, so capture kept flushing snapshots while admission was dead. Two hours earlier the
same call had returned 1,231 markets.

Nothing was lost — the 11:30 band had already ended and the fix landed the same day — but
the loss would have been the whole of 2026-09-08, discovered a day late at best.

**Why the obvious alternatives fail.** Paging deeper is unbounded and the shard count
grows. `/events?with_nested_markets=true` ignores `min_close_ts`/`max_close_ts` outright:
6,000 events contained 9 KXHIGH markets.

**The universe is now defined by series, not by a venue-wide scan.** Admission queries
`/markets?series_ticker=…&status=open` for each series the catalogue lists in the target
category, which is the only form of the query that cannot be crowded out. This is the
same inversion that fixed `list_recently_resolved` on 2026-09-03, and the second time the
identical mistake has cost this project data. **An empty sweep is now a counted failure**,
because the expensive part of this outage was not the bug, it was the silence.

The universe is therefore: *open markets in series the catalogue has already seen in
Climate and Weather, closing within 30 hours, volume ≥ 1,000, capped at 400.* A series
never catalogued is never collected. That is a real limitation, accepted deliberately: a
fixed and stated universe is worth more than one that silently grows or empties.

**This makes the universe bigger, not smaller.** Measured immediately after the fix: 292
open weather markets within 30 h, 164 above the volume cut, 114 H2-eligible. The
venue-wide sweep it replaces reached 113 markets above the cut **across all categories**.
The 5,000-row cap had been truncating weather accrual all along, which is the honest
explanation of §7's 20 observations a day.

**Amendment 6's open question is answered, and the answer is to change nothing.**
Lowering the volume cut from 1,000 to 0 raises H2-eligible markets from 99 to 114 — 15%,
against a wider-spread sample whose mid is a worse estimate of the market's belief, and
whose contamination would land on H4's territory rather than H2's. **`min_volume` stays
at 1,000.** Widening what H2 forecasts remains a new `model_version` and is not taken.

**G0 restarts at 2026-09-07.** The collector changed materially today; a 14-day
demonstration that it holds a clean window cannot be assembled half from the old one.
Earliest completion moves from 2026-09-17 to **2026-09-21**, and rows stamped before that
are excluded from the G3 sample in place of the 2026-09-17 cut-off named in Amendment 5
and §7. This costs four days and is the conservative reading; the alternative is claiming
a clean window for a collector that did not exist for most of it.

**The 715 stands; the date does not.** Amendment 6's pre-committed n is unchanged, and so
is σ_d = 0.2020 — the disagreement it measures is a property of the model, not of how many
markets are swept. But the ~20/day accrual behind its 2026-10-23 projection was measured
through the broken sweep and is now meaningless. **No new completion date is declared
here.** It will be measured over the first clean week after 2026-09-21 and recorded in its
own amendment. Guessing it now, from one post-fix sample taken on the day of the fix,
would be exactly the kind of number this document exists to prevent.

### Amendment 8 — 2026-09-07, the G0 restart is withdrawn; the real fault is 09-04

**Supersedes Amendment 7's G0 restart, written hours earlier the same day.** That restart
was wrong, and the reason it was wrong is worth recording: it reasoned from "the collector
changed" to "the window is void" without asking what G0 actually certifies.

**G0 certifies an operational property, and admission is not part of it.** The gate is
that the machine wakes, the scheduled tasks fire, and ticks land inside the band. The
2026-09-07 change replaced *which markets are admitted*; it did not touch the wake lock,
the band, the tick loop, or the cadence. It was also deployed at 20:13 local, nine hours
after the band closed, so no band was interrupted by it. A window is not void because the
set of markets inside it changed — only because a horizon inside it went unsampled.
**Earliest G0 completion returns to 2026-09-17 as far as this change is concerned.**

**But the window is not clean, for an unrelated reason found while checking this.** Run
as Amendment 4 specifies, over the whole declared window rather than the last day:

```bash
gap_report --since 2026-09-03T18:33Z --band 05:30-11:30 --tz Europe/Madrid
```

**G0 NOT MET: 7 CRITICAL in-band dense misses.** All seven share one timestamp —
**2026-09-04T10:33:55** — so it is one stoppage, not seven faults. The collector logged a
healthy sweep at 10:33 and flushed 361 snapshots at 10:34–10:35, then stopped, with the
band open until 11:30. Amendment 3 is explicit that this is a fault and not a scope limit:
"Stopping early — last sample at 09:00 with the band open until 11:00 — is a fault."

**The cause was deployment, not the machine.** The band feature was being installed that
day (`4dcdbe3`, `32be461`, `5e637a7`, `b45d18f` are all 2026-09-04), and
`supervisor.ps1 -Install` uses `-Force`, which stops running jobs. The known trap, paid
for. Capture over the window is 97.6% across 1,130 markets and every other day is clean:
the wake-and-register machinery is working, exactly as claimed.

**Why it stayed hidden for three days, which is the more serious finding.** `gap_report`
defaults to `--days 1`. Run that way it answers "was yesterday clean", and it answered
"G0 clean" every day while a fault sat three days back inside the same declared window.
G0 is a property of the *whole* window and can only be tested with `--since`. The script
now says so when `--since` is absent, in the same terms as the existing `--band` warning —
both are the same failure: a gate quietly answering an easier question than the one
declared.

**Operative dates.** The G0 clock starts after the last CRITICAL, so fourteen consecutive
clean days run from **2026-09-05** and complete on **2026-09-19**. Rows stamped before
2026-09-19 are excluded from the G3 sample, replacing both the 2026-09-17 of Amendment 5
and the 2026-09-21 of Amendment 7. Amendment 6's n = 715 is unchanged.

**One thing this does not do.** Six of the seven affected markets are sports and
economics, which Amendment 7's weather-only universe no longer tracks. That does not
retroactively excuse the fault — G0 as declared covers what was tracked at the time — but
it does mean the same stoppage would cost one market rather than seven from here on.
Nothing about that is a reason to relax the gate.

**Standing rule, since this is now the third gate to have quietly answered the wrong
question** (the band that read 05:30–11:00, the sweep that returned nothing, this):
before any run of `gap_report` is treated as a G0 verdict, it must be the full command
above, with both `--since` and `--band`. A verdict from a shorter invocation is not a
verdict.

### Amendment 9 — 2026-09-07, New Orleans added to the station list

The wider universe of Amendment 7 exposed a city H2 was declining for no stated reason.
`KXHIGHTNOLA` — five rungs a day — had no `settlement_station`, so H2 returned nothing
for it. Unlike `KXLOW`, which Amendment 5 declined on an argument, this was an omission:
the station list simply did not contain New Orleans.

**`CLIMSY`, New Orleans (Louis Armstrong), America/Chicago.** The code was read from the
market's own `rules_primary` — *"the maximum temperature recorded at New Orleans
(CLIMSY)"* — not inferred from the city name. That distinction is the whole reason the
station list exists: `CLIMDW` is Midway and `CLINYC` is Central Park, and a wrong guess
produces a confident forecast for the wrong place.

**This is not a new `model_version`, and here is why.** Amendment 5 froze
`src/models/weather.py` and `src/analysis/weather.py`, and both are untouched —
`git log 782bb44..HEAD` over them is still empty. `src/ingest/stations.py` is ingest, not
model: the forecasting logic is unchanged and now simply receives a station it previously
lacked. But it *does* change which markets H2 answers, which is why it is declared here
rather than done quietly, and why it is done before G0 completes rather than after.

**Effect.** H2's coverage goes from 100 of 105 catalogued open `KXHIGH` markets to
**106 of 106**, with `no_station_or_day` at zero. Roughly five observations a day, about
5%. Small, and taken because the cost was one line whose only risk — the wrong station —
was removed by reading the rules text instead of guessing.

**One limitation, stated rather than discovered later.** New Orleans has no GEFS ensemble
history before 2026-09-07, because the fetcher only collects stations in this list and
ensembles cannot be backfilled. NOLA therefore contributes nothing to any window opening
before that date. Since counted accrual does not begin until 2026-09-19 (Amendment 8),
this costs the study nothing.
