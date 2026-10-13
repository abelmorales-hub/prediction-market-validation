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

### Amendment 10 — 2026-09-13, G0 counts clean band-days, not consecutive calendar days

**This amendment loosens a gate after that gate failed, which is the move pre-declaration
exists to prevent. It is written first so it cannot be read as anything else.** What
follows is the argument for doing it anyway, and the price paid for it. Amendment 8
revised a G0 judgement too, but revised it *toward* rigour; this goes the other way, and
that asymmetry is the reason for the length of this entry.

**What happened.** On 2026-09-13 the machine woke for the band at 05:29:31 and came up
with no network. Every scheduled tick for six hours died identically —
`psycopg.OperationalError: failed to resolve host 'aws-1-eu-west-1.pooler.supabase.com'`,
125 of them across `pmv-snapshot`, `pmv-emit`, `pmv-gefs` and `pmv-resolutions`. A
DNS-Client timeout at 05:30:08 names an unrelated host, so this was the machine's
connection and not anything the study owns. No `data/snapshots/date=2026-09-13/`
directory was ever created. Predictions and GEFS ensembles for that day are gone
permanently; resolutions are backfillable.

**`pmv-g0-check` reported "G0 clean", exit 0, that morning.** `logs/g0-20260913.log` came
out byte-identical to the previous day's but for the header timestamp. That is the
finding that matters more than the outage.

**Why the gate could not see it, which is structural and not a misconfiguration.** A gap
is the interval *between two consecutive snapshots of one market*. A day that captures
nothing produces no snapshots, therefore no pairs, therefore no gaps. `find_gaps` can
only find holes between rows that exist; it is constitutionally unable to notice rows
that do not. `gap_report.py` compounded it by returning **0** on an empty range with "No
snapshots in range. Nothing to check." **Fourth gate in this study to quietly answer an
easier question than the one declared**, after the band that read 05:30–11:00, the sweep
that returned nothing, and the `--days 1` default. The signal was sitting in plain sight
and unused: `Get-ScheduledTaskInfo` showed `LastTaskResult = 1` on all four collector
tasks.

**The distinction the old rule did not make.** Amendment 3 fixed G0 as "no CRITICAL gap
inside the band, for fourteen consecutive days", and Amendment 8 restated what it
certifies: the machine wakes, the tasks fire, ticks land in-band. Under that text
2026-09-13 is a fault and the clock restarts to 2026-09-14, completing 2026-09-27. But
the two faults this study has seen are not the same kind of thing:

| | 2026-09-04 | 2026-09-13 |
|---|---|---|
| what happened | stopped 57 min before band close | zero rows for the whole band |
| rows in the sample from that day | yes, with a hole near horizons | none |
| can it corrupt an observation? | **yes** — a forecast paired with a stale price | no — nothing exists to corrupt |
| what it costs | integrity of the window | *n*, and nothing else |

§7 calls G0 "trustworthiness of the window". A day contributing zero rows cannot make the
window untrustworthy; it can only make it shorter. A partial stop can, and did, which is
why Amendment 8's restart was right and this one is not automatic.

**The decisive argument is the abandonment clause.** §4 pauses the project if G0 cannot
be held for fourteen consecutive days *within a month*. Two faults in ten days, from
unrelated causes — a `-Force` install, a NIC that did not come back from S3 — is the
observed rate, and each all-or-nothing restart spends half the month. On that evidence
the old rule abandons a methodologically sound study for its home network rather than for
anything about its statistics. A gate that fails for reasons unrelated to what it
certifies is the same defect as a gate that passes for them.

**G0 is redefined, for the second and final time.** From "no CRITICAL gap inside the
band, for fourteen **consecutive days**" to:

> **Fourteen clean band-days within any eighteen-day span, with no CRITICAL gap on any of
> them.** A *band-day* is clean when at least one snapshot lands inside 05:30–11:30
> Europe/Madrid and no CRITICAL gap is reported for it; lost otherwise. A lost day does
> not count toward the fourteen and does not restart the clock.

**Two conditions still restart the clock**, so the counter cannot degenerate into
"fourteen days on which something happened":

1. **Two consecutive lost days.** The rig failing to recover unattended is precisely the
   operational property G0 certifies, and one bad morning followed by another is that
   failure rather than bad luck.
2. **The eighteen-day span elapsing with fewer than fourteen clean days.** Attrition slow
   enough to hide is still attrition.

A CRITICAL gap voids the window exactly as before. Nothing here softens that, and it is
worth being explicit: **this changes only the treatment of days with no data, never of
days with bad data.**

**Implemented, not merely declared.** `src/scheduler/gaps.py` gains `band_days()` — a
ledger of every local day whose band lies wholly inside the window, counting days that
should exist rather than holes between rows that do — and `g0_status()`, which applies the
rule above. `scripts/gap_report.py` prints the ledger before anything derived from the
rows, because every other number in that report is conditional on rows existing and this
is the only check that fires when they do not. Its empty-range path now returns **1**. The
report exits 1 when the most recently judged band-day is lost, so a dead morning reaches
`Get-ScheduledTaskInfo` the same day instead of three days later. `g0_check.ps1` needed no
change; it already propagates the exit code. Seven cases in `tests/unit/test_gaps.py`,
including the 09-13 shape, assert that `find_gaps` stays silent on it while the ledger
does not. **A rule this document declares and no code checks would have been the fifth
gate answering an easier question.**

