# Simulation results (generated)

_Simulation: energy from SimMachine model (assumptions), carbon from real 2020 traces (DE/GB/FR/US-CAL; ENTSO-E/CAISO via Let's Wait Awhile) and one SYNTHETIC India trace._

Predictor error levels: {'source': 'results/model_metrics.json', 'ml': 0.14514681325083098, 'table': 0.2569735310116113}. Seeds per trace: 10, days: 14.
Savings are relative to `default` (start now, OS scheduling) for the same seed and trace; ± is a 95% CI.

## E1 Baselines on AC

| trace | policy | carbon saving % | energy saving % | miss rate % | mean delay h |
|---|---|---|---|---|---|
| de_2020_hourly | battery_only | 2.8 ± 0.9 | 2.9 ± 1.0 | 0.00 | 0.00 |
| de_2020_hourly | carbon_only | 4.7 ± 1.3 | 0.0 ± 0.0 | 0.00 | 0.53 |
| de_2020_hourly | combined_noai | 11.1 ± 1.0 | 8.3 ± 1.0 | 0.07 | 0.32 |
| de_2020_hourly | default | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| de_2020_hourly | greenthread | 13.3 ± 1.3 | 11.3 ± 0.9 | 0.00 | 0.17 |
| de_2020_hourly | heuristic | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| fr_2020_hourly | battery_only | 2.8 ± 0.9 | 2.9 ± 1.0 | 0.00 | 0.00 |
| fr_2020_hourly | carbon_only | 2.1 ± 0.8 | 0.0 ± 0.0 | 0.00 | 0.22 |
| fr_2020_hourly | combined_noai | 9.8 ± 0.8 | 8.2 ± 0.9 | 0.07 | 0.14 |
| fr_2020_hourly | default | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| fr_2020_hourly | greenthread | 12.2 ± 1.0 | 11.4 ± 0.7 | 0.00 | 0.04 |
| fr_2020_hourly | heuristic | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| gb_2020_hourly | battery_only | 2.7 ± 0.8 | 2.9 ± 1.0 | 0.00 | 0.00 |
| gb_2020_hourly | carbon_only | 13.0 ± 1.5 | 0.0 ± 0.0 | 0.00 | 1.12 |
| gb_2020_hourly | combined_noai | 18.3 ± 1.4 | 8.3 ± 0.7 | 0.07 | 0.83 |
| gb_2020_hourly | default | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| gb_2020_hourly | greenthread | 19.9 ± 1.7 | 11.2 ± 0.8 | 0.00 | 0.67 |
| gb_2020_hourly | heuristic | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| in-so_synthetic_2020_hourly | battery_only | 2.9 ± 1.0 | 2.9 ± 1.0 | 0.00 | 0.00 |
| in-so_synthetic_2020_hourly | carbon_only | 0.3 ± 0.2 | 0.0 ± 0.0 | 0.00 | 0.04 |
| in-so_synthetic_2020_hourly | combined_noai | 8.1 ± 1.0 | 8.3 ± 0.9 | 0.07 | 0.02 |
| in-so_synthetic_2020_hourly | default | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| in-so_synthetic_2020_hourly | greenthread | 11.2 ± 1.0 | 11.2 ± 1.0 | 0.00 | 0.01 |
| in-so_synthetic_2020_hourly | heuristic | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| us-cal_2020_hourly | battery_only | 2.9 ± 0.9 | 2.9 ± 1.0 | 0.00 | 0.00 |
| us-cal_2020_hourly | carbon_only | 1.7 ± 1.1 | 0.0 ± 0.0 | 0.00 | 0.14 |
| us-cal_2020_hourly | combined_noai | 8.5 ± 1.1 | 8.2 ± 0.9 | 0.07 | 0.07 |
| us-cal_2020_hourly | default | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |
| us-cal_2020_hourly | greenthread | 11.4 ± 1.0 | 11.0 ± 0.9 | 0.00 | 0.03 |
| us-cal_2020_hourly | heuristic | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 0.00 |

## E2 Ablations (all traces, forecast noise 0.15)

| policy | carbon saving % | energy saving % | miss rate % | p95 slowdown | escalations/run |
|---|---|---|---|---|---|
| default | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.00 | 1.00 | 0.0 |
| greenthread | 12.7 ± 0.8 | 11.0 ± 0.4 | 0.00 | 8.12 | 0.0 |
| gt_no_battery | 12.7 ± 0.8 | 11.0 ± 0.4 | 0.00 | 8.12 | 0.0 |
| gt_no_carbon | 1.2 ± 0.1 | 1.2 ± 0.1 | 0.00 | 1.25 | 0.0 |
| gt_no_defer | 11.6 ± 0.4 | 11.4 ± 0.4 | 0.00 | 1.41 | 0.0 |
| gt_no_ml | 9.9 ± 0.7 | 8.2 ± 0.3 | 0.07 | 7.66 | 0.2 |
| gt_no_modes | 2.3 ± 0.8 | 0.0 ± 0.0 | 0.00 | 9.07 | 0.0 |
| gt_no_quantile | 12.7 ± 0.8 | 11.0 ± 0.4 | 0.00 | 8.12 | 0.0 |
| gt_no_uncertainty | 13.5 ± 1.0 | 11.0 ± 0.4 | 0.00 | 11.10 | 0.0 |

## E3 Uncertainty stress (forecast error x signal outage)

| forecast noise | outage frac | policy | carbon saving % | miss rate % |
|---|---|---|---|---|
| 0.0 | 0.0 | combined_noai | 11.2 ± 1.1 | 0.07 |
| 0.0 | 0.0 | default | 0.0 ± 0.0 | 0.00 |
| 0.0 | 0.0 | greenthread | 13.6 ± 1.0 | 0.00 |
| 0.0 | 0.0 | gt_no_uncertainty | 14.5 ± 1.2 | 0.00 |
| 0.0 | 0.15 | combined_noai | 11.2 ± 1.1 | 0.07 |
| 0.0 | 0.15 | default | 0.0 ± 0.0 | 0.00 |
| 0.0 | 0.15 | greenthread | 13.1 ± 0.9 | 0.00 |
| 0.0 | 0.15 | gt_no_uncertainty | 14.5 ± 1.2 | 0.00 |
| 0.0 | 0.3 | combined_noai | 11.2 ± 1.1 | 0.07 |
| 0.0 | 0.3 | default | 0.0 ± 0.0 | 0.00 |
| 0.0 | 0.3 | greenthread | 12.7 ± 0.7 | 0.00 |
| 0.0 | 0.3 | gt_no_uncertainty | 14.5 ± 1.2 | 0.00 |
| 0.15 | 0.0 | combined_noai | 10.5 ± 0.9 | 0.07 |
| 0.15 | 0.0 | default | 0.0 ± 0.0 | 0.00 |
| 0.15 | 0.0 | greenthread | 12.7 ± 0.8 | 0.00 |
| 0.15 | 0.0 | gt_no_uncertainty | 13.5 ± 1.0 | 0.00 |
| 0.15 | 0.15 | combined_noai | 10.5 ± 0.9 | 0.07 |
| 0.15 | 0.15 | default | 0.0 ± 0.0 | 0.00 |
| 0.15 | 0.15 | greenthread | 12.6 ± 0.7 | 0.00 |
| 0.15 | 0.15 | gt_no_uncertainty | 13.5 ± 1.0 | 0.00 |
| 0.15 | 0.3 | combined_noai | 10.5 ± 0.9 | 0.07 |
| 0.15 | 0.3 | default | 0.0 ± 0.0 | 0.00 |
| 0.15 | 0.3 | greenthread | 12.3 ± 0.6 | 0.00 |
| 0.15 | 0.3 | gt_no_uncertainty | 13.5 ± 1.0 | 0.00 |
| 0.3 | 0.0 | combined_noai | 9.7 ± 0.8 | 0.09 |
| 0.3 | 0.0 | default | 0.0 ± 0.0 | 0.00 |
| 0.3 | 0.0 | greenthread | 12.0 ± 0.8 | 0.00 |
| 0.3 | 0.0 | gt_no_uncertainty | 12.0 ± 1.0 | 0.00 |
| 0.3 | 0.15 | combined_noai | 9.6 ± 0.9 | 0.09 |
| 0.3 | 0.15 | default | 0.0 ± 0.0 | 0.00 |
| 0.3 | 0.15 | greenthread | 12.0 ± 0.7 | 0.00 |
| 0.3 | 0.15 | gt_no_uncertainty | 12.0 ± 1.0 | 0.00 |
| 0.3 | 0.3 | combined_noai | 9.7 ± 0.9 | 0.09 |
| 0.3 | 0.3 | default | 0.0 ± 0.0 | 0.00 |
| 0.3 | 0.3 | greenthread | 11.8 ± 0.7 | 0.00 |
| 0.3 | 0.3 | gt_no_uncertainty | 12.0 ± 1.0 | 0.00 |
| 0.5 | 0.0 | combined_noai | 9.0 ± 0.9 | 0.09 |
| 0.5 | 0.0 | default | 0.0 ± 0.0 | 0.00 |
| 0.5 | 0.0 | greenthread | 10.9 ± 0.8 | 0.00 |
| 0.5 | 0.0 | gt_no_uncertainty | 10.7 ± 1.0 | 0.00 |
| 0.5 | 0.15 | combined_noai | 9.1 ± 0.9 | 0.09 |
| 0.5 | 0.15 | default | 0.0 ± 0.0 | 0.00 |
| 0.5 | 0.15 | greenthread | 11.0 ± 0.8 | 0.00 |
| 0.5 | 0.15 | gt_no_uncertainty | 10.7 ± 1.0 | 0.00 |
| 0.5 | 0.3 | combined_noai | 8.8 ± 0.9 | 0.09 |
| 0.5 | 0.3 | default | 0.0 ± 0.0 | 0.00 |
| 0.5 | 0.3 | greenthread | 11.0 ± 0.7 | 0.00 |
| 0.5 | 0.3 | gt_no_uncertainty | 10.7 ± 1.0 | 0.00 |

## E4 Battery state of charge (carbon via battery ledger)

| SoC | policy | energy saving % | miss rate % | p95 slowdown |
|---|---|---|---|---|
| 0.1 | battery_only | 7.6 ± 0.4 | 0.07 | 4.02 |
| 0.1 | combined_noai | 7.4 ± 0.4 | 0.07 | 4.02 |
| 0.1 | default | 0.0 ± 0.0 | 0.00 | 1.00 |
| 0.1 | greenthread | 11.3 ± 0.3 | 0.00 | 1.75 |
| 0.1 | heuristic | 15.7 ± 0.1 | 0.00 | 1.35 |
| 0.3 | battery_only | 8.2 ± 0.4 | 0.07 | 3.30 |
| 0.3 | combined_noai | 8.0 ± 0.3 | 0.07 | 3.76 |
| 0.3 | default | 0.0 ± 0.0 | 0.00 | 1.00 |
| 0.3 | greenthread | 11.5 ± 0.3 | 0.00 | 1.62 |
| 0.3 | heuristic | 15.7 ± 0.1 | 0.00 | 1.35 |
| 0.8 | battery_only | 8.3 ± 0.3 | 0.07 | 2.89 |
| 0.8 | combined_noai | 8.1 ± 0.4 | 0.07 | 3.63 |
| 0.8 | default | 0.0 ± 0.0 | 0.00 | 1.00 |
| 0.8 | greenthread | 11.5 ± 0.3 | 0.00 | 1.62 |
| 0.8 | heuristic | 15.7 ± 0.1 | 0.00 | 1.35 |

## E5 Prediction-error sensitivity

| ML log-error sd | policy | carbon saving % | miss rate % |
|---|---|---|---|
| 0.05 | default | 0.0 ± 0.0 | 0.00 |
| 0.05 | greenthread | 17.1 ± 0.7 | 0.00 |
| 0.05 | gt_no_quantile | 17.1 ± 0.7 | 0.00 |
| 0.15 | default | 0.0 ± 0.0 | 0.00 |
| 0.15 | greenthread | 12.4 ± 0.8 | 0.00 |
| 0.15 | gt_no_quantile | 12.4 ± 0.8 | 0.00 |
| 0.3 | default | 0.0 ± 0.0 | 0.00 |
| 0.3 | greenthread | 7.2 ± 0.9 | 0.00 |
| 0.3 | gt_no_quantile | 7.1 ± 0.9 | 0.00 |
| 0.5 | default | 0.0 ± 0.0 | 0.00 |
| 0.5 | greenthread | 3.8 ± 1.0 | 0.07 |
| 0.5 | gt_no_quantile | 3.6 ± 1.0 | 0.07 |
