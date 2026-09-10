---
name: verify-pmv
description: >
  Verify work on the prediction-market-validation repo before it is trusted or
  committed. Runs the mechanical gates (ruff, mypy, pytest, import-linter),
  checks the H2 freeze, checks the G0 operational gate, then reviews the change
  against the pre-declaration's protocol rules that no linter can catch.
  Use after ANY change to this repo, before any commit, before believing a
  report, and whenever asked to "verify", "check the work", "is this correct",
  "did I break the protocol", or "can I commit this".
---

# Verify prediction-market-validation

This repo's failure mode is not broken code. It is **a check that quietly answers
an easier question than the one it claims to answer.** That has happened three
times — the 05:30–11:00 band mismatch, the empty universe sweep, and
`gap_report --days 1` reporting clean for three days with the 09-04 fault inside
the window. Every tier below exists because one of those got through.

So the job is not "run the commands". It is: for each check, ask what question it
actually answered, and whether that is the declared one.

Read `docs/pre_declaration.md` — especially the Amendments — before ruling on
anything in Tier 4. It is the source of truth; this file is a summary that can drift.

---

## Tier 1 — mechanical gates

All four must pass. Run from the repo root.

```bash
uv run ruff check .
uv run mypy --strict src/
uv run pytest                      # unit only; never touches the network
uv run lint-imports                # 3 contracts, all must read KEPT
```

`lint-imports` is the golden rule made mechanical: `src/scoring/` cannot import
`src/models/`, `src/baselines/`, `src/analysis/`, or anything that reaches the
network or database. **A contract that was edited to make a change pass is a
failure, not a fix** — check `git diff pyproject.toml` for edits to
`[tool.importlinter]` and treat any loosening as blocking.

Integration tests hit the live venue and are excluded by default. Run
`uv run pytest -m integration` only when the change touches `src/ingest/`.

## Tier 2 — the H2 freeze

`model_version = gefs_ensemble_raw` is frozen at commit `782bb44`
(Amendment 5). Two files are frozen:

```bash
git log --oneline 782bb44..HEAD -- src/models/weather.py src/analysis/weather.py
```

**Must print nothing.** Any output is a blocking violation.

The freeze is a claim about those two files, not about HEAD — `emit()` stamps
whatever sha is current at run time, so the sha in the database is not evidence
either way. Changing the model means **a new `model_version` plus an amendment**,
never an edit to a frozen file. If a change wants different forecast behaviour,
the correct diff adds a model; it never modifies these two.

Ingest is not the model. Adding a station to `src/ingest/stations.py` does not
touch the freeze (Amendment 9 did exactly this).

## Tier 3 — G0, the operational gate

```powershell
powershell -ExecutionPolicy Bypass -File scripts\g0_check.ps1
```

Exit 0 = clean, 1 = CRITICAL. The daily verdict is also readable without running
anything:

```powershell
Get-ScheduledTaskInfo -TaskName pmv-g0-check   # LastTaskResult IS the gate
```

Do not hand-roll a `gap_report` invocation instead. `g0_check.ps1` pins
`--since` to the **G0 clock start (2026-09-05)**, `--band 05:30-11:30` and
`--tz Europe/Madrid`, and each of those is load-bearing:

- Without `--band`, every overnight hole counts and the gate reads red forever.
- Without `--since`, it defaults to `--days 1` and answers "was yesterday clean",
  which is the exact substitution that hid the 09-04 fault.
- `--since` is the **G0 clock** start, not the analysis-window start
  (2026-09-03T18:33Z). Moving it forward without a CRITICAL to justify it passes
  the gate on a short window; that move belongs in an amendment.

A clean G0 says the machine woke, the tasks fired and ticks landed in-band. It
says **nothing** about whether admission worked — the empty sweep ran green
through G0 for a day (Amendment 8).

## Tier 4 — protocol rules no linter catches

Judgment, not commands. Walk the diff (`git diff`, or `git log -p` for the range
under review) against each. Any hit is blocking until an amendment covers it.

**1. Peeking. The single most expensive violation.**
H2's BSS is one pre-committed reading. `scripts/score.py` and
`scripts/permutation_test.py` both print a score per model, and `build_frame`
picks up every `model_version` in the table, so an unfiltered run used to spend
that reading on whoever happened to type it.

Both now withhold `FROZEN_MODELS` (`src/analysis/frame.py`) unless named:

```bash
uv run python -m scripts.score                             # baselines only, safe
uv run python -m scripts.score --model gefs_ensemble_raw   # THE G3 look, spends it
```

So the check is no longer "did someone run the scorer" but:
- Does `git diff` weaken `resolve_models`, `FROZEN_MODELS`, or the `models=`
  argument at either call site? Any of those re-opens the leak.
- Did a new report path start calling `build_frame` **without** `models=`?
  A fresh script is the obvious way back in — `build_frame`'s default is still
  "every model", because the pivot layer is not where policy belongs.
- Does `reports/validation.md` or a pasted result contain `gefs_ensemble_raw`?
  If so the look was taken; find out by whom and whether it was the G3 run.