**Operative dates.** The ledger over 2026-09-05 → 2026-09-13 reads eight clean, one lost
(2026-09-13), no restart. Fourteen clean band-days are therefore reached on **2026-09-19**
if every remaining day is clean, and rows stamped before **2026-09-20** are excluded from
the G3 sample — one day later than Amendment 8's 2026-09-19. **That one day is the entire
schedule effect of this amendment**, against the two weeks a restart would have cost.

**What this does not touch.** H2's frozen files (`git log 782bb44..HEAD` over
`src/models/weather.py` and `src/analysis/weather.py` is still empty), `model_version`,
Amendment 6's n = 715, σ_d = 0.2020, the effect size 0.047, the band, the universe, and
the single-G3-run stopping rule. No outcome was read in the course of writing this.

**The standing objection, recorded so it travels with the result.** This was written after
seeing the gate fail, by the party the gate constrains. The mitigations are that it lands
before G0 completes and before any counted accrual, that it tightens detection in the same
change as it loosens the reset, that the loosening is bounded by two explicit restart
conditions, and that the data-integrity criterion is untouched. Anyone weighing the final
result should read this amendment and Amendment 8 together and judge for themselves
whether the fourteen days were earned.

### Amendment 11 — 2026-10-08, accrual closes; the G0 verdict, and the sample the single run uses

**G0 is not met. 159 CRITICAL gaps stand in the window, and §5 says a window with a
CRITICAL gap in it is void.** This amendment decides what that does to the single G3 run,
and it is written by the party the gate constrains, after the gate failed, which is the
second time this document has had to say so. Everything below is measured without reading
a single outcome of the sample it defines; `git log 782bb44..HEAD -- src/models/weather.py
src/analysis/weather.py` is still empty.

**Accrual is complete.** Amendment 6 pre-committed **715 nominal T-24h observations in
Climate and Weather**. The count crossed 715 on **2026-10-06** and stands at **817**, in a
window that closes at the end of the last complete band before this entry:

> **The frozen sample is every H2 `gefs_ensemble_raw` T-24h prediction stamped in
> [2026-09-20T00:00:00Z, 2026-10-08T09:30:00Z).** 817 nominal observations, 276 event
> families, 100% Climate and Weather. The cut is a fixed instant: the sample cannot grow
> between this amendment and the run.

Cumulative, by day: 705 on 10-05, **761 on 10-06**, 810 on 10-07, 864 on 10-08 (the last
figure is measured to now, not to the cut). The 102-observation overshoot past the trigger
is an artifact of checking the count on 10-01 and again on 10-08 rather than daily.
Counting rows reads no outcomes, so nothing was spent — but a trigger nobody watches is a
trigger that drifts, and it is recorded here rather than left as a rounding detail.

**The G0 ledger, by gap-start day.** Capture 96.3% over 280,045 snapshots; 159 CRITICAL,
86 MISSED since 2026-09-05:

| day | CRITICAL | cause |
|---|--:|---|
| 09-17 → 09-20 | 90 | sweep stalls: one market's 8-attempt retry ladder blocking the whole sweep, fixed in `cf85486` on 09-21 |
| 09-21, 09-27, 09-28, 10-02, 10-05, 10-06 | 11 | residual TLS resets, 1–4 per day |
| 09-30 | 10 | the 2026-10-01 dead-resolver outage, seen from the evening before: `end_date` is the day *after* the forecast day, so the gap starts in-band at the 09-30 band end and runs to the 08:31 recovery |
| 10-04 | 48 | 22 `WinError 10054` resets in one band, against 5–13 on neighbouring days — the worst day since the fix, and not a regression of it |

**The operational half of G0 is met and the integrity half is not.** Under Amendment 10
the ledger reads 14 clean band-days reached **2026-09-29**, with only 2026-09-13, -14 and
-23 lost. The machine wakes, the tasks fire, the ticks land in-band. What fails is the
criterion Amendment 10 explicitly refused to soften: a CRITICAL gap voids the window.

**What a CRITICAL gap can and cannot do to an observation — checked, not argued.**
Amendment 10 distinguished a fault that costs *n* from a fault that corrupts an
observation. The same distinction applies inside the CRITICAL class, and here it is
decidable from the data: an observation exists only if `pick` found a snapshot **at or
before** the horizon and within five minutes, and `emit` stamps the prediction with that
snapshot's own timestamp. Measured over all 817 rows of the frozen sample, with the
horizon recomputed from `end_date` as stored today:

    lag = (end_date - 24h) - prediction ts
    min 0.11 min   median 2.54 min   max 3.20 min
    rows beyond the 5-minute tolerance:        0
    rows timestamped after their own horizon:  0

So **no observation in the frozen sample is paired with a stale price**, and no close was
moved after emission — that second check matters because `upsert_markets` refreshes
`end_date` on every catalogue pass, which could retroactively invalidate a sound pairing.
The 159 CRITICAL gaps cost *n* and bias *which* observations exist. They did not corrupt
one that does.

**Two exclusions, both of them tightenings, applied anyway.** Amendment 2's rule holds —
exclusions may be tightened by a definition change and are never loosened by one — so the
sample is cut further, not admitted wider:

1. Rows stamped before **2026-09-20**, per Amendment 10.
2. **Every market carrying a CRITICAL gap in its own dense windows**, whether or not that
   gap cost it an observation: 156 markets, listed in `docs/g3_excluded_markets.txt`,
   generated by `scripts/gap_report --critical-markets` and committed so the sample stays
   reproducible after the parquet grows. 39 of the 156 fall inside the window.

