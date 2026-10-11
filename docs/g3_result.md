# Forecast validation report

Generated 2026-10-11 17:06 UTC

`n` = 778 paired observations across 263 event families. The benchmark is the market price at the same timestamp as each prediction (P1).

**Reading this table.** `eff. n` is the nominal n divided by the observed design effect - clustered evidence is worth less than its row count. `need eff. n` is section 5's requirement to detect the effect size actually observed, at alpha 0.05 and 80% power, using this sample's sigma_d. A row marked `UNDERPOWERED` did not have the evidence to find what it reports, **whatever its confidence interval says**.


## Overall

| forecast | n | clusters | eff. n | mean(d) | 95% CI | BSS | ECE | AUC | need eff. n | verdict |
|---|--:|--:|--:|--:|:--:|--:|--:|--:|--:|:--|
| gefs_ensemble_raw | 778 | 263 | 746 | -0.1008 | [-0.1185, -0.0839] | -0.7848 | 0.171 | 0.532 | 46 | adequate |

## Excluding tail price buckets (G3)

section 4.2: almost all fake edge lives in the tails. An improvement that disappears here is longshot bias, not skill.

| forecast | n | clusters | eff. n | mean(d) | 95% CI | BSS | ECE | AUC | need eff. n | verdict |
|---|--:|--:|--:|--:|:--:|--:|--:|--:|--:|:--|
| gefs_ensemble_raw | 631 | 257 | 587 | -0.1149 | [-0.1362, -0.0943] | -0.7264 | 0.210 | 0.479 | 41 | adequate |

## By price bucket


### [0.00, 0.05]

| forecast | n | clusters | eff. n | mean(d) | 95% CI | BSS | ECE | AUC | need eff. n | verdict |
|---|--:|--:|--:|--:|:--:|--:|--:|--:|--:|:--|
| gefs_ensemble_raw | 147 | 107 | 147 | -0.0403 | [-0.0596, -0.0236] | -46.2419 | 0.116 | n/a | 67 | adequate |

### (0.05, 0.20]

| forecast | n | clusters | eff. n | mean(d) | 95% CI | BSS | ECE | AUC | need eff. n | verdict |
|---|--:|--:|--:|--:|:--:|--:|--:|--:|--:|:--|
| gefs_ensemble_raw | 232 | 158 | 232 | -0.0701 | [-0.0930, -0.0485] | -0.9032 | 0.186 | 0.389 | 50 | adequate |

### (0.20, 0.80]

| forecast | n | clusters | eff. n | mean(d) | 95% CI | BSS | ECE | AUC | need eff. n | verdict |
|---|--:|--:|--:|--:|:--:|--:|--:|--:|--:|:--|
| gefs_ensemble_raw | 397 | 232 | 397 | -0.1400 | [-0.1694, -0.1116] | -0.6797 | 0.277 | 0.444 | 36 | adequate |

### (0.80, 0.95]

_n = 2, below the 20-row floor._

### (0.95, 1.00]

_n = 0, below the 20-row floor._

## By category


### Climate and Weather

| forecast | n | clusters | eff. n | mean(d) | 95% CI | BSS | ECE | AUC | need eff. n | verdict |
|---|--:|--:|--:|--:|:--:|--:|--:|--:|--:|:--|
| gefs_ensemble_raw | 778 | 263 | 746 | -0.1008 | [-0.1185, -0.0839] | -0.7848 | 0.171 | 0.532 | 46 | adequate |
