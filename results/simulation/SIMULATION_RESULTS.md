# Simulation results (generated)

_Simulation: energy from SimMachine model (assumptions unless --calibrate with measured data), carbon from real 2020 traces (DE/GB/FR/US-CAL; ENTSO-E/CAISO via Let's Wait Awhile) and one SYNTHETIC India trace._

Predictor errors (noise removed): absolute {'ml': 0.08178079187103357, 'table': 0.21191060362012698}, mode effect {'ml': 0.02, 'table': 0.09773483956606956}; run-to-run noise 0.119 (source: results/model_metrics.json).
test split: 14-day windows starting 2020-07-01 + 14*seed days (Jul-Dec); tuning split (kappa sweep only): windows from 2020-01-15 (Jan-Jun). Seeds per trace: 10, days per window: 14.
Savings are relative to `default` (start now, OS scheduling) for the same window and jobs; ± is a 95% CI.

## E1 Baselines on AC

| trace | policy | carbon saving % | energy saving % | miss rate % | mean delay h |
|---|---|---|---|---|---|
| de_2020_hourly | battery_only | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| de_2020_hourly | carbon_only | 5.0 ± 2.6 | 0.0 ± 0.0 | 0.00 | 0.36 |
| de_2020_hourly | combined_noai | 18.3 ± 2.4 | 14.8 ± 0.6 | 0.00 | 0.29 |
| de_2020_hourly | default | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| de_2020_hourly | greenthread | 19.1 ± 2.3 | 15.7 ± 0.2 | 0.00 | 0.33 |
| de_2020_hourly | heuristic | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| fr_2020_hourly | battery_only | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| fr_2020_hourly | carbon_only | 4.7 ± 2.7 | 0.0 ± 0.0 | 0.00 | 0.43 |
| fr_2020_hourly | combined_noai | 17.9 ± 2.3 | 14.9 ± 0.5 | 0.00 | 0.30 |
| fr_2020_hourly | default | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| fr_2020_hourly | greenthread | 18.8 ± 2.2 | 15.7 ± 0.2 | 0.00 | 0.36 |
| fr_2020_hourly | heuristic | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| gb_2020_hourly | battery_only | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| gb_2020_hourly | carbon_only | 11.3 ± 3.9 | 0.0 ± 0.0 | 0.00 | 0.88 |
| gb_2020_hourly | combined_noai | 23.0 ± 3.5 | 14.6 ± 0.7 | 0.00 | 0.73 |
| gb_2020_hourly | default | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| gb_2020_hourly | greenthread | 24.4 ± 3.4 | 15.5 ± 0.4 | 0.00 | 0.82 |
| gb_2020_hourly | heuristic | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| in-so_synthetic_2020_hourly | battery_only | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| in-so_synthetic_2020_hourly | carbon_only | 0.2 ± 0.2 | 0.0 ± 0.0 | 0.00 | 0.04 |
| in-so_synthetic_2020_hourly | combined_noai | 14.8 ± 0.3 | 14.8 ± 0.3 | 0.00 | 0.02 |
| in-so_synthetic_2020_hourly | default | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| in-so_synthetic_2020_hourly | greenthread | 15.7 ± 0.3 | 15.7 ± 0.2 | 0.00 | 0.03 |
| in-so_synthetic_2020_hourly | heuristic | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| us-cal_2020_hourly | battery_only | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| us-cal_2020_hourly | carbon_only | 4.2 ± 4.3 | -0.0 ± 0.0 | 0.00 | 0.18 |
| us-cal_2020_hourly | combined_noai | 17.0 ± 3.4 | 14.7 ± 0.5 | 0.00 | 0.10 |
| us-cal_2020_hourly | default | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| us-cal_2020_hourly | greenthread | 18.1 ± 3.6 | 15.7 ± 0.2 | 0.00 | 0.14 |
| us-cal_2020_hourly | heuristic | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |

## E2 Ablations (all traces, AC, forecast noise 0.15)

| policy | carbon saving % | energy saving % | miss rate % | p95 slowdown |
|---|---|---|---|---|
| default | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 1.00 |
| greenthread | 18.1 ± 1.0 | 15.7 ± 0.1 | 0.00 | 4.30 |
| greenthread_v1 | 17.4 ± 0.7 | 15.7 ± 0.1 | 0.00 | 4.29 |
| gt_fixed_conf | 17.5 ± 0.8 | 15.7 ± 0.1 | 0.00 | 4.40 |
| gt_no_battery | 18.1 ± 1.0 | 15.7 ± 0.1 | 0.00 | 4.30 |
| gt_no_carbon | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 1.00 |
| gt_no_defer | 15.7 ± 0.1 | 15.7 ± 0.1 | 0.00 | 1.35 |
| gt_no_gate | 18.0 ± 1.0 | 15.7 ± 0.1 | 0.00 | 4.14 |
| gt_no_ml | 17.4 ± 1.0 | 14.9 ± 0.2 | 0.00 | 4.26 |
| gt_no_modes | 3.5 ± 1.3 | -0.0 ± 0.0 | 0.00 | 4.33 |
| gt_no_quantile | 18.1 ± 1.0 | 15.6 ± 0.1 | 0.00 | 4.33 |
| gt_no_uncertainty | 18.2 ± 1.0 | 15.7 ± 0.1 | 0.01 | 6.51 |

## E3 Carbon-signal quality (AC)

Forecast kinds: oracle = recorded future (+noise); seasonal = same hour yesterday (what a free Electricity Maps plan supports); persistence = latest value; shift = forecast phase error in hours.

| forecast | noise | outage | shift h | policy | carbon saving % | miss rate % |
|---|---|---|---|---|---|---|
| oracle | 0.0 | 0.0 | 0.0 | combined_noai | 18.2 ± 1.3 | 0.00 |
| oracle | 0.0 | 0.0 | 0.0 | default | 0.0 ± 0.0 | 0.00 |
| oracle | 0.0 | 0.0 | 0.0 | greenthread | 19.2 ± 1.3 | 0.00 |
| oracle | 0.0 | 0.0 | 0.0 | gt_fixed_conf | 18.0 ± 1.0 | 0.00 |
| oracle | 0.0 | 0.0 | 0.0 | gt_no_uncertainty | 19.2 ± 1.3 | 0.00 |
| oracle | 0.0 | 0.0 | 12.0 | combined_noai | 13.3 ± 1.1 | 0.00 |
| oracle | 0.0 | 0.0 | 12.0 | default | 0.0 ± 0.0 | 0.00 |
| oracle | 0.0 | 0.0 | 12.0 | greenthread | 16.3 ± 0.6 | 0.00 |
| oracle | 0.0 | 0.0 | 12.0 | gt_fixed_conf | 14.4 ± 0.9 | 0.00 |
| oracle | 0.0 | 0.0 | 12.0 | gt_no_uncertainty | 14.0 ± 1.1 | 0.00 |
| oracle | 0.15 | 0.0 | 0.0 | combined_noai | 17.3 ± 1.1 | 0.00 |
| oracle | 0.15 | 0.0 | 0.0 | default | 0.0 ± 0.0 | 0.00 |
| oracle | 0.15 | 0.0 | 0.0 | greenthread | 18.1 ± 1.0 | 0.00 |
| oracle | 0.15 | 0.0 | 0.0 | gt_fixed_conf | 17.5 ± 0.8 | 0.00 |
| oracle | 0.15 | 0.0 | 0.0 | gt_no_uncertainty | 18.2 ± 1.0 | 0.01 |
| oracle | 0.15 | 0.0 | 6.0 | combined_noai | 14.1 ± 0.4 | 0.00 |
| oracle | 0.15 | 0.0 | 6.0 | default | 0.0 ± 0.0 | 0.00 |
| oracle | 0.15 | 0.0 | 6.0 | greenthread | 15.8 ± 0.5 | 0.00 |
| oracle | 0.15 | 0.0 | 6.0 | gt_fixed_conf | 15.0 ± 0.3 | 0.00 |
| oracle | 0.15 | 0.0 | 6.0 | gt_no_uncertainty | 14.9 ± 0.4 | 0.00 |
| oracle | 0.15 | 0.3 | 0.0 | combined_noai | 17.5 ± 1.1 | 0.00 |
| oracle | 0.15 | 0.3 | 0.0 | default | 0.0 ± 0.0 | 0.00 |
| oracle | 0.15 | 0.3 | 0.0 | greenthread | 18.3 ± 1.0 | 0.00 |
| oracle | 0.15 | 0.3 | 0.0 | gt_fixed_conf | 16.8 ± 0.5 | 0.00 |
| oracle | 0.15 | 0.3 | 0.0 | gt_no_uncertainty | 18.5 ± 1.0 | 0.01 |
| oracle | 0.3 | 0.0 | 0.0 | combined_noai | 16.2 ± 0.8 | 0.00 |
| oracle | 0.3 | 0.0 | 0.0 | default | 0.0 ± 0.0 | 0.00 |
| oracle | 0.3 | 0.0 | 0.0 | greenthread | 17.5 ± 0.9 | 0.00 |
| oracle | 0.3 | 0.0 | 0.0 | gt_fixed_conf | 16.9 ± 0.5 | 0.00 |
| oracle | 0.3 | 0.0 | 0.0 | gt_no_uncertainty | 17.4 ± 0.7 | 0.00 |
| oracle | 0.5 | 0.0 | 0.0 | combined_noai | 15.2 ± 0.7 | 0.00 |
| oracle | 0.5 | 0.0 | 0.0 | default | 0.0 ± 0.0 | 0.00 |
| oracle | 0.5 | 0.0 | 0.0 | greenthread | 17.2 ± 0.8 | 0.00 |
| oracle | 0.5 | 0.0 | 0.0 | gt_fixed_conf | 16.5 ± 0.5 | 0.00 |
| oracle | 0.5 | 0.0 | 0.0 | gt_no_uncertainty | 16.6 ± 0.6 | 0.00 |
| persistence | 0.0 | 0.0 | 0.0 | combined_noai | 14.9 ± 0.2 | 0.00 |
| persistence | 0.0 | 0.0 | 0.0 | default | 0.0 ± 0.0 | 0.00 |
| persistence | 0.0 | 0.0 | 0.0 | greenthread | 16.2 ± 0.6 | 0.00 |
| persistence | 0.0 | 0.0 | 0.0 | gt_fixed_conf | 15.7 ± 0.1 | 0.00 |
| persistence | 0.0 | 0.0 | 0.0 | gt_no_uncertainty | 15.7 ± 0.1 | 0.00 |
| seasonal | 0.0 | 0.0 | 0.0 | combined_noai | 16.7 ± 0.9 | 0.01 |
| seasonal | 0.0 | 0.0 | 0.0 | default | 0.0 ± 0.0 | 0.00 |
| seasonal | 0.0 | 0.0 | 0.0 | greenthread | 17.4 ± 0.9 | 0.00 |
| seasonal | 0.0 | 0.0 | 0.0 | gt_fixed_conf | 17.0 ± 0.6 | 0.00 |
| seasonal | 0.0 | 0.0 | 0.0 | gt_no_uncertainty | 17.7 ± 0.9 | 0.00 |

## E4 Battery state of charge

| SoC | policy | energy saving % | miss rate % | p95 slowdown |
|---|---|---|---|---|
| 0.1 | battery_only | 15.1 ± 0.2 | 0.00 | 1.35 |
| 0.1 | combined_noai | 15.1 ± 0.2 | 0.00 | 1.35 |
| 0.1 | default | 0.0 ± 0.0 | 0.00 | 1.00 |
| 0.1 | greenthread | 15.7 ± 0.1 | 0.00 | 1.35 |
| 0.1 | greenthread_v1 | 15.7 ± 0.1 | 0.00 | 1.35 |
| 0.1 | gt_no_gate | 15.7 ± 0.1 | 0.00 | 1.35 |
| 0.1 | heuristic | 15.7 ± 0.1 | 0.00 | 1.35 |
| 0.3 | battery_only | 15.0 ± 0.2 | 0.00 | 1.35 |
| 0.3 | combined_noai | 15.0 ± 0.2 | 0.00 | 1.35 |
| 0.3 | default | 0.0 ± 0.0 | 0.00 | 1.00 |
| 0.3 | greenthread | 15.7 ± 0.1 | 0.00 | 1.35 |
| 0.3 | greenthread_v1 | 15.7 ± 0.1 | 0.00 | 1.35 |
| 0.3 | gt_no_gate | 15.7 ± 0.1 | 0.00 | 1.35 |
| 0.3 | heuristic | 15.7 ± 0.1 | 0.00 | 1.35 |
| 0.8 | battery_only | 14.9 ± 0.2 | 0.00 | 1.35 |
| 0.8 | combined_noai | 15.1 ± 0.2 | 0.00 | 1.35 |
| 0.8 | default | 0.0 ± 0.0 | 0.00 | 1.00 |
| 0.8 | greenthread | 15.7 ± 0.1 | 0.00 | 1.35 |
| 0.8 | greenthread_v1 | 15.7 ± 0.1 | 0.00 | 1.35 |
| 0.8 | gt_no_gate | 15.7 ± 0.1 | 0.00 | 1.35 |
| 0.8 | heuristic | 15.7 ± 0.1 | 0.00 | 1.35 |

## E5 Tight deadlines (deadline = 1.25-3.0 x normal runtime)

| power | escalation | policy | miss rate % | carbon saving % | energy saving % |
|---|---|---|---|---|---|
| battery 0.3 | False | default | 0.07 ± 0.06 | 0.0 ± 0.0 | 0.0 ± 0.0 |
| battery 0.3 | False | greenthread | 0.79 ± 0.14 | 8.5 ± 0.3 | 8.5 ± 0.3 |
| battery 0.3 | False | gt_no_quantile | 6.29 ± 0.41 | 13.6 ± 0.2 | 13.6 ± 0.2 |
| battery 0.3 | False | heuristic | 12.36 ± 0.94 | 15.9 ± 0.0 | 15.9 ± 0.0 |
| battery 0.3 | True | default | 0.07 ± 0.06 | 0.0 ± 0.0 | 0.0 ± 0.0 |
| battery 0.3 | True | greenthread | 0.71 ± 0.16 | 8.5 ± 0.3 | 8.5 ± 0.3 |
| battery 0.3 | True | gt_no_quantile | 0.71 ± 0.16 | 8.5 ± 0.3 | 8.5 ± 0.3 |
| battery 0.3 | True | heuristic | 12.36 ± 0.94 | 15.9 ± 0.0 | 15.9 ± 0.0 |
| AC | False | default | 0.07 ± 0.06 | 0.0 ± 0.0 | 0.0 ± 0.0 |
| AC | False | greenthread | 0.79 ± 0.14 | 8.5 ± 0.3 | 8.5 ± 0.3 |
| AC | False | gt_no_quantile | 6.26 ± 0.41 | 13.6 ± 0.2 | 13.6 ± 0.2 |
| AC | False | heuristic | 0.07 ± 0.06 | 0.0 ± 0.0 | 0.0 ± 0.0 |
| AC | True | default | 0.07 ± 0.06 | 0.0 ± 0.0 | 0.0 ± 0.0 |
| AC | True | greenthread | 0.71 ± 0.16 | 8.5 ± 0.3 | 8.5 ± 0.3 |
| AC | True | gt_no_quantile | 0.71 ± 0.16 | 8.5 ± 0.3 | 8.5 ± 0.3 |
| AC | True | heuristic | 0.07 ± 0.06 | 0.0 ± 0.0 | 0.0 ± 0.0 |

## E6 Mode-gate sensitivity to mode-effect error (battery, SoC 0.3)

| ML mode-effect error | policy | energy saving % | p95 slowdown |
|---|---|---|---|
| 0.02 | default | 0.0 ± 0.0 | 1.00 |
| 0.02 | greenthread | 15.7 ± 0.1 | 1.35 |
| 0.02 | gt_no_gate | 15.7 ± 0.1 | 1.35 |
| 0.02 | heuristic | 15.7 ± 0.1 | 1.35 |
| 0.05 | default | 0.0 ± 0.0 | 1.00 |
| 0.05 | greenthread | 15.7 ± 0.1 | 1.35 |
| 0.05 | gt_no_gate | 15.7 ± 0.1 | 1.35 |
| 0.05 | heuristic | 15.7 ± 0.1 | 1.35 |
| 0.1 | default | 0.0 ± 0.0 | 1.00 |
| 0.1 | greenthread | 15.5 ± 0.1 | 1.35 |
| 0.1 | gt_no_gate | 14.7 ± 0.3 | 1.35 |
| 0.1 | heuristic | 15.7 ± 0.1 | 1.35 |
| 0.2 | default | 0.0 ± 0.0 | 1.00 |
| 0.2 | greenthread | 15.6 ± 0.1 | 1.35 |
| 0.2 | gt_no_gate | 10.2 ± 0.5 | 2.98 |
| 0.2 | heuristic | 15.7 ± 0.1 | 1.35 |
| 0.3 | default | 0.0 ± 0.0 | 1.00 |
| 0.3 | greenthread | 15.6 ± 0.1 | 1.35 |
| 0.3 | gt_no_gate | 5.9 ± 0.7 | 4.72 |
| 0.3 | heuristic | 15.7 ± 0.1 | 1.35 |

## E7 Version-1 simulator assumption (independent per-mode errors, sd 0.145) vs measured structure

| error structure | policy | energy saving % |
|---|---|---|
| independent | default | 0.0 ± 0.0 |
| independent | greenthread | 15.6 ± 0.1 |
| independent | greenthread_v1 | 11.5 ± 0.3 |
| independent | heuristic | 15.7 ± 0.1 |
| shared | default | 0.0 ± 0.0 |
| shared | greenthread | 15.7 ± 0.1 |
| shared | greenthread_v1 | 15.7 ± 0.1 |
| shared | heuristic | 15.7 ± 0.1 |

## Tuning split: gate strength kappa (Jan-Jun windows, AC and battery pooled)

| kappa | carbon saving % | energy saving % | miss rate % |
|---|---|---|---|
| 0.0 | 18.17 | 15.54 | 0.00 |
| 0.5 | 18.18 | 15.55 | 0.00 |
| 1.0 | 18.21 | 15.55 | 0.00 |
| 2.0 | 18.32 | 15.55 | 0.00 |