**The G3 sample is therefore 778 clean nominal observations over 263 event families**,
2.96 per family — looser clustering than the 2.6 Amendment 6 planned against.

**The exclusion is not a repair, and the residual is selection, not noise.** Excluding a
flagged market cannot recover an observation a stall already prevented from existing; it
only removes one that survived. **102 markets carrying a CRITICAL gap have no H2 T-24h row
at all** — an upper bound on observations lost to stalls, against 817 kept. Worse, the
loss is not uniform: the 39 flagged markets inside the window disagree with the market
*more* than the kept ones, median |p̂ − m| **0.200 against 0.137**. Stalls land at :33 and
:45, which is when the dense windows of the :00-closing ladders open, so they
preferentially cost the observations where H2 takes its largest positions. The direction
this pushes mean(d) is unknown — it removes both the model's best chances and its worst —
and it is unfixable from here. It travels with the result as a limitation on external
validity, and anyone reading the G3 number should read this paragraph with it.

**Why this is not the pause §4 provides for.** The honest case for pausing is that the
gate failed and this entry is the interested party reinterpreting it. Against that: the
property §5's void rule exists to protect is verified per observation above, by a check
anyone can re-run; the operational property G0 certifies is held; the dominant fault is
already fixed in code rather than argued away; and a per-day or whole-window void would
discard 372 to 778 observations whose integrity is individually checkable, to protect
against a corruption channel shown to be closed. **What would change this answer:** a
single row in the frozen sample outside tolerance, a close that moved after emission, or
any evidence that `pick`'s tolerance is not the binding constraint on what gets admitted.
None of the three is present.

**The single run, and the condition it waits on.** The pre-committed 715 is a count of
*observations*, and an unresolved market is not one. Of the 778 clean nominal rows, **675
have an outcome recorded**; the 103 that do not all close by **2026-10-09T08:00:00Z** and
settle within hours of their close. Therefore:

> **G3 runs when the frozen sample's resolved count reaches 715, and not before.** If it
> has not by **2026-10-11T00:00:00Z**, the run happens anyway on whatever has resolved,
> with the shortfall stated in the report. No further accrual is counted either way — the
> cut is fixed and nothing stamped after it enters the sample.

The command is fixed here so it cannot be tuned later:

    uv run python -m scripts.score --model gefs_ensemble_raw --horizon T-24h \
      --since 2026-09-20T00:00:00Z --until 2026-10-08T09:30:00Z \
      --exclude docs/g3_excluded_markets.txt --out reports/g3_h2.md

**Pass requires all four of §4's conditions**, unchanged: mean(d) > 0; the clustered
bootstrap 95% CI by `event_family` excluding zero; **effective n ≥ 143** (Amendment 6's
requirement at the pre-declared effect size 0.047 with σ_d = 0.2020); and the improvement
surviving the exclusion of the `[0, 0.05] ∪ [0.95, 1]` buckets. Failure closes H2 and is
publishable. It licenses no search for a different category, horizon or model.

**Enforced, not declared.** A rule this document states and no code checks would be the
fifth gate quietly answering an easier question. `src/analysis/frame.py` gains
`g3_sample()`, which applies the window, the exclusion list and the lag check as a row
filter *before* the pivot — the order matters, because `build_frame`'s completeness rule
would otherwise trim the frame to whatever survived — and fails closed on any row whose
horizon it cannot verify. `scored_pairs` now returns `end_date` so the check has something
to compute against. `scripts/score.py` grows `--since`, `--until` and `--exclude`, and
prints a `SampleCensus` whose arithmetic must reconcile: rows in, minus each rule's
casualties, equals rows admitted. Nine cases in `tests/unit/test_g3_sample.py`, including
a close that moves after emission and a snapshot that lands after its own horizon.

**Health gates, re-run on the frozen window with H2 withheld** (permitted by §4; they say
nothing about H2): `baseline_market` scores **exactly +0.0000** BSS with a
[+0.0000, +0.0000] CI in every stratum and every price bucket — G2 holds on precisely the
rows the G3 run will use, which is the one check that proves the pipeline has no bias of
its own. `baseline_shrink` loses, −0.0318 BSS over 972 observations and 251 families,
which is H1 behaving as §3 predicted: the bar, not the edge. Market ECE 0.049, AUC 0.900.

**What this does not touch.** H2's frozen files, `model_version`, σ_d = 0.2020, the effect
size 0.047, n = 715, the endpoint, the single-run rule, the band, the universe, and the
category. Nothing here is a model change and no new `model_version` is created.

**The standing objection, recorded so it travels with the result.** This amendment lets a
study proceed whose declared gate is red, and it was written by the only party who wanted
it to proceed. The mitigations are that it tightens the sample in both of its operative
rules, that the integrity claim it rests on is a measurement anyone can reproduce rather
than an argument, that the selection residual is stated rather than minimised, that the
run condition and command are fixed in advance, and that no outcome of the sample was read
in writing it. Read it with Amendments 8 and 10 and judge for yourself whether the window
was earned.

### Amendment 12 — 2026-10-11, the single run is spent: G3 fails and H2 is closed

The run declared in Amendment 11 was taken once, at **2026-10-11 17:06 UTC**, on the
frozen sample, with the command fixed there. The report is `docs/g3_result.md`. H2 does
not merely fail to beat the market — it is **far worse** than the market, and the result
is not close enough to be a question of power.

