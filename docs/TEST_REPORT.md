# GreenThread — Test and Results Report

Generated 13 Oct 2026. Every number below comes from files in `results/`. Nothing in this
report was measured on Windows: the Windows-specific numbers have to be produced on the
laptop with `scripts\run_all_windows.ps1`.

## 1. Environments

| | Development machine (all results below) | Target (to run) |
|---|---|---|
| OS | Linux 6.18 VM | Windows 11 |
| CPU | Intel Xeon @ 2.10 GHz, 2 vCPU | i7 10th gen (4C/8T) or Ryzen 7 7445HS (6C/12T) — both homogeneous cores |
| Backend | `posix`: ECO = nice(10), HALF = sched_setaffinity | `windows`: EcoQoS, priority, CPU Sets, Job Objects |
| Energy | **model only** (no battery, no RAPL) | Battery discharge (WMI) on battery; model on AC |

## 2. Automated tests — 37/37 pass (`results/pytest_output.txt`)

| Area | Tests | What is checked |
|---|---|---|
| Windows ABI & logic (mocked kernel32) | 8 | EcoQoS struct = 12 bytes with Control/State masks; BELOW_NORMAL priority; CPU Sets pick one logical CPU per core; E-cores = lowest EfficiencyClass; HighQoS escalation clears CPU Sets; CPU-Set failure → affinity fallback; OpenProcess failure reported not raised; Job Object hard cap = 8-byte struct, rate×100 |
| Capability / parsing | 3 | SYSTEM_CPU_SET_INFORMATION parsing (hybrid layout); mode availability on homogeneous/hybrid/1-core |
| Carbon | 4 | Trace time mapping & compression; deterministic forecast noise; outage → stale value, confidence decay; unaware mode trusts raw |
| Accounting / config | 4 | Ledger weighted intensity; charge vs discharge; single-path attribution (AC vs battery); unknown config keys rejected |
| Policy properties | 8 | Interactive never deferred/throttled; non-deferrable starts now; deferral lands in clean window and meets deadline; quantile constraint excludes slow modes; infeasible → NORMAL now; battery → no deferral, low SoC → ECO; vectorised objective equals brute force; stale signal → no deferral |
| ML | 3 | ML beats table on held-out data; q90 coverage within [0.75, 1]; save/load; table fallbacks |
| Integration (real processes) | 6 | Engine defers a job into the low-carbon window of a compressed trace and accounts it at 100 g/kWh vs 500 g/kWh; interactive probe stays NORMAL; escalation to BOOST when deadline at risk; real nice/affinity applied; null backend dry run; simulator determinism; CLI `detect`/`carbon` |

Bugs these tests caught during development: `wintypes.ULONG` is 8 bytes on LP64 (struct sizes
24/16 instead of 12/8) → fixed-width `c_uint32`; HANDLE return types truncated on 64-bit →
explicit `restype`/`argtypes`; receding-horizon replanning procrastinated (start time kept
sliding later) → commitment rule (replans may only move a start earlier).

## 3. Prediction layer (real data, `results/model_metrics.json`)

384 real runs on the development machine (3 workload kinds × 4 sizes × {1,2} threads × 4
modes × {0,1} background threads × 2 reps, randomised order).

| Split | Predictor | MAE (s) | MAPE % | Pinball q0.9 | q0.9 coverage | Upper margin % | log-err sd |
|---|---|---|---|---|---|---|---|
| Temporal (train 60 / calib 20 / test 20) | **ML (GBDT quantile)** | **0.067** | **10.9** | **0.017** | 0.896 | 28.4 | **0.145** |
| | Table baseline | 0.112 | 19.9 | 0.034 | 0.870 | 36.0 | 0.257 |
| Unseen largest size per kind | **ML** | **0.172** | **9.6** | **0.051** | 0.865 | 30.0 | **0.125** |
| | Table baseline | 0.312 | 21.6 | 0.091 | 0.938 | 48.8 | 0.276 |

* ML roughly halves the error and gives tighter upper bounds at similar coverage (target 0.90).
* The first ML version predicted absolute runtime and had **0 % q0.9 coverage** on unseen sizes
  (trees cannot extrapolate); switching to per-unit targets fixed this. Kept as a documented
  negative result.
* Inference 7.1 ms per call (all modes batched), model 1.5 MB, fully local.

## 4. Real-process experiment (`results/experiment_linux_testbed/`)

Workload `configs/jobs_demo.json`: 6 batch jobs (3 deferrable) + 2 interactive probes; carbon
trace DE 2020-07-07 compressed 1 h → 10 s; 3 reps per policy, randomised order. Energy is
**model-based** (CPU-seconds × W/core, ECO not credited with lower power on this machine);
carbon uses the real trace intensity during each job.