Withholding is a guard against accident, not a lock — G3 has to type the flag.
Baseline-only scoring remains fine at any time.

**2. Stopping rule.** n = **715 nominal** T-24h observations, pre-committed
(Amendment 6). Nominal on purpose: `clustered_bootstrap.effective_n` is a
function of `d`, so stopping on it means peeking to decide whether to peek.
No G3 date is declared. A diff that adds early-stopping, a running BSS readout,
or a "are we there yet" report is a violation.

**3. Gates must answer the declared question.** The signature bug. For every
check the change adds or modifies, state what question it answers and compare it
to the declared one. Known instances, all of which must stay fixed:
- `gap_report` requires `--since` and `--band` (it now warns when they are missing).
- `watch_resolutions` requires `--catalogue`; the default scan cannot reach a
  tracked market, because ~5,000 combo shards settle a day and fill every page.
- Never `list_open_markets` without `series_tickers`. The venue-wide sweep is
  dead (Amendment 7): 12,000+ consecutive `KXMVECROSSCATEGORY-SHARD1-*` rows blow
  past `_paged`'s 25×200 cap, and the combo filter then returns `[]` with no
  exception. `/events?with_nested_markets=true` is not an escape — it ignores
  `min_close_ts`/`max_close_ts`.
- An empty result must increment a failure counter, never pass as "nothing to do".
  `universe:empty` in `stats.failures` exists for this.

**4. Nothing that must be live may be backfilled.**
`predictions` — the DB refuses a stamp at or after market close; a missed horizon
is gone forever. `gefs_ensemble` — the Open-Meteo endpoint does not report the
run's init time, so point-in-time is guaranteed **by the clock, not the payload**;
rows are trustworthy only because they were written live. Any code that
constructs a historical `fetched_at` or `ts`, or that relaxes those constraints,
is a blocking bug. Resolutions *are* backfillable; only these two are not.

**5. Read-only.** No orders, no wallet, no keys, no capital or position logic,
ever. This is the repo's premise, not a phase.

**6. Structural fields only.** The station comes from `rules_primary` and nowhere
else; the strike comes from `strike_type` + `floor_strike`/`cap_strike`. Never
parse the ticker suffix (`T94` is a `less` rung, `T101` a `greater` one) and
never parse question text. Three station codes are not the obvious airport —
`CLIMDW` = Chicago Midway, `CLIHOU` = Houston Hobby, `CLINYC` = Central Park,
and `CLIMSY` = New Orleans. A wrong station still returns a number, off by about
the width of one Kalshi bucket.

**7. Universe parameters are research choices.** `min_volume` stays 1,000;
dropping it to 0 buys ~15% more H2-eligible markets at the cost of wider
spreads, i.e. H4 contamination. `max_markets` 400, 30 h close window. Changing
any of them mid-study needs an amendment, because they feed the power
calculation.

**8. Deliberate weirdness that must not be "fixed".** Per-rung Laplace
`(k+1)/(n+2)` means a ladder sums to `(n+rungs)/(n+2)`, not 1 — on purpose;
without it every unanimous far rung fails the `0 < p_hat < 1` constraint and
only near-the-money rungs survive, which is a selection effect. Ensemble members
are rounded half-up before comparison. `is_pure_forecast` goes false once the
target day has begun locally. A diff that "corrects" any of these is wrong.

**9. Amendments.** Any change to a pre-declared parameter — hypothesis, effect
size, n, window, band, universe, model_version — needs an amendment appended to
`docs/pre_declaration.md` **and committed**, because the commit timestamp is the
evidence. A parameter changed in code but not in the doc is the worst case: it
looks pre-declared and isn't.

**10. Supervisor traps.** A task action without `-WindowStyle Hidden` inherits
the calling shell's console and dies with `STATUS_CONTROL_C_EXIT` (0xC000013A),
logging nothing. `supervisor.ps1 -Install` uses `-Force`, which **stops running
jobs** — that is what caused the binding 09-04 fault. Never re-install mid-band.

---

## Verdict

Report as a table, most severe first, then a one-line overall call. Say which
tier each finding came from and quote the evidence (command output, file:line,
or the amendment it contradicts).

| Tier | Check | Result |
|---|---|---|
| 1 | ruff / mypy / pytest / lint-imports | |
| 2 | H2 freeze (`782bb44..HEAD`) | |
| 3 | G0 (`g0_check.ps1` exit code) | |
| 4 | protocol review | |

**Overall: PASS** only when Tiers 1–3 are green *and* Tier 4 found nothing.
Otherwise **BLOCKED**, naming the rule.

Never soften a Tier 2 or Tier 4 finding into a suggestion. Those are the ones
that cost data or invalidate the study, and both failure modes look fine at the
time.

## What this does not check

Whether the science is right. It checks that the work is consistent with what
was pre-declared — not that the pre-declaration was a good idea. A finding of
"the protocol itself looks wrong" is worth raising, but it is an amendment
proposal, not a verification result.