| stratum | n | families | eff. n | mean(d) | 95% CI | BSS | ECE | AUC |
|---|--:|--:|--:|--:|:--:|--:|--:|--:|
| overall | 778 | 263 | 746 | **−0.1008** | [−0.1185, −0.0839] | −0.7848 | 0.171 | 0.532 |
| tails excluded | 631 | 257 | 587 | **−0.1149** | [−0.1362, −0.0943] | −0.7264 | 0.210 | 0.479 |

**Against §4's four conditions: 1 fails, 2 fails, 3 passes, 4 is moot.** `mean(d) > 0` is
false and the sign is wrong, not small; the CI excludes zero *on the wrong side*;
effective n is 746 against the 143 Amendment 11 required, so the study had ample power and
used it to measure a loss; and there is no improvement for the tail exclusion to survive —
removing the tails makes H2 worse, which is the opposite of the longshot artifact §4.2
guards against. **Failure. H2 is closed.** Per §4 this licenses no search for a different
category, horizon or model, and in particular **no station-bias-corrected `model_version`
may be fit on this sample**: these 778 rows are spent, and nothing fitted on them can ever
be tested out of sample again.

**The mechanism is the one the design notes predicted, and the numbers identify it rather
than assume it.** Unadjusted grid output carries a station and elevation bias against The
Weather Company observation the market settles on. The signature is miscalibration, not
absence of signal: ECE 0.171 against the market's 0.049 on the same rows, while AUC is
0.532 overall and **0.389–0.479 inside every price bucket** — at or below chance once the
bucket has removed the market's own ordering. A model with the right information and the
wrong offset looks exactly like this. The −46.24 BSS in [0.00, 0.05] is the same fact at
its most expensive: 147 rows where the market says ~0, H2 says otherwise, and the market
is right.

**Two deviations from Amendment 11's run condition, both recorded, neither affecting the
sample.** The `pmv-g3` task never fired on 2026-10-10: **2026-10-10 and 2026-10-11 were
both total collection losses** (no in-band snapshot at all, machine not awake), and the
task carries `StartWhenAvailable = $false` by design, so the missed occurrence was not
caught up. The run therefore happened **17 hours past the 2026-10-11T00:00:00Z fallback
instant**, by hand, through `scripts/g3_run.ps1` — the declared instrument, which inlines
the command verbatim and has since unregistered itself. Second, the 05:30 resolution
sweeps of 10-10 and 10-11 did not happen either, so `watch_resolutions --catalogue` was
run first and wrote 263 outcomes. Both deviations move in the same harmless direction: the
cut at 2026-10-08T09:30:00Z is a fixed instant and nothing could enter the sample, while
the delay raised the **resolved** count from Amendment 11's 675 to **778 of 778** — every
clean nominal row now has an outcome, so the pre-committed 715 was met on resolved
observations and the fallback's shortfall clause is moot. Confirmed before the look by a
run with `--require-n 999999`, which refuses before `build_report` and so spends nothing:
it printed the census and 778, and scored nothing.

**What the limitations can and cannot do to this verdict.** Amendment 11's selection
residual stands — 102 flagged markets have no row, and flagged markets disagree with the
market more than kept ones (median |p̂ − m| 0.200 vs 0.137) — and its direction is still
unknown. It cannot plausibly account for a −0.10 mean paired difference whose CI clears
zero by 0.08 with an AUC at chance. The G0 ledger is red and Amendment 11's case for
proceeding anyway was written by the interested party; note that the window it bought
produced a null, which is the outcome that argument had the least incentive to produce.

**Publishable as a negative result**, which §4 declared in advance and which is the point
of the whole apparatus: a forecast a reasonable person would have expected to carry
information does not beat the price of a market that already knows the station bias. The
scorer, the gates, σ_d, the sample freeze and the refusal guard all worked; what they were
measuring did not.

### Amendment 13 — 2026-10-11, H3: the station-corrected ensemble, pre-registered

A new hypothesis, a new `model_version`, a new window, and a new host. §4 forbids searching
for a different category, horizon or model *after* a failure, and this does not do that: the
category, horizon, endpoint, benchmark and pass conditions are all unchanged. What changes is
one modelling defect that Amendment 12 identified and measured, corrected on data that can
never serve as a test set again.

**H3.** `model_version = gefs_station_bias_v1`. The GEFS ensemble with a constant per-station
offset added to every member before the strike arithmetic. `src/models/station_bias.py` (the
twenty offsets, generated) and `src/analysis/station_bias.py` (the forecaster) are H3's frozen
pair, exactly as `src/models/weather.py` and `src/analysis/weather.py` are H2's. Verify with
`git log <this amendment's commit>..HEAD -- src/models/station_bias.py
src/analysis/station_bias.py`, which must stay empty. Changing an offset is a new
`model_version` and a new amendment, never an edit.

The forecaster **inherits** `WeatherForecaster` rather than copying it, so H3 provably shares
H2's point-in-time rule — `latest_ensemble_before(station, settles_on, point.ts)`, the one line
where a look-ahead leak could enter — instead of a duplicate that could drift. The members
move, not the strikes: shifting floor and cap by minus the offset is equivalent only for a
whole-degree offset, because settlement is an integer and members are rounded half-up first.

**The training data, and the cutoff that matters.** 545 pinned station-days settling on or
before **2026-10-08**, fitted by `scripts/fit_station_bias.py`. Two sources, both unusable as a
test set and therefore free:

