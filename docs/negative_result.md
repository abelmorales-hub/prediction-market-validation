# A raw weather-model ensemble does not beat prediction-market prices on daily temperature

**Negative result. Pre-registered, single-look, closed 2026-10-11.**

A 31-member GEFS ensemble, converted to probabilities on Kalshi's daily maximum-temperature
ladders with no station correction, was tested against the market's own price at the same
instant, 24 hours before close, on 778 pre-registered observations across 263 event
families and 20 US stations.

It lost, and not narrowly. **Mean paired Brier difference −0.1008** (clustered bootstrap
95% CI **[−0.1185, −0.0839]**), a **Brier skill score of −0.78** against the market. The
forecast's absolute Brier was **0.229** where the market's was **0.128**. The study was
powered to detect an effect of 0.047 and had five times the effective sample size it
needed; it used that power to measure a loss.

The useful content of this result is not "the market is efficient". It is a measurement of
how much a specific, known, uncorrected bias costs — and a worked example of a forecast
that is confidently wrong rather than uninformative.

---

## 1. What was tested

**The markets.** Kalshi lists daily maximum-temperature contracts on 20 US weather
stations. Each day's event is a *ladder* of rungs tiling the temperature line exactly
once — `T < 94`, `94 ≤ T ≤ 95`, `T > 101` — settling on The Weather Company's
whole-degree observation at a named station. Each rung is scored as its own binary market
against its own price.

**The forecast (`gefs_ensemble_raw`).** For each rung: take the 31-member GEFS ensemble
forecast of that station's daily maximum, round each member half-up to whole degrees,
count the members `k` that settle the rung YES, and report

```
p̂ = (k + 1) / (n + 2)        n = 31
```

That is the whole model. No station correction, no elevation adjustment, no bias term, no
fitted parameter. The Laplace smoothing is a declared structural choice, not a tuned one:
a 31-member ensemble votes unanimously on most rungs of a six-rung ladder, and without
smoothing every far rung would be exactly 0 or 1 and get dropped by the schema's
`0 < p̂ < 1` constraint — leaving a sample made only of near-the-money rungs where the
market is sharpest, which is a selection effect dressed as a result. The side effect is
that one ladder's rungs sum to `(n + rungs)/(n + 2)`, not 1. That is correct here, because
normalising across the ladder would couple observations the scorer treats as independent.

**The benchmark.** The market's mid price for the *same rung* at the *same timestamp* as
the prediction. Pairing is the point: the difference removes the event's own difficulty
from the comparison, so a hot ladder in Phoenix and a coin-flip in Seattle contribute on
the same footing.

**The endpoint.** `d = BS_market − BS_model`, mean over the sample, with a 95% confidence
interval from a bootstrap clustered on `event_family` (one station-day). Positive means
the model beat the price.

**The horizon.** T-24h — the prediction is stamped against a snapshot taken at most five
minutes before the 24-hours-to-close instant, and never after it.

## 2. Why the loss is credible, stated before the result was seen

A negative result is cheap to produce by accident. These are the specific things that were
fixed in advance, with the dates they were fixed:

- **The scorer was built before any model existed**, and `import-linter` forbids the
  scoring package from importing the models, baselines or analysis packages. A scorer that
  can see the model is a scorer that will eventually be bent to flatter it.
- **The model was frozen 2026-09-05** as two files at a named commit, 15 days before the
  sample window opened. Verifiable: `git log 782bb44..HEAD -- src/models/weather.py
  src/analysis/weather.py` is empty. Changing the model means a new `model_version` and a
  new amendment, never an edit.
- **The sample was frozen before any outcome was read** — every prediction stamped in
  `[2026-09-20T00:00:00Z, 2026-10-08T09:30:00Z)`, minus 156 markets excluded for data-
  quality faults listed in a committed file. 778 rows. The cut is a fixed instant, so the
  sample could not grow between the declaration and the run.
- **The required sample size was committed in advance** (715 observations), computed from a
  σ_d measured *without reading a single outcome*: under the null `y ~ Bernoulli(m)`, both
  `E[d|m]` and `Var[d|m]` are closed-form functions of `D = p̂ − m`, so the dispersion of
  the endpoint can be derived from the forecasts and prices alone. This matters because
  scoring resolved rows yields σ_d and mean(d) *together*, and mean(d) is the one look.
- **The run was guarded by code, not by intention.** `score.py --require-n 715` aborts
  before the function where a score first exists; the frozen model is withheld from any
  run that does not name it explicitly; the scheduled job refuses to produce a second
  report and unregisters itself after the run that succeeds. The guard was verified
  firing: at 675 resolved observations it exited non-zero and scored nothing.
