# Forecast validation report

**Shortfall, stated as Amendment 18 requires.** The frozen sample is 1,104 clean nominal
observations; 1,100 had resolved when the run was taken and are what is scored below. The
4 unresolved are all rungs of one ladder — `KXHIGHNY-26NOV13-{B50.5, B52.5, B54.5, T50}`,
Central Park's high for 2026-11-13, closed 2026-11-14T06:00Z — for which the venue reported
`result ""`, `settlement_value null` and an `expiration_time` of **2026-11-20T15:00:00Z**,
two and a half days past the pre-registered fallback instant of 2026-11-18T00:00:00Z. The
run was taken 2026-11-16 on Abel's instruction, before that instant: the cut is fixed, so
nothing could enter the sample in the interval, and the venue's own expiration time showed
the 4 rows could not resolve by the fallback either, so the fallback's 1,100-row sample was
already the sample. Had all 4 resolved at the most favorable paired difference possible
(d = +1 each), mean(d) would move from −0.0845 to no better than −0.0806. The verdict does
not depend on them. The deviation and its direction are recorded in Amendment 19.

Generated 2026-11-16 20:21 UTC

`n` = 1,100 paired observations across 430 event families. The benchmark is the market price at the same timestamp as each prediction (P1).

**Reading this table.** `eff. n` is the nominal n divided by the observed design effect - clustered evidence is worth less than its row count. `need eff. n` is section 5's requirement to detect the effect size actually observed, at alpha 0.05 and 80% power, using this sample's sigma_d. A row marked `UNDERPOWERED` did not have the evidence to find what it reports, **whatever its confidence interval says**.


## Overall

| forecast | n | clusters | eff. n | mean(d) | 95% CI | BSS | ECE | AUC | need eff. n | verdict |
|---|--:|--:|--:|--:|:--:|--:|--:|--:|--:|:--|
| gefs_station_bias_v1 | 1,100 | 430 | 879 | -0.0845 | [-0.1027, -0.0666] | -0.5470 | 0.160 | 0.610 | 81 | adequate |

## Excluding tail price buckets (G3)

section 4.2: almost all fake edge lives in the tails. An improvement that disappears here is longshot bias, not skill.

| forecast | n | clusters | eff. n | mean(d) | 95% CI | BSS | ECE | AUC | need eff. n | verdict |
|---|--:|--:|--:|--:|:--:|--:|--:|--:|--:|:--|
| gefs_station_bias_v1 | 893 | 426 | 701 | -0.0932 | [-0.1149, -0.0718] | -0.5045 | 0.195 | 0.569 | 76 | adequate |

## By price bucket


### [0.00, 0.05]

| forecast | n | clusters | eff. n | mean(d) | 95% CI | BSS | ECE | AUC | need eff. n | verdict |
|---|--:|--:|--:|--:|:--:|--:|--:|--:|--:|:--|
| gefs_station_bias_v1 | 206 | 177 | 206 | -0.0446 | [-0.0662, -0.0240] | -1.8909 | 0.109 | 0.531 | 96 | adequate |

### (0.05, 0.20]

| forecast | n | clusters | eff. n | mean(d) | 95% CI | BSS | ECE | AUC | need eff. n | verdict |
|---|--:|--:|--:|--:|:--:|--:|--:|--:|--:|:--|
| gefs_station_bias_v1 | 308 | 251 | 308 | -0.0578 | [-0.0805, -0.0347] | -0.4893 | 0.176 | 0.488 | 99 | adequate |

### (0.20, 0.80]

| forecast | n | clusters | eff. n | mean(d) | 95% CI | BSS | ECE | AUC | need eff. n | verdict |
|---|--:|--:|--:|--:|:--:|--:|--:|--:|--:|:--|
| gefs_station_bias_v1 | 567 | 374 | 504 | -0.1065 | [-0.1344, -0.0790] | -0.4755 | 0.261 | 0.533 | 72 | adequate |

### (0.80, 0.95]

_n = 18, below the 20-row floor._

### (0.95, 1.00]

_n = 1, below the 20-row floor._

## By category


### Climate and Weather

| forecast | n | clusters | eff. n | mean(d) | 95% CI | BSS | ECE | AUC | need eff. n | verdict |
|---|--:|--:|--:|--:|:--:|--:|--:|--:|--:|:--|
| gefs_station_bias_v1 | 1,100 | 430 | 879 | -0.0845 | [-0.1027, -0.0666] | -0.5470 | 0.160 | 0.610 | 81 | adequate |