- days before 2026-09-20, never part of any sample (Amendment 10);
- days inside the frozen G3 window, spent — their outcomes have been read, so nothing fitted
  by anyone who has seen `docs/g3_result.md` can be tested on them again.

The target is the settlement temperature **read off the resolved ladder itself**, not from any
external feed: the ladder is the settlement expressed as inequalities, so intersecting them has
no proxy error against the quantity the venue actually paid on. One bug is recorded because its
canary is the only reason it is not in the offsets: keying ladders on `(station, settles_on)`
merges the day's *minimum* ladder with its maximum (`KXLOWTATL` beside `KXHIGHTATL`), and 379
of 649 ladders came out contradictory, with 6–13 °F spreads and a −7.75 °F Central Park offset
that looked like output. Filtering to `KXHIGH` takes contradictions to 0, days fitted to 545
and spreads to 1.3–3.4 °F.

The offsets are physically coherent, which is the first *independent* support for Amendment
12's diagnosis: Phoenix +3.5 and Las Vegas +3.1 (desert stations hotter than a 0.25° cell
average), SFO −2.6 and Central Park −1.75 (coastal and park stations cooler). Mean |offset|
≈ 1.6 °F against 2 °F-wide buckets is exactly how a forecast ends up confidently on the wrong
rung.

**Power, measured without outcomes.** `scripts/sigma_d.py --counterfactual` recomputes H3's p̂
from the stored ensembles against the prices already on H2's rows — p̂ is a pure function of
the ensemble record and the strike, so no new emission and no outcome is needed. Over 1,314
pairs in 452 families:

| | H2 | H3 |
|---|--:|--:|
| mean \|p̂ − m\| | 0.1900 | **0.1588** |
| σ_d | 0.2424 | **0.2092** |
| δ = mean(D²) | 0.0673 | **0.0509** |
| required effective n | 102 | **133** |

**σ_d = 0.2092 and δ = 0.0509 are pre-committed here.** The correction shrinks disagreement
with the market by only ~16%, so H3 still bets far from the price and the test stays easy to
power: 133 effective observations, against a design effect of 1.04 measured on the G3 sample.
One caveat in the conservative direction — the offsets were fitted on days this sizing sample
overlaps, so out of sample |p̂ − m| is likely *larger*, and n scales as its inverse square, so
the n below errs large.

> **The window is [2026-10-13T00:00:00Z, 2026-11-12T00:00:00Z) — thirty days.** The sample is
> every `gefs_station_bias_v1` T-24h prediction stamped inside it, in Climate and Weather, less
> any market carrying a CRITICAL gap in its own dense window. Rows stamped before the open are
> shakeout and excluded, the same way the pre-2026-09-20 rows were.
>
> **Pre-committed minimum n = 400 nominal observations**, which is §6's ×3 clustering inflation
> applied to the 133 rather than the measured 1.04 — conservative on purpose, because a design
> effect measured on one sample is not a promise about the next.

**Thirty days is not a power requirement and must not be defended as one.** At the observed
~46 observations/day the minimum n arrives in about nine days, and power alone would be
satisfied in three. The window is thirty days because the G3 sample's narrowness — eighteen
days of one season in twenty cities — is the limitation most likely to make a *positive* result
fail to replicate, and a nine-day window would reproduce it. The thirty days buy the autumn
regime change (US DST ends 2026-11-01, inside the window). Stopping early because n arrived
early would be the thing §4's stopping rule forbids, wearing a different hat.

**The run.** Once, at the window close, with the command fixed here:

    uv run python -m scripts.score --model gefs_station_bias_v1 --horizon T-24h \
      --since 2026-10-13T00:00:00Z --until 2026-11-12T00:00:00Z \
      --exclude docs/h3_excluded_markets.txt --out reports/h3.md --require-n 400

Pass requires all four of §4's conditions, unchanged: mean(d) > 0; the clustered bootstrap 95%
CI by `event_family` excluding zero; effective n ≥ 133; and the improvement surviving exclusion
of the `[0, 0.05] ∪ [0.95, 1]` buckets. Failure closes H3 and is publishable. `FROZEN_MODELS`
now withholds H3 as well as H2, so an unqualified scorer run cannot spend the look.

**H2 stops being emitted, and stays withheld.** It is closed; emitting it would only create
something to be tempted by, and re-reading a closed hypothesis on new rows is the "test until
the interval cooperates" move the single-look rule exists to prevent. Nothing is lost: H2's p̂
is recomputable from the stored ensemble record whenever a raw-versus-corrected comparison is
genuinely wanted, which is how the sizing above was done.

**The host changes, and G0's criterion changes with it.** Collection moves to GitHub Actions
(`.github/workflows/collect.yml`). The desktop lost 5 of 37 band-days and three of those were
the same unexplained failure to wake, including one unbroken 47-hour sleep across 10-10 and
10-11 — with `WakeToRun` and `RTCWAKE` both verified correct. A runner cannot sleep, so the
dominant failure class is removed rather than mitigated. Consequences for the gate:

- **The band is now 03:20–09:10 UTC**, two overlapping shifts, and G0 for this window is
  `gap_report --since 2026-10-13T00:00:00Z --band 03:20-09:10 --tz UTC`. Amendment 10's
  band-day accounting carries over unchanged.
- **UTC removes a seam nobody had noticed.** The old band was declared in Europe/Madrid local
  time, and Madrid leaves DST on 2026-10-25 — inside this window. The band would have shifted
  an hour against the market close times halfway through, silently.