- **The pipeline was checked for bias of its own on exactly these rows.** Feeding the
  market's own price in as the model must score exactly zero. It does: BSS **+0.0000**,
  CI [+0.0000, +0.0000], in every stratum and every price bucket. This is the single check
  that catches timestamp misalignment, and it caught two real bugs earlier in the project
  — one of which had produced a fake +0.0046 skill score for a null model.
- **The market's own Brier landed at 0.128**, inside the 0.10–0.18 band declared as
  plausible. Outside that band the correct conclusion is a data bug, not a finding: 0.25
  means misaligned timestamps, 0.02 means post-resolution prices leaked in.

## 3. The result

| stratum | n | families | eff. n | mean(d) | 95% CI | BSS | ECE | AUC |
|---|--:|--:|--:|--:|:--:|--:|--:|--:|
| **overall** | 778 | 263 | 746 | **−0.1008** | [−0.1185, −0.0839] | −0.7848 | 0.171 | 0.532 |
| tails excluded | 631 | 257 | 587 | −0.1149 | [−0.1362, −0.0943] | −0.7264 | 0.210 | 0.479 |
| market price ≤ 0.05 | 147 | 107 | 147 | −0.0403 | [−0.0596, −0.0236] | −46.24 | 0.116 | n/a |
| 0.05 – 0.20 | 232 | 158 | 232 | −0.0701 | [−0.0930, −0.0485] | −0.9032 | 0.186 | 0.389 |
| 0.20 – 0.80 | 397 | 232 | 397 | −0.1400 | [−0.1694, −0.1116] | −0.6797 | 0.277 | 0.444 |

`eff. n` is nominal n divided by the observed design effect — clustered evidence is worth
less than its row count. The two top price buckets held 2 and 0 rows and are not reported.

The four pre-declared success conditions were: `mean(d) > 0`; CI excludes zero; effective n
sufficient; improvement survives excluding the tail price buckets. **The first two fail,
the third passes (746 against the 143 required), and the fourth is moot** — there is no
improvement for the tail exclusion to test, and removing the tails makes the forecast
*worse*, which is the opposite of the longshot artifact that condition exists to catch.

Absolute Brier scores, from the skill score and the paired difference:

| | market | model |
|---|--:|--:|
| overall | 0.128 | 0.229 |
| tails excluded | 0.158 | 0.273 |
| 0.20 – 0.80 bucket | 0.206 | 0.346 |

**A constant forecast of 0.5 scores 0.25 by construction.** The model's overall 0.229 is
barely better than that, and in the mid-price bucket its 0.346 is *worse than a coin flip*.
That single comparison carries the result better than the skill score does: this is not a
weak forecast, it is an actively harmful one.

## 4. Why it lost — miscalibration, not absence of signal

The two diagnostics separate "knows nothing" from "knows something and is shifted":

**Discrimination is at chance once the market's own ordering is removed.** Overall AUC is
0.532. Inside each price bucket — where the market has already sorted the events by
probability — it is 0.389, 0.444 and 0.479. A forecast with no information scores 0.5; a
forecast *below* 0.5 is anti-correlated with the outcome within that stratum.

**Calibration is 3.5× worse than the market's.** Expected calibration error 0.171 against
the market's 0.049, measured on the same frozen window, rising to 0.210 and 0.277 in the
strata where the model is most confident.

**The cheapest bucket is the most diagnostic.** In the 147 rows the market priced at or
below 0.05, **every single one resolved NO** — this is why AUC is undefined there, one
class only. The market's mean Brier in that bucket was 0.0009, i.e. it priced them around
0.03 and was right every time. The model's was 0.0412, a root-mean-square probability of
**0.20**. It put roughly a fifth of its belief on 147 events that the market had correctly
written off, and paid 46 times the market's loss to do it.

That is the signature of a distribution in the right shape at the wrong location. The
settlement is a whole-degree observation at a named station; the ensemble is a 0.25° grid
cell. A systematic offset of even one or two degrees moves members across rung boundaries,
and with 31 members each member is three points of probability. Rungs the market knows are
dead collect votes. The model's errors are not noise around the price — they are a shift
the price already accounts for.

This was the expected direction. The model's own source file, written before the data
existed, opens by saying so: *"It is expected to lose … A loss here should be read as that
bias, not as evidence the market is efficient."* The result's value is that it **measures**
that bias rather than asserting it: **0.10 in mean paired Brier, at T-24h, over 263
station-days.**

## 5. What this does and does not establish

**It establishes** that raw numerical weather-model output, treated as a probability
without station-level correction, is far worse than the price of a liquid temperature
market 24 hours out, by a margin no plausible amount of sampling noise or data-quality
loss can explain.

