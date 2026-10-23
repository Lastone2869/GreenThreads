# Simulation results (generated)

_Simulation: energy from SimMachine model (assumptions unless --calibrate with measured data), carbon from real 2020 traces (DE/GB/FR/US-CAL; ENTSO-E/CAISO via Let's Wait Awhile) and one SYNTHETIC India trace._

Predictor errors (noise removed): absolute {'ml': 0.11783286100644509, 'table': 0.42148431035434386}, mode effect {'ml': 0.02, 'table': 0.10750790419857334}; run-to-run noise 0.119 (source: results\model_metrics.json).
test split: 14-day windows starting 2020-07-01 + 14*seed days (Jul-Dec); tuning split (kappa sweep only): windows from 2020-01-15 (Jan-Jun). Seeds per trace: 10, days per window: 14.
Savings are relative to `default` (start now, OS scheduling) for the same window and jobs; ± is a 95% CI.

## E1 Baselines on AC

| trace | policy | carbon saving % | energy saving % | miss rate % | mean delay h |
|---|---|---|---|---|---|
| de_2020_hourly | battery_only | -613.2 ± 84.9 | -623.4 ± 88.1 | 0.00 | 0.00 |
| de_2020_hourly | carbon_only | 4.8 ± 2.6 | 0.0 ± 0.0 | 0.00 | 0.36 |
| de_2020_hourly | combined_noai | 4.6 ± 2.6 | -0.2 ± 0.2 | 0.00 | 0.34 |
| de_2020_hourly | default | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| de_2020_hourly | greenthread | 5.1 ± 2.6 | 0.0 ± 0.0 | 0.07 | 0.38 |
| de_2020_hourly | heuristic | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| fr_2020_hourly | battery_only | -620.4 ± 86.0 | -623.4 ± 88.1 | 0.00 | 0.00 |
| fr_2020_hourly | carbon_only | 4.5 ± 2.5 | 0.0 ± 0.0 | 0.00 | 0.42 |
| fr_2020_hourly | combined_noai | 4.1 ± 2.6 | -0.1 ± 0.1 | 0.00 | 0.39 |
| fr_2020_hourly | default | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| fr_2020_hourly | greenthread | 4.7 ± 2.7 | 0.0 ± 0.0 | 0.00 | 0.44 |
| fr_2020_hourly | heuristic | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| gb_2020_hourly | battery_only | -629.8 ± 85.8 | -623.4 ± 88.1 | 0.00 | 0.00 |
| gb_2020_hourly | carbon_only | 10.6 ± 3.5 | 0.0 ± 0.0 | 0.00 | 0.85 |
| gb_2020_hourly | combined_noai | 10.5 ± 3.9 | -0.2 ± 0.2 | 0.00 | 0.81 |
| gb_2020_hourly | default | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| gb_2020_hourly | greenthread | 11.5 ± 4.1 | 0.0 ± 0.0 | 0.00 | 0.91 |
| gb_2020_hourly | heuristic | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| in-so_synthetic_2020_hourly | battery_only | -623.7 ± 89.6 | -623.4 ± 88.1 | 0.00 | 0.00 |
| in-so_synthetic_2020_hourly | carbon_only | 0.2 ± 0.1 | 0.0 ± 0.0 | 0.00 | 0.04 |
| in-so_synthetic_2020_hourly | combined_noai | 0.1 ± 0.1 | -0.1 ± 0.1 | 0.00 | 0.03 |
| in-so_synthetic_2020_hourly | default | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| in-so_synthetic_2020_hourly | greenthread | 0.3 ± 0.2 | 0.0 ± 0.0 | 0.00 | 0.04 |
| in-so_synthetic_2020_hourly | heuristic | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| us-cal_2020_hourly | battery_only | -623.0 ± 94.3 | -623.4 ± 88.1 | 0.00 | 0.00 |
| us-cal_2020_hourly | carbon_only | 3.5 ± 4.3 | 0.0 ± 0.0 | 0.00 | 0.16 |
| us-cal_2020_hourly | combined_noai | 3.1 ± 4.0 | -0.1 ± 0.1 | 0.00 | 0.13 |
| us-cal_2020_hourly | default | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| us-cal_2020_hourly | greenthread | 4.1 ± 4.4 | -0.0 ± 0.0 | 0.00 | 0.18 |
| us-cal_2020_hourly | heuristic | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |

## E2 Ablations (all traces, AC, forecast noise 0.15)

| policy | carbon saving % | energy saving % | miss rate % | p95 slowdown |
|---|---|---|---|---|
| default | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 1.00 |
| greenthread | 3.5 ± 1.3 | 0.0 ± 0.0 | 0.00 | 4.36 |
| greenthread_v1 | 2.8 ± 1.0 | 0.0 ± 0.0 | 0.00 | 4.49 |
| gt_fixed_conf | 2.8 ± 1.0 | 0.0 ± 0.0 | 0.00 | 4.49 |
| gt_no_battery | 3.5 ± 1.3 | 0.0 ± 0.0 | 0.00 | 4.36 |
| gt_no_carbon | -428.9 ± 28.3 | -426.8 ± 27.7 | 0.00 | 1.01 |
| gt_no_defer | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 1.00 |
| gt_no_gate | 3.5 ± 1.3 | 0.0 ± 0.0 | 0.00 | 4.32 |
| gt_no_ml | 3.2 ± 1.2 | 0.0 ± 0.0 | 0.00 | 4.21 |
| gt_no_modes | 3.5 ± 1.3 | -0.0 ± 0.0 | 0.00 | 4.33 |
| gt_no_quantile | 3.7 ± 1.4 | 0.0 ± 0.0 | 0.00 | 4.37 |
| gt_no_uncertainty | 3.6 ± 1.3 | 0.0 ± 0.0 | 0.01 | 6.61 |

## E3 Carbon-signal quality (AC)

Forecast kinds: oracle = recorded future (+noise); seasonal = same hour yesterday (what a free Electricity Maps plan supports); persistence = latest value; shift = forecast phase error in hours.

| forecast | noise | outage | shift h | policy | carbon saving % | miss rate % |
|---|---|---|---|---|---|---|
| oracle | 0.0 | 0.0 | 0.0 | combined_noai | 4.5 ± 1.5 | 0.00 |
| oracle | 0.0 | 0.0 | 0.0 | default | 0.0 ± 0.0 | 0.00 |
| oracle | 0.0 | 0.0 | 0.0 | greenthread | 5.1 ± 1.6 | 0.01 |
| oracle | 0.0 | 0.0 | 0.0 | gt_fixed_conf | 3.6 ± 1.3 | 0.00 |
| oracle | 0.0 | 0.0 | 0.0 | gt_no_uncertainty | 5.1 ± 1.6 | 0.01 |
| oracle | 0.0 | 0.0 | 12.0 | combined_noai | -2.0 ± 1.3 | 0.00 |
| oracle | 0.0 | 0.0 | 12.0 | default | 0.0 ± 0.0 | 0.00 |
| oracle | 0.0 | 0.0 | 12.0 | greenthread | 0.9 ± 0.7 | 0.00 |
| oracle | 0.0 | 0.0 | 12.0 | gt_fixed_conf | -1.8 ± 1.1 | 0.00 |
| oracle | 0.0 | 0.0 | 12.0 | gt_no_uncertainty | -2.1 ± 1.3 | 0.00 |
| oracle | 0.15 | 0.0 | 0.0 | combined_noai | 3.3 ± 1.2 | 0.00 |
| oracle | 0.15 | 0.0 | 0.0 | default | 0.0 ± 0.0 | 0.00 |
| oracle | 0.15 | 0.0 | 0.0 | greenthread | 3.5 ± 1.3 | 0.00 |
| oracle | 0.15 | 0.0 | 0.0 | gt_fixed_conf | 2.8 ± 1.0 | 0.00 |
| oracle | 0.15 | 0.0 | 0.0 | gt_no_uncertainty | 3.6 ± 1.3 | 0.01 |
| oracle | 0.15 | 0.0 | 6.0 | combined_noai | -1.1 ± 0.5 | 0.00 |
| oracle | 0.15 | 0.0 | 6.0 | default | 0.0 ± 0.0 | 0.00 |
| oracle | 0.15 | 0.0 | 6.0 | greenthread | 0.3 ± 0.6 | 0.00 |
| oracle | 0.15 | 0.0 | 6.0 | gt_fixed_conf | -0.9 ± 0.4 | 0.00 |
| oracle | 0.15 | 0.0 | 6.0 | gt_no_uncertainty | -1.0 ± 0.5 | 0.00 |
| oracle | 0.15 | 0.3 | 0.0 | combined_noai | 3.4 ± 1.3 | 0.00 |
| oracle | 0.15 | 0.3 | 0.0 | default | 0.0 ± 0.0 | 0.00 |
| oracle | 0.15 | 0.3 | 0.0 | greenthread | 3.6 ± 1.2 | 0.00 |
| oracle | 0.15 | 0.3 | 0.0 | gt_fixed_conf | 1.7 ± 0.6 | 0.00 |
| oracle | 0.15 | 0.3 | 0.0 | gt_no_uncertainty | 3.8 ± 1.3 | 0.01 |
| oracle | 0.3 | 0.0 | 0.0 | combined_noai | 2.3 ± 1.0 | 0.00 |
| oracle | 0.3 | 0.0 | 0.0 | default | 0.0 ± 0.0 | 0.00 |
| oracle | 0.3 | 0.0 | 0.0 | greenthread | 2.6 ± 1.0 | 0.00 |
| oracle | 0.3 | 0.0 | 0.0 | gt_fixed_conf | 1.8 ± 0.7 | 0.00 |
| oracle | 0.3 | 0.0 | 0.0 | gt_no_uncertainty | 2.6 ± 1.0 | 0.00 |
| oracle | 0.5 | 0.0 | 0.0 | combined_noai | 1.3 ± 0.7 | 0.00 |
| oracle | 0.5 | 0.0 | 0.0 | default | 0.0 ± 0.0 | 0.00 |
| oracle | 0.5 | 0.0 | 0.0 | greenthread | 2.3 ± 1.0 | 0.00 |
| oracle | 0.5 | 0.0 | 0.0 | gt_fixed_conf | 1.2 ± 0.6 | 0.00 |
| oracle | 0.5 | 0.0 | 0.0 | gt_no_uncertainty | 1.5 ± 0.8 | 0.00 |
| persistence | 0.0 | 0.0 | 0.0 | combined_noai | -0.1 ± 0.0 | 0.00 |
| persistence | 0.0 | 0.0 | 0.0 | default | 0.0 ± 0.0 | 0.00 |
| persistence | 0.0 | 0.0 | 0.0 | greenthread | 0.9 ± 0.8 | 0.00 |
| persistence | 0.0 | 0.0 | 0.0 | gt_fixed_conf | 0.0 ± 0.0 | 0.00 |
| persistence | 0.0 | 0.0 | 0.0 | gt_no_uncertainty | 0.0 ± 0.0 | 0.00 |
| seasonal | 0.0 | 0.0 | 0.0 | combined_noai | 2.6 ± 1.1 | 0.00 |
| seasonal | 0.0 | 0.0 | 0.0 | default | 0.0 ± 0.0 | 0.00 |
| seasonal | 0.0 | 0.0 | 0.0 | greenthread | 2.6 ± 1.1 | 0.00 |
| seasonal | 0.0 | 0.0 | 0.0 | gt_fixed_conf | 2.3 ± 1.0 | 0.00 |
| seasonal | 0.0 | 0.0 | 0.0 | gt_no_uncertainty | 2.3 ± 1.8 | 0.00 |

## E4 Battery state of charge

| SoC | policy | energy saving % | miss rate % | p95 slowdown |
|---|---|---|---|---|
| 0.1 | battery_only | -1.5 ± 0.2 | 0.00 | 1.00 |
| 0.1 | combined_noai | -1.5 ± 0.2 | 0.00 | 1.00 |
| 0.1 | default | 0.0 ± 0.0 | 0.00 | 1.00 |
| 0.1 | greenthread | -0.0 ± 0.0 | 0.00 | 1.00 |
| 0.1 | greenthread_v1 | -0.3 ± 0.0 | 0.00 | 1.00 |
| 0.1 | gt_no_gate | -0.3 ± 0.0 | 0.00 | 1.00 |
| 0.1 | heuristic | -2.4 ± 0.0 | 0.00 | 1.00 |
| 0.3 | battery_only | -1.5 ± 0.2 | 0.00 | 1.00 |
| 0.3 | combined_noai | -1.5 ± 0.2 | 0.00 | 1.00 |
| 0.3 | default | 0.0 ± 0.0 | 0.00 | 1.00 |
| 0.3 | greenthread | -0.0 ± 0.0 | 0.00 | 1.00 |
| 0.3 | greenthread_v1 | -0.3 ± 0.0 | 0.00 | 1.00 |
| 0.3 | gt_no_gate | -0.3 ± 0.0 | 0.00 | 1.00 |
| 0.3 | heuristic | -2.4 ± 0.0 | 0.00 | 1.00 |
| 0.8 | battery_only | -1.5 ± 0.2 | 0.00 | 1.00 |
| 0.8 | combined_noai | -1.5 ± 0.2 | 0.00 | 1.00 |
| 0.8 | default | 0.0 ± 0.0 | 0.00 | 1.00 |
| 0.8 | greenthread | -0.0 ± 0.0 | 0.00 | 1.00 |
| 0.8 | greenthread_v1 | -0.3 ± 0.0 | 0.00 | 1.00 |
| 0.8 | gt_no_gate | -0.3 ± 0.0 | 0.00 | 1.00 |
| 0.8 | heuristic | -2.4 ± 0.0 | 0.00 | 1.00 |

## E5 Tight deadlines (deadline = 1.25-3.0 x normal runtime)

| power | escalation | policy | miss rate % | carbon saving % | energy saving % |
|---|---|---|---|---|---|
| battery 0.3 | False | default | 0.07 ± 0.06 | 0.0 ± 0.0 | 0.0 ± 0.0 |
| battery 0.3 | False | greenthread | 0.07 ± 0.06 | -0.0 ± 0.0 | -0.0 ± 0.0 |
| battery 0.3 | False | gt_no_quantile | 0.07 ± 0.06 | -0.0 ± 0.0 | -0.0 ± 0.0 |
| battery 0.3 | False | heuristic | 0.14 ± 0.12 | -2.4 ± 0.0 | -2.4 ± 0.0 |
| battery 0.3 | True | default | 0.07 ± 0.06 | 0.0 ± 0.0 | 0.0 ± 0.0 |
| battery 0.3 | True | greenthread | 0.07 ± 0.06 | -0.0 ± 0.0 | -0.0 ± 0.0 |
| battery 0.3 | True | gt_no_quantile | 0.07 ± 0.06 | -0.0 ± 0.0 | -0.0 ± 0.0 |
| battery 0.3 | True | heuristic | 0.14 ± 0.12 | -2.4 ± 0.0 | -2.4 ± 0.0 |
| AC | False | default | 0.07 ± 0.06 | 0.0 ± 0.0 | 0.0 ± 0.0 |
| AC | False | greenthread | 0.07 ± 0.06 | -0.0 ± 0.0 | -0.0 ± 0.0 |
| AC | False | gt_no_quantile | 0.07 ± 0.06 | -0.0 ± 0.0 | -0.0 ± 0.0 |
| AC | False | heuristic | 0.07 ± 0.06 | 0.0 ± 0.0 | 0.0 ± 0.0 |
| AC | True | default | 0.07 ± 0.06 | 0.0 ± 0.0 | 0.0 ± 0.0 |
| AC | True | greenthread | 0.07 ± 0.06 | -0.0 ± 0.0 | -0.0 ± 0.0 |
| AC | True | gt_no_quantile | 0.07 ± 0.06 | -0.0 ± 0.0 | -0.0 ± 0.0 |
| AC | True | heuristic | 0.07 ± 0.06 | 0.0 ± 0.0 | 0.0 ± 0.0 |

## E6 Mode-gate sensitivity to mode-effect error (battery, SoC 0.3)

| ML mode-effect error | policy | energy saving % | p95 slowdown |
|---|---|---|---|
| 0.02 | default | 0.0 ± 0.0 | 1.00 |
| 0.02 | greenthread | -0.0 ± 0.0 | 1.00 |
| 0.02 | gt_no_gate | -0.3 ± 0.0 | 1.00 |
| 0.02 | heuristic | -2.4 ± 0.0 | 1.00 |
| 0.05 | default | 0.0 ± 0.0 | 1.00 |
| 0.05 | greenthread | -0.0 ± 0.0 | 1.00 |
| 0.05 | gt_no_gate | -0.8 ± 0.1 | 1.00 |
| 0.05 | heuristic | -2.4 ± 0.0 | 1.00 |
| 0.1 | default | 0.0 ± 0.0 | 1.00 |
| 0.1 | greenthread | -0.0 ± 0.0 | 1.00 |
| 0.1 | gt_no_gate | -1.4 ± 0.1 | 1.00 |
| 0.1 | heuristic | -2.4 ± 0.0 | 1.00 |
| 0.2 | default | 0.0 ± 0.0 | 1.00 |
| 0.2 | greenthread | 0.0 ± 0.0 | 1.00 |
| 0.2 | gt_no_gate | -4.4 ± 0.4 | 2.00 |
| 0.2 | heuristic | -2.4 ± 0.0 | 1.00 |
| 0.3 | default | 0.0 ± 0.0 | 1.00 |
| 0.3 | greenthread | 0.0 ± 0.0 | 1.00 |
| 0.3 | gt_no_gate | -7.1 ± 0.6 | 2.01 |
| 0.3 | heuristic | -2.4 ± 0.0 | 1.00 |

## E7 Version-1 simulator assumption (independent per-mode errors, sd 0.145) vs measured structure

| error structure | policy | energy saving % |
|---|---|---|
| independent | default | 0.0 ± 0.0 |
| independent | greenthread | 0.0 ± 0.0 |
| independent | greenthread_v1 | -2.8 ± 0.3 |
| independent | heuristic | -2.4 ± 0.0 |
| shared | default | 0.0 ± 0.0 |
| shared | greenthread | -0.0 ± 0.0 |
| shared | greenthread_v1 | -0.3 ± 0.0 |
| shared | heuristic | -2.4 ± 0.0 |

## Tuning split: gate strength kappa (Jan-Jun windows, AC and battery pooled)

| kappa | carbon saving % | energy saving % | miss rate % |
|---|---|---|---|
| 0.0 | 3.66 | -0.00 | 0.00 |
| 0.5 | 3.66 | -0.00 | 0.00 |
| 1.0 | 3.66 | -0.00 | 0.00 |
| 2.0 | 3.66 | -0.00 | 0.00 |