- `--until` is now an absolute instant rather than a tick count, which fixes the defect behind
  the 09-20 and 10-01 overruns; Parquet part names carry a content token so two overlapping
  shifts cannot collide on a path; and the snapshot store is tracked, so its point-in-time claim
  is a GitHub commit timestamp instead of an assurance.
- **Days on the old host, if any, are recorded rather than excluded.** The window opens on a
  fixed instant and the Windows tasks keep collecting until the Actions band is verified, so the
  first days may be mixed-host. That is an operational fact about the window, not a property of
  an observation, and it is disclosed with the result.

**What this does not touch.** The endpoint, the benchmark (P1), the horizon, the category, the
universe, the exclusion rule, the scorer, the single-run rule, H2's frozen files, and
Amendment 11's finding that no observation in the G3 sample was paired with a stale price.

**The standing objection.** A second study by the same party after the first one failed is how
a null becomes a discovery, and no amendment can fully answer that. What is offered instead:
the correction is fitted only on rows that can never judge it, the cutoff is recorded inside
the generated file, σ_d and δ are fixed here before any H3 outcome exists, n and the window and
the command are fixed here, the scorer withholds H3 by default, and the one declared reason for
collecting thirty days when nine would do is written down above so that stopping early would be
visibly a violation rather than a judgement call.

### Amendment 14 — 2026-10-12, the window slips to 2026-10-16: the new host's scheduler does not fire

**Amendment 13's central operational claim was false within a day of being written.** It says
collection moves to GitHub Actions. On 2026-10-12, the first day the new host was live, the
band did not run once. The window therefore opens **three days later**, because a
pre-registration whose stated collector is not the real one is worth less than one that waits.

**What was observed.** Four cron entries in one workflow file, over the first twelve hours:

| cron | purpose | runs |
|---|---|---|
| `5 * * * *` | GEFS, hourly | 3, at :48, :18 and :38 |
| `20 3 * * *` | band shift A | **0** |
| `10 6 * * *` | band shift B | **0** |
| `30 10 * * *` | resolutions | **0** |

Amendment 13 reasoned about the wrong failure. It argued -- correctly -- that Actions is
*late*, and that lateness is survivable because the collector is a window rather than an
alarm, so a late start eats margin in front of the window while `--until` keeps the end
fixed. What happened was **omission**. The events never fired, and no margin, timeout or
deadline can absorb a trigger that does not occur. The margins were sound and aimed at the
wrong thing.

**The compute was never in doubt and is not being blamed.** A manually dispatched band on
2026-10-11 ran 12m29s, swept Kalshi at `polled=161 written=161 failed=0`, honoured its
absolute deadline, committed its Parquet and pushed first try. The preflight passes from an
Azure runner. What failed is the scheduler, and only the scheduler.

**One change, to test one hypothesis.** The only entry that fired was the hourly one, at
minutes unrelated to its own, so the hypothesis is that an hourly cron starves the
specific-time entries sharing its file. `collect.yml` is split into `band.yml` (the two band
crons, nothing else) and `feeds.yml` (the hourly cron, plus a watchdog). **The cron times are
deliberately unchanged**, so the next three days test the file structure rather than a
reshuffle, and the band declared below stays the one that was declared. `30 10 * * *` stays
beside the hourly cron as a control: if it fires while the band also fires, the
file-splitting explanation is wrong and the real cause is still unknown.

**The silence was worse than the failure.** No band ran, and nothing said so -- it was found
by a person looking at a dashboard. `feeds.yml` gains a watchdog that rides the one trigger
known to fire, judges only after 10:00 UTC, and **fails on purpose** when no band succeeded
that day, because a failed scheduled workflow is the one thing GitHub emails about
unprompted. This is the fifth time in this project that a component answered an easier
question than the one asked, and the second time the remedy was to make absence noisy.

> **The window moves to [2026-10-16T00:00:00Z, 2026-11-15T00:00:00Z) — still thirty days.**
> Every other term of Amendment 13 is unchanged: the sample definition, σ_d = 0.2092,
> δ = 0.0509, required effective n = 133, minimum n = 400, the four pass conditions, the
> single-run rule, the frozen files, and the training cutoff of 2026-10-08. H3 keeps emitting
> in the meantime and those rows are shakeout, excluded exactly as the pre-2026-10-13 ones
> would have been.

The run command changes only in its two instants:

    uv run python -m scripts.score --model gefs_station_bias_v1 --horizon T-24h \
      --since 2026-10-16T00:00:00Z --until 2026-11-15T00:00:00Z \
      --exclude docs/h3_excluded_markets.txt --out reports/h3.md --require-n 400

**Both hosts collect, and the sample is the union.** The desktop is not retired until the
Actions band has proven itself; on 2026-10-12 it did the whole job alone — 10,083 snapshots
across 311 markets, 96.5% capture, 2 CRITICAL gaps, and **the first H3 rows ever written:
118 at T-24h across 59 markets**, 04:56–07:59 UTC. Mixed-host days are an operational fact
about the window rather than a property of any observation, and they travel with the result.

**G0's band for this window is 03:30–09:10 UTC**, judged by
`gap_report --since 2026-10-16T00:00:00Z --band 03:30-09:10 --tz UTC`, with Amendment 10's
band-day accounting unchanged. That interval is the Actions shifts' span narrowed to the
desktop's start, so it is covered whichever host runs.