| Policy | Carbon saving vs default (95 % CI) | Model energy saving | Deadline misses | Mean start delay (s) | Probe p95 (ms) | Decision time (ms) |
|---|---|---|---|---|---|---|
| default | 0 | 0 | 0 | 0.3 | 2.9 ± 2.3 | 0 |
| heuristic | −1.1 ± 2.2 | −1.1 | 0 | 0.3 | 2.5 ± 0.4 | 0 |
| carbon_only | 11.8 ± 4.1 | 0.0 | 0 | 20.7 | 2.3 ± 0.2 | 0.6 |
| battery_only | 8.7 ± 8.6 | 9.3 | 0 | 0.3 | 2.3 ± 0.1 | 0.3 |
| combined_noai | **18.3 ± 8.4** | 10.2 | 0 | 16.8 | 2.3 ± 0.2 | 0.8 |
| greenthread | 9.9 ± 6.1 | 8.0 | 0 | 3.8 | 2.3 ± 0.1 | 10.8 |

Reading: every policy met every deadline and none degraded interactive latency. With a
**perfect** replayed forecast, GreenThread deferred less than `combined_noai` (its confidence
discount shrinks far-ahead dips) and saved less carbon; with n = 3 the CIs overlap, so the
difference is not significant. The "energy saving" here is lower CPU-seconds from core
restriction on a 2-vCPU VM, not metered energy.

## 5. Trace-driven simulation (`results/simulation/`, 10 seeds × 14 days × 5 traces)

Same policy code; machine model 4C/8T laptop (assumed EcoQoS power ratio 0.58, slowdown 1.35,
3 W active platform power); predictor errors set to the **measured** log-error sd above
(ML 0.145, table 0.257). Carbon traces real except the synthetic India trace.

**E1 – baselines on AC (carbon saving %, 95 % CI; miss rate 0 for all):**

| Trace | carbon_only | battery_only | combined_noai | greenthread |
|---|---|---|---|---|
| DE 2020 | 4.7 ± 1.3 | 2.8 ± 0.9 | 11.1 ± 1.0 | **13.3 ± 1.3** |
| GB 2020 | 13.0 ± 1.5 | 2.8 ± 0.8 | 18.3 ± 1.4 | **19.9 ± 1.7** |
| FR 2020 | 2.1 ± 0.8 | 2.8 ± 0.9 | 9.8 ± 0.8 | **12.2 ± 1.1** |
| US-CAL 2020 | 1.7 ± 1.1 | 2.9 ± 0.9 | 8.5 ± 1.1 | **11.4 ± 1.0** |
| IN-SO synthetic | 0.3 ± 0.2 | 2.9 ± 1.0 | 8.1 ± 1.0 | **11.2 ± 1.0** |

**E2 – ablations (all traces, forecast noise 0.15):** greenthread 12.7 ± 0.8; without ML 9.9
± 0.7 (and 0.1 % misses); without deferral 11.6; without modes 2.3; without carbon term 1.2;
without uncertainty 13.5 ± 1.0; without quantile constraint and without battery term:
identical to greenthread (constraint never binding, battery term inactive on AC).

**E3 – forecast noise × signal outage:** greenthread beats `combined_noai` by ~2 points in
every cell and keeps 0.00 % misses (table baseline 0.07–0.09 %). The uncertainty-unaware
variant is 0.8–1.8 points better at low noise and equal within CI at noise 0.3–0.5.

**E4 – on battery:** energy saving at SoC 0.8/0.3/0.1: greenthread 11.5/11.5/11.3 % (p95
slowdown 1.6–1.75); the simple `heuristic` (always ECO on battery) 15.7 % (slowdown 1.35);
battery_only / combined_noai 7.4–8.3 % with p95 slowdown 2.9–4.0.

**E5 – ML error sd 0.05 → 0.5:** carbon saving falls 17.1 → 3.8 %; misses stay 0 until sd 0.5
(0.07 %); quantile vs median constraint indistinguishable under these deadlines.

## 6. Hypotheses — verdicts

| Hypothesis | Verdict | Evidence |
|---|---|---|
| H1 carbon reduction without extra misses | **Supported in simulation**, not significant in the 3-rep real run | E1: 11–20 % with 0 misses; real run 9.9 ± 6.1 % |
| H2 quantile constraint keeps misses low as error grows | **Not demonstrated** | Deadlines in the workloads were too loose for the constraint to bind; needs tighter-deadline workloads |
| H3 uncertainty handling helps under stale/noisy signals | **Not supported** | Costs 0.8–1.8 points at low noise, ties at high noise; in CSV replay a stale forecast stays informative, so the discount is an insurance premium that did not pay off |
| H4 ML improves over the non-AI predictor and outcomes | **Supported** | 10.9 vs 19.9 % MAPE; +2–3 carbon points and 0 vs 0.07–0.09 % misses in E1–E3 |

Two further findings: (1) on battery, a simple always-ECO rule saves more energy than R-BACI
because selecting the min-cost mode from noisy per-mode predictions suffers a winner's-curse
effect (measured errors are only ~22 % shared across modes); (2) most of the simulated saving
comes from execution-mode choice, whose size rests on the assumed EcoQoS power ratio — the
on-laptop `calibrate` step decides whether it holds.

## 7. What must still be run on the laptop

1. `setup_windows.ps1` (detect --probe + tests on real Win32).
2. `idle`, `calibrate`, `collect --scale 4` **on battery** → measured energy labels.
3. `experiment --reps 5` on battery and on AC; `simulate --metrics results\model_metrics.json`
   with `SimMachine` parameters from `calibrate_from_csv`.
Replace sections 3–5 of this report with those outputs.