**It does not establish** that the market is efficient. A market that is itself badly
calibrated would still beat this forecast; losing to the price is weak evidence about the
price. The market's absolute performance (Brier 0.128, ECE 0.049, AUC 0.900 on comparable
rows) is consistent with a well-calibrated forecaster, but that is a descriptive
observation, not a tested hypothesis.

**It does not establish** that a bias-corrected version would lose — which is precisely
why the correction is out of bounds here. The pre-registration says failure closes the
hypothesis and licenses no search for a different category, horizon or model. Concretely:
**no station-corrected model may be fitted on these 778 rows.** They are spent. Anything
tuned on them can never be tested out of sample against them again, and a correction fitted
to the same rows that would evaluate it is exactly the move the whole apparatus exists to
prevent.

**It says nothing about tradability.** No economic gate was reached; nothing here accounts
for spread, depth or fees, and no orders were ever placed. The pipeline has no wallet and
no capital logic by construction.

## 6. Limitations

Stated in descending order of how much they should worry a reader.

1. **The data-quality gate was red when the run was taken.** 159 CRITICAL sampling gaps
   stand in the collection window, and the protocol's own rule voids a window containing
   one. The decision to proceed was made by the party who wanted to proceed, which is
   recorded as a standing objection rather than resolved. The argument for proceeding is a
   measurement, not a rhetorical one: an observation exists only if a snapshot was found at
   or before its horizon within five minutes, and over all 817 candidate rows that lag was
   min 0.11 / median 2.54 / **max 3.20 minutes**, with zero rows beyond tolerance and zero
   stamped after their own horizon. The gaps cost sample size and bias *which* observations
   exist; they corrupt none that does.
2. **The loss of observations was not random.** 102 markets carrying a sampling fault have
   no prediction row at all, and the faults cluster at the minutes when the dense sampling
   window of the hour-closing ladders opens. The markets that were lost disagreed with the
   market price *more* than the ones kept (median |p̂ − m| 0.200 against 0.137), so the
   study preferentially lost the observations where the forecast took its largest
   positions. The direction this pushes the result is genuinely unknown — it removes the
   model's best chances along with its worst — and it is unfixable after the fact.
3. **One venue, one category, one horizon, 18 days.** Kalshi, US daily maximum temperature,
   T-24h, 2026-09-20 to 2026-10-08. September–October weather in 20 US cities is not
   weather in general, and a single ensemble (GEFS, via a JSON endpoint rather than native
   GRIB2) is not numerical weather prediction in general.
4. **Clustering is coarse.** One cluster per station-day, 2.96 rows per cluster. Rungs of
   the same ladder are strongly dependent and that is handled; dependence *between* nearby
   stations on the same synoptic day is not.
5. **The ensemble's init time is not in the payload.** Point-in-time integrity rests on the
   fact that rows were written live by a scheduled job, not on a timestamp the data source
   provides. The feed therefore cannot be backfilled, and this study cannot be replicated
   on history — only re-run forward.
6. **The leakage check is untested here.** A permutation test exists in the pipeline and is
   vacuous for a forecast that lost: leakage inflates skill, and there is none to explain.

## 7. What a follow-up would have to look like

Not a continuation — a new study. A station-bias-corrected model would need: a new
`model_version`; its correction fitted **only** on data stamped before the new sample
window opens; a fresh pre-registration with its own σ_d, effect size and required n; and a
new forward-collected sample. The infrastructure for all of that already exists and works,
which is the one thing this result confirms positively.

The honest summary of the project is that the apparatus performed and the hypothesis did
not. The scorer, the gates, the frozen model, the σ_d-without-outcomes derivation, the
fixed sample and the refusal guard all did their jobs. What they were pointed at had no
edge.

## 8. Reproduction

```
git show 9b0e279                                          # this result, and Amendment 12
git log 782bb44..HEAD -- src/models/weather.py src/analysis/weather.py   # must be empty

uv run python -m scripts.score --model gefs_ensemble_raw --horizon T-24h \
  --since 2026-09-20T00:00:00Z --until 2026-10-08T09:30:00Z \
  --exclude docs/g3_excluded_markets.txt --out reports/g3_h2.md
```

- Protocol and all 12 amendments: `docs/pre_declaration.md`
- Design, gates G0–G4: `docs/prediction_market_validation_pipeline.md`
- The report as generated: `docs/g3_result.md`
- The 156 excluded markets: `docs/g3_excluded_markets.txt`

The run was taken once, at **2026-10-11 17:06 UTC**, on a sample frozen on 2026-10-08.