**One obligation, written down because this project has twice been bitten by a dated chore
nobody remembered.** The desktop's band is declared in Europe/Madrid local time, and Madrid
leaves DST on **2026-10-25, inside this window**: its 05:30 start moves from 03:30 UTC to
04:30 UTC, an hour *after* the band declared above. From that date the 03:30–04:30 UTC slice
— which contains the T-24h instants of every Eastern-time station — is covered only if
Actions is working. The remedy needs no dated action and is recommended rather than applied:
widen the desktop band to start 04:30 local, putting its UTC start at 02:30 before the change
and 03:30 after, inside the declared band in both regimes. If neither that nor a working
Actions band is in place by 2026-10-25, the days after it carry a one-hour band mismatch that
must be disclosed with the result rather than quietly judged against a band nobody was
collecting in.

### Amendment 15 — 2026-10-12, the watchdog moves to the desktop: it was watching the wrong host, from the wrong trigger

No research parameter changes here. The hypothesis, the window
[2026-10-16T00:00:00Z, 2026-11-15T00:00:00Z), σ_d, δ, the effective-n and minimum-n
thresholds, the pass conditions, the frozen files and G0's declared 03:30–09:10 UTC band are
all exactly as Amendment 14 left them. This records an operational change, and it is written
down only because Amendment 14 declared the watchdog in prose, so leaving the doc unamended
would describe a component the repository no longer contains.

**Amendment 14's watchdog was wrong twice, and the second fault is fatal.** It was declared
hours earlier the same day, which is the honest measure of how fast a diagnosis can go stale.

1. *It judged the wrong host.* The check was
   `gh run list --workflow band.yml --status success`, so its question was "did **Actions**
   collect today". The question worth asking is "did **the collector** collect today", and on
   2026-10-12 those had opposite answers: the desktop wrote 10,083 snapshots at 96.5% capture
   while Actions ran no band at all. A watchdog that reads red on the project's best day of
   collection is not a watchdog.
2. *It rode a trigger that stopped firing.* It was deliberately attached to the hourly
   `5 * * * *` cron on the stated grounds that this was "the only trigger known to fire". By
   17:26 UTC that cron had missed **six consecutive hourly slots** since the split landed at
   11:12 UTC, and `band.yml` and `feeds.yml` had zero scheduled runs between them. A manual
   `workflow_dispatch` of `feeds.yml` succeeded in 20s — DNS, TLS and HTTP all green from the
   runner — so the workflow is valid and dispatchable, and only the scheduler is at fault.

**This is the sixth time a component here answered an easier question than the one asked**,
and the first time the component was itself the remedy for the fifth. The pattern is now
specific enough to state as a rule: *a check must observe the thing that does the work, not a
proxy for it, and must run on a trigger independently known to fire.* Amendment 14's watchdog
violated both halves while being introduced to fix a silence.

**What replaces it.** `scripts/band_check.ps1`, Task Scheduler task `pmv-band-check`, daily at
11:33 local — three minutes after the desktop band closes, so Amendment 10's ledger has a
whole closed band to judge. It reads the Parquet the collector just wrote, via
`gap_report --liveness`, and its exit code is the verdict: 0 alive, 1 BAND LOST, 2
misconfigured. Task Scheduler is the trigger, chosen because it demonstrably fired every
collector job on the day Actions fired none. The alarm is a desktop toast plus
`LastTaskResult`, and `supervisor.ps1 -Status` now reports it — along with `pmv-g0-check`,
which had been registered but absent from the status loop's task list and so invisible to it.

**`--liveness` is a new flag, and it deliberately answers less than `gap_report` does.** It
reports only whether the last closed band captured anything, ignoring gap severity entirely.
That narrowness is the point, and it is the one design decision here worth defending: today's
band was healthy and *still* carried 2 CRITICAL gaps of about 2.9 minutes against the 2.5×
tolerance. An alarm wired to gap severity would have fired on that morning, and on most
mornings, which is the permanent `1` that `supervisor.ps1` already warns cannot flag
anything. So the two gates stay separate and neither subsumes the other: **`pmv-band-check`
asks whether the collector ran, `pmv-g0-check` asks whether what it collected is clean.**
`band_days` and `find_gaps` were already split along this seam; this only exposes it to the
scheduler. `tests/unit/test_band_liveness.py` pins the distinction by running identical data
through both paths and asserting the verdicts differ.

**The watchdog's band is the desktop's own 05:30–11:30 Europe/Madrid, not G0's declared
03:30–09:10 UTC**, and the difference is deliberate: a liveness check asks whether the
collector ran in the window it was configured to run in, so it must read the same constant
`supervisor.ps1` schedules from. Judging liveness against the UTC research band would make the
watchdog fire for a *band mismatch* — which is the DST obligation above, a disclosure matter —
rather than for a dead collector. `band_check.ps1` is pinned, never hand-rolled, for the same
reason `g0_check.ps1` is, and if `$BandStart`/`$BandDurationH` move, it moves with them.

**What this does not fix, stated plainly.** Actions still does not fire on schedule, so the
redundancy Amendment 14 relies on ("both hosts collect, and the sample is the union") is
currently one host plus a manual dispatch. The DST obligation above is untouched and still
falls due on 2026-10-25. And the watchdog is now only as reachable as the desktop it runs on:
a machine that is off reports nothing, where the Actions watchdog would at least have been
able to mail. That is a worse failure mode in theory and a better one in practice, because the
desktop is the host that actually collects — but if Actions is ever fixed, a second watchdog
there judging the *desktop's* committed Parquet would close the gap, and that is the right
shape for it rather than the one retired here.

### Amendment 16 — 2026-10-13, the schedule-splitting diagnosis was wrong, and the band leaves GitHub's scheduler

No research parameter changes. The hypothesis, the window
[2026-10-16T00:00:00Z, 2026-11-15T00:00:00Z), σ_d = 0.2092, δ = 0.0509, the required
effective n of 133, min n = 400 nominal, the frozen `gefs_station_bias_v1` pair and G0's
declared 03:30–09:10 UTC band are all exactly as Amendments 13–15 left them. This records an
operational change and a retracted diagnosis.

**Amendment 14's diagnosis is falsified, by the control it had the sense to leave behind.**
The claim was that an hourly cron starves specific-time crons sharing its workflow file — all
four schedules sat in `collect.yml`, and over 12 hours only `5 * * * *` ever fired. The band
moved to `band.yml`, and `30 10 * * *` (resolutions) deliberately stayed in `feeds.yml`
beside the hourly entry, as the control: *"if it fires tomorrow while the band also fires, the
file-splitting story is wrong and something else is going on."* On 2026-10-13 everything
fired, and everything fired hours late:

| cron | purpose | file | fired | late by |
|---|---|---|---|---|
| `20 3 * * *` | band shift A | `band.yml` | 10:46Z | 7h26m |
| `10 6 * * *` | band shift B | `band.yml` | 14:03Z | 7h53m |
| `30 10 * * *` | resolutions | `feeds.yml` | 17:41Z | 7h11m |
| `5 * * * *` | GEFS hourly | `feeds.yml` | 3 of 24 slots | — |

So it was never the file, and the split bought nothing. **It is worth stating that the
remedy's own control is what refuted it** — the alternative was believing a fixed mechanism
for weeks on the evidence that the band now fires, which it does.

**The band collected zero rows on both shifts anyway, and reported success.** `--until` is an
absolute instant, by deliberate design (Amendment 13: a tick count drifts under suspend). A
shift that starts after its own deadline therefore has an empty window: the snapshotter
exited on its first tick, the commit step found nothing to add, and both runs went green in
about four minutes having written nothing. The run log says `collecting until
2026-10-13T06:50:00+00:00` at 10:46Z and nothing anywhere says that is impossible. **Fifth
occurrence of this project's recurring fault** — a check reporting success on an easier
question than the one asked, after the band mismatch, the empty sweep, the `--days 1`
default, the empty-range pass, and Amendment 15's watchdog.

Fixed where it is visible: `band.yml` now refuses a dispatch whose deadline has already
passed, with `::error::` and a non-zero exit, before the snapshotter starts. Verified in both
directions against the live shell logic — today's 06:50Z refuses, a future deadline proceeds.

**The band leaves GitHub's scheduler entirely.** `band.yml`'s `schedule` triggers are removed;
it is `workflow_dispatch`-only, driven by `pinger/` — a Cloudflare Worker with three cron
triggers that POSTs `workflow_dispatch` over the REST API. API triggers are not on the
scheduler that failed: every hand dispatch in this project has started within seconds,
including the 12m29s band on 2026-10-11 that polled 161 markets and pushed first try.

Three decisions in it worth recording:

1. **Not hosted on the desktop.** Task Scheduler is the one timer here that has never failed
   to fire, and it drives every local collector. But Actions exists to cover the days the
   desktop is asleep — which are precisely the days a desktop-hosted pinger is also asleep. A
   redundant collector triggered by the host it is redundant *for* is not redundant.
2. **The pinger judges nothing.** It dispatches and reports the HTTP status. Whether a run
   collected anything is answered by `band.yml`'s deadline guard and by `band_check.ps1`
   against the committed Parquet. Amendment 15 retired a watchdog for watching the wrong host
   from the wrong trigger; a watchdog inside the trigger it is meant to be independent of
   would repeat that, one amendment later.
3. **GEFS is pinged hourly too**, which is a widening of scope and is deliberate. A band
   running on a day the desktop is asleep would otherwise emit against whatever GitHub's
   3-of-24 cron last fetched, and the ensemble cannot be backfilled — point-in-time integrity
   rests entirely on the row having been written live. `feeds.yml` keeps its own crons:
   resolutions are backfillable, GEFS duplicates are harmless append-only rows, and the two
   are now the only remaining evidence of whether GitHub's scheduler ever recovers.

`pinger/check.mjs` is a pre-deploy self-check, and it was verified to fail on three real drift
cases (an unmapped cron, a shift transposed onto the other's deadline, a deadline earlier than
the cron that fires it) rather than merely to pass.

**What is still open, stated plainly.**

- **The pinger is committed but not deployed.** `wrangler deploy` needs a Cloudflare account
  and a fine-grained GitHub token, which is Abel's step. **Until it runs, Actions collects
  nothing at all** — the desktop is the sole collector. This is not a regression: the
  scheduled runs it replaces also collected nothing, just silently. The window opens
  2026-10-16.
- **The DST obligation is untouched** and still falls due 2026-10-25, when Madrid's exit moves
  the desktop band from 03:30Z to 04:30Z, an hour after the declared band start. If neither a
  widened desktop band nor a working Actions band is in place by then, the days after it carry
  a one-hour band mismatch that must be disclosed with the result.
- **The desktop's sleep failures are unmitigated.** 2026-10-10 and 2026-10-11 were both total
  losses, pre-window and therefore free; the same failure inside the window is not.
