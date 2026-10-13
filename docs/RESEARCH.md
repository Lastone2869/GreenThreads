# GreenThread — Research Basis, Design and Evaluation

## 1. PDF compliance matrix (technical requirements only)

Source: *Operating Systems Lab Project (40 Marks) – Project Guidelines*. Administrative items
(team size, attendance, VTOP upload, form) are out of scope by request.

| # | Requirement (PDF) | How GreenThread meets it | Verification |
|---|---|---|---|
| R1 | Apply OS concepts through design, implementation, testing, evaluation | Native scheduler controlling real processes | `pytest`, real runs, experiments |
| R2 | Implementation, not only theory | Python package; Win32 via ctypes, Linux via syscalls/sysfs | Code in `greenthread/` |
| R3 | One or more OS concepts | CPU scheduling (start-time + mode policy), process management (spawn/monitor/kill), thread programming (multithreaded workloads, engine waiter threads, ledger lock), resource allocation (CPU Sets, affinity, Job Object CPU caps), process monitoring (CPU time, escalation), device management (battery/AC telemetry), performance analysis (experiments) | §6 OS-concept map |
| R4 | Open-source libs allowed, core by team | numpy/pandas/scikit-learn/psutil only as libraries; scheduler, controls, accounting, simulator written here | Source |
| R5 | Modular code, documentation | 14 modules with single responsibilities; docstrings | `greenthread/*` |
| R6 | Version history and backup | Git repository included (`git log`) | `.git` |
| R7 | Must execute during demo | Capability detection + fallbacks (no API, no battery counter, no E-cores, control failure) | `test_integration.py`, manual §7 |
| R8 | Review I: problem, objectives, literature, design, architecture, algorithm/flowchart, module division, working prototype ≥60–70 % | §2–§5 here; architecture diagram in README; complete implementation | This file |
| R9 | Review II: complete implementation, testing, performance evaluation, error handling, demo of all features, documentation, viva | Automated tests, experiments (real + simulation), error-handling table (manual §7), viva notes §6 | `docs/TEST_REPORT.md` |
| R10 | Deliverables: proposal, code, documentation, tests & results, user manual, presentation | Code, `README.md`, this file, `TEST_REPORT.md` (auto-generated), `USER_MANUAL.md`. Proposal/PPT content is in this file; slides not yet produced | — |
| R11 | Final report contents: intro, objectives, design, implementation, testing, results, conclusion, future work, references, code appendix | §2–§9 here + `TEST_REPORT.md` | — |

## 2. Problem statement and research question

Laptops run many CPU-heavy background jobs whose timing is flexible (backups, indexing,
exports, model training) next to latency-sensitive interactive work. The OS scheduler
optimises responsiveness and power locally but is blind to (a) the carbon intensity of the
grid electricity, (b) the fact that energy drawn from the battery was charged earlier at some
other carbon intensity, and (c) how much slack each job's deadline actually has.

**Research question.** On a single laptop (Windows 11 or Linux), can a user-mode scheduler
that chooses each job's *start time* and *execution mode* using (i) runtime predictions with
calibrated upper quantiles, (ii) carbon signals whose trust is learned from their own observed
errors, and (iii) uncertainty-robust mode selection reduce attributable carbon and battery
energy compared with default OS scheduling and simpler carbon/battery heuristics, **without**
increasing deadline misses or interactive latency — and does counting battery energy at its
charge-time carbon intensity change the answer?

**Hypotheses.**
* H1 (carbon): on AC, R-BACI lowers attributable carbon versus `default` on grids with diurnal
  variability, with no increase in deadline misses.
* H2 (safety): the quantile constraint keeps the miss rate low as prediction error grows,
  where a median-based constraint does not.
* H3 (uncertainty): when carbon signals are stale or noisy, confidence discounting keeps the
  scheduler from deferring on bad information (no loss vs. default; fewer harmful deferrals
  than an uncertainty-unaware variant).
* H4 (AI): the ML predictor is more accurate than a lookup-table predictor and this translates
  into better scheduling outcomes (ablation `gt_no_ml`).
* H5 (battery): on battery GreenThread saves at least as much energy as the simple "always
  EcoQoS" rule while keeping the deadlines that rule breaks.
* H6 (ledger): counting battery energy at the grid intensity at time of use misstates its
  carbon by a material amount (≥ 5 %) for common charging habits.
* H7 (hardware): EcoQoS actually lowers energy per job on the target laptop (measured, not assumed).

Version 1.1 revised H2 and H3: version 1 never exercised the deadline check (deadlines too
loose) and its fixed-decay uncertainty discount cost carbon without ever paying off. §10 lists
what changed and why.

## 3. Literature review (2020–2026, verified)

Only sources whose existence and bibliographic details were checked during this work are
listed. "Limitations" mixes the authors' stated limitations (marked *stated*) with our analysis.

| # | Work | Venue / ID | What it does | Limitations relevant to us | Relevance |
|---|---|---|---|---|---|
| L1 | Wiesner, Behnke, Scheinert, Gontarska, Thamsen — *Let's Wait Awhile: How Temporal Workload Shifting Can Reduce Carbon Emissions in the Cloud* | Middleware '21, doi:10.1145/3464298.3493399 | Analyses temporal shifting potential (DE, GB, FR, CA, 2020); simulates scheduling of delay-tolerant jobs incl. forecast accuracy | Data-centre scope; no device battery; jobs' runtimes assumed known; no execution-mode choice | Source of our real carbon traces; baseline `carbon_only` follows this idea |
| L2 | Sukprasert, Souza, Bashir, Irwin, Shenoy — *On the Limitations of Carbon-Aware Temporal and Spatial Workload Shifting in the Cloud* | EuroSys '24, doi:10.1145/3627703.3650079 | Trace analysis over 123 regions; quantifies upper bounds of shifting | *Stated:* practical reductions are limited and simple policies capture most of the benefit; benefit shrinks as grids get greener | Sets realistic expectations: we must not claim large savings; motivates combining shifting with energy-mode selection |
| L3 | Souza et al. — *Ecovisor: A Virtual Energy System for Carbon-Efficient Applications* | ASPLOS '23, doi:10.1145/3575693.3575709 | Exposes virtual grid/solar/battery to cloud applications | Requires a controllable energy system (servers, batteries, solar); not a client OS | Closest idea for "battery as time-shifted energy"; our ledger is a passive, client-side analogue |
| L4 | Hanafy et al. — *CarbonScaler: Leveraging Cloud Workload Elasticity for Optimizing Carbon-Efficiency* | SIGMETRICS '24 (POMACS), doi:10.1145/3626788 | Scales elastic cloud jobs up/down with carbon intensity | Needs elastic multi-server jobs; cloud only | Our mode choice (HALF/ECO) is a single-machine analogue of scaling resources |
| L5 | Radovanović et al. — *Carbon-Aware Computing for Datacenters* | arXiv:2106.11750 (Google CICS) | Day-ahead carbon-aware capacity curves for flexible fleet load | Fleet-level; forecasts of load and carbon at data-centre scale | Shows forecast-driven, risk-aware shifting at scale |
| L6 | Microsoft — *Windows Update is now carbon aware* | Windows 11 22H2 support note | Schedules update installation when grid is cleaner, only when plugged in and data available | One workload (updates), closed policy, no user deadlines or battery accounting | Closest deployed client-side system; GreenThread generalises to user jobs with deadlines |
| L7 | Kim et al. — *GreenScale: Carbon-Aware Systems for Edge Computing* | arXiv:2304.00404 (2023) | Carbon-aware placement across mobile/edge/cloud; models mobile carbon via charging behaviour | Placement across tiers, not an OS scheduler on one device | Supports treating a device's battery carbon as set at charge time |
| L8 | Microsoft Learn — `SetProcessInformation` (ProcessPowerThrottling / EcoQoS), `JOBOBJECT_CPU_RATE_CONTROL_INFORMATION`, CPU Sets | Official API docs | EcoQoS lowers frequency or uses efficient cores; job objects cap CPU rate | *Stated:* EcoQoS is not for foreground/performance-critical work | Defines our ECO mode and safety rule (interactive jobs never ECO) |
| L9 | Electricity Maps API v3 | Official docs | Latest/history/forecast gCO2e/kWh per zone | Forecast endpoint plan-dependent; estimated values flagged | Live source; motivates freshness handling |
| L10 | E-Mapper — *Energy-Efficient Resource Allocation for Traditional Operating Systems on Heterogeneous Processors* | arXiv:2406.18980 (2024) | Energy-efficient allocation on hybrid CPUs from user space | Hybrid-CPU specific | Relevant only to the optional `ECORES` mode |
| L11 | TU Dresden — *Energy Efficiency Features of the Intel Alder Lake Architecture* | ICPE '24 | Measures P/E-core energy behaviour | Hardware characterisation | Same as L10 |

## 4. Research gaps (validated) and choice

| Gap | What exists | What is missing | Supported? | Feasible on target laptops? |
|---|---|---|---|---|
| G1 Carbon accounting of battery-powered execution | Cloud work treats energy as drawn at the moment of use; Ecovisor controls physical batteries; GreenScale models mobile charging | A *scheduler* on a client device that charges battery-powered execution at the intensity of the stored energy, so that running on battery is neither "free" nor double-counted | Not found in our search (narrow claim) | Yes — needs only battery capacity readings |
| G2 Risk-bounded deadline feasibility from learned per-mode runtimes | Deadline-aware carbon shifting assumes known runtimes (L1); quantile prediction is common in cluster scheduling | Using *calibrated upper-quantile* runtime predictions per OS execution mode as the feasibility test when deferring/throttling on a client | Partially (an adaptation) | Yes |
| G3 Behaviour under stale/uncertain/misleading carbon signals | L1 studies forecast error in simulation; L2 shows sophisticated policies add little | A signal that *learns how much to trust itself* by scoring its own past forecasts per lead time, shrinking toward the hour-of-day climatology (Bayes-optimal weight), so it is as trusting as a good forecast deserves and degrades gracefully when the forecast is stale, absent (free API tier) or wrong | Partially (adaptation of forecast-combination ideas to a client scheduler) | Yes |
| G5 Choosing among execution modes from noisy predictions | Mode/DVFS selection usually assumes known or profiled costs | Empirical-Bayes shrinkage of a job's predicted mode effect toward the mode's population effect plus a pessimism margin, which removes the winner's-curse loss when predictions are noisy | Adaptation (statistics applied to OS mode choice) | Yes |
| G4 Hybrid P/E-core placement | Studied (L10, L11; Intel Thread Director) | — | Well covered | **No**: i7-10th gen and Ryzen 7 7445HS are homogeneous; implemented only as a detected optional mode |

**Selected contribution.** *R-BACI*: one constrained scheduler that combines G1 (battery
carbon ledger), G2 (quantile feasibility over learned per-mode runtimes), G3 (self-calibrating
carbon signal) and G5 (robust mode selection), implemented with supported Windows 11 and Linux
user-mode controls. G1 is the primary novelty claim and now has its own evidence (year-long
charging-habit study + field logger, §5 of TEST_REPORT); G2, G3, G5 are supporting
contributions that adapt known ideas and are each isolated by an ablation. G4 was dropped as a
research claim because it cannot be evaluated on the available hardware.

**What we do not claim.** Not "first carbon-aware scheduler"; not large savings (L2); not
changing grid intensity; not per-thread energy measurement (Windows does not expose it).

## 5. Formulation (implemented in `policy.py`)

For job *j* arriving at *t* with deadline *d_j*, choose offset *o* ∈ O_j and mode *m* ∈ M:

```
min  J(o,m) = λc · C̃(o,m) · (1 + λr · σ(o,m)/I_prior)
            + λb · u(SoC) · B̃(m)
            + λl · (o + q50(m)) / (d_j − t)
            + λo · 1[o > 0]
s.t. o + q_τ(m) + δ ≤ d_j − t            (risk-bounded deadline, τ = 0.9)
     o = 0, m = NORMAL   if interactive
     o = 0               unless deferrable, on AC, carbon-aware
```

| Term | Definition | Units / normalisation |
|---|---|---|
| E(m) | predicted incremental energy of the job in mode m | Wh |
| C̃ | AC: E(m)·Ī_eff(o..o+q50) / (E(NORMAL)·Ī_eff(0..q50)); battery: E(m)/E(NORMAL) (ledger intensity cancels) | dimensionless, 1 = run now normally |
| Ī_eff | c·I_forecast + (1−c)·I_clim(hour of day); c = s²/(s²+e²(ℓ)), ℓ = data age + horizon | gCO2e/kWh |
| e²(ℓ) | forecast error variance at lead ℓ, measured by scoring past forecasts against observed values (pseudo-count prior equal to the fixed decay 0.5^(ℓ/h_h)) | (gCO2e/kWh)² |
| s, I_clim | spread of intensity around, and value of, the observed ~4-week hour-of-day climatology | gCO2e/kWh |
| σ | √(s²e²/(s²+e²)) — posterior sd of the effective intensity | gCO2e/kWh |
| g(m) | robust mode factor: exp(lr_post + κ·sd_post − lr), lr_post = (τ²·lr + σ_m²·μ_m)/(τ²+σ_m²), sd_post = √(τ²σ_m²/(τ²+σ_m²)); multiplies C̃ and B̃ for m ≠ NORMAL | dimensionless |
| lr, σ_m, μ_m, τ | predicted log energy ratio of mode m vs NORMAL, its held-out error (noise removed), the mode's mean effect over training configurations, and the spread of that effect | — |
| B̃ | E(m)/E(NORMAL) on battery, else 0 | dimensionless |
| u(SoC) | clip(0.5/SoC, 1, 10) — battery urgency | — |
| q50, q_τ | ML median and calibrated τ-quantile runtime | s |

Hard vs. soft: deadline feasibility, interactivity and deferrability are hard constraints;
carbon, battery, latency and overhead are soft. If nothing is feasible the job runs now in
NORMAL (fallback). At run time, if `now + (1−progress)·q_τ > d_j − δ` the engine escalates to
BOOST. Deferred jobs are replanned periodically but may only move earlier (commitment rule).

**Accounting (`accounting.py`) — one physical path per joule.**
AC: C = E/η_adapter/1000 · I_grid(actual trace during the run).
Battery: C = E/1000 · I_ledger, where the ledger tracks stored Wh and their intensity:
I ← (S·I + ΔE·I_grid/η_charge)/(S+ΔE) on charge; unchanged on discharge. B (battery) is a
resource objective, not a second carbon term, so physical consumption is never counted twice.
E = (device energy − idle baseline × duration) × job's share of busy CPU time when measured
on battery; otherwise CPU-seconds × calibrated W/core.

## 6. OS concepts map (viva)

| Concept | Where | What to explain |
|---|---|---|
| CPU scheduling | `policy.py` | Admission-time scheduling of start time + mode; feasibility vs. objective; fallback |
| Process management | `engine.py` | CreateProcess (Popen), READY/GO handshake so controls apply before work, wait thread, kill on exit |
| Thread programming | `workload.py`, `engine.py` | GIL-releasing hashing/zlib threads; per-job waiter threads; sampler thread |
| Synchronisation | `accounting.py` | Lock-protected ledger updates from sampler and engine |
| Resource allocation | `backends/windows.py` | CPU Sets (`SetProcessDefaultCpuSets`), affinity masks, Job Object CPU-rate hard cap |
| Power management / QoS | `backends/windows.py` | EcoQoS vs. HighQoS (`SetProcessInformation`), priority classes |
| Device management | `backends/windows.py`, `telemetry.py` | `GetSystemPowerStatus`, WMI `BatteryStatus` discharge rate/capacity |
| Process monitoring | `engine.py`, `telemetry.py` | CPU time, deadline risk, escalation |
| Linux equivalents | `backends/posix.py` | `sched_setattr` (SCHED_BATCH, util-clamp ≈ EcoQoS), `sched_setaffinity` with sysfs topology, `/sys/class/power_supply`, RAPL `energy_uj` |
| Performance analysis | `experiments.py`, `simulator.py` | Baselines, ablations, CIs |

## 7. AI/ML methodology

* **Model:** scikit-learn `HistGradientBoostingRegressor`, quantile loss at 0.5 and 0.9, plus a
  squared-error model for CPU-seconds and (when labels exist) measured energy.
* **Targets:** log(runtime), log(CPU-seconds) as a per-workload least-squares line in
  log(size) plus a boosted residual (v1.1), and log(Wh per unit) when measured. Version 1.0
  used absolute targets (q90 coverage 0 % on unseen sizes) and then per-unit targets, which
  extrapolated unstably when a workload has fixed overhead (matmul on the second dev machine:
  bias swung between −0.17 and −1.01 log with a 25 % change in training rows). The line +
  residual form is stable (bias −0.05 to −0.08 across splits on both machines).
* **Features:** workload kind, execution mode, log size, threads, threads per usable core,
  logical/physical cores, background CPU load before start, AC state.
* **Data:** `collect` runs kinds × 4 sizes × thread counts × all supported modes × background
  load levels × repetitions, in randomised order (avoids thermal/time confounding).
* **Splits:** (a) temporal 60/20/20 by collection order — calibration set fits a conformal
  multiplier for the upper quantile; (b) workload-leakage check holding out the largest size
  of each kind.
* **Baseline:** lookup table of median seconds/unit per (kind, threads, mode) with fallbacks
  and an empirical upper ratio.
* **Metrics:** MAE, MAPE, pinball loss at 0.5/0.9, q90 coverage, mean upper margin, log-error
  bias/sd, inference latency, model size.
* **Noise decomposition (v1.1):** identical configurations are run twice, so run-to-run noise
  is measured directly (pooled within-group sd ≈ 0.10 log on the dev machine). Model error =
  √(observed error² − noise²). Version 1 counted this noise as model error, which made mode
  comparisons look ~3× less reliable than they are and drove the simulator's battery result.
* **Mode-effect error (`rel_sigma`)** is measured on held-out *paired* runs (same job, two
  modes). A dedicated paired-ratio model was also trained; it compared modes *worse* than the
  level model's own mode differences (noise-removed error 0.12 vs 0.05) and is reported but not
  used — a documented negative result.
* **Safeguards:** predictions only *choose among* OS-safe actions; constraint uses the upper
  quantile; runtime escalation; interactive jobs bypass the model; untrained → baselines only.

## 8. Threats to validity

* The development machine used for the measured results in `TEST_REPORT.md` is a 2-vCPU
  Linux VM without battery, RAPL access or util-clamp: `ECO` there is SCHED_BATCH + nice 10,
  which does not lower frequency, so the testbed config credits ECO with no power saving.
  Energy/carbon numbers from that machine come from the power model and intensity accounting,
  not from a meter. The Windows or Linux pipeline must be run on the target laptop to obtain
  measured energy (H7).
* Simulation energy uses an explicit `SimMachine` model (EcoQoS power ratio 0.58, slowdown
  1.35, 3 W active platform power) — assumptions until replaced by calibration on the laptop
  (`simulate --calibrate` after `collect` on battery). E4–E6 conclusions on battery depend on
  these values; H7 in the report states whether the laptop confirmed them.
* Simulation windows: results use 14-day windows in Jul–Dec 2020; the only tuned parameter
  (gate strength κ) was checked on Jan–Jun windows, so test windows were not used for tuning.
* The noise-removal step assumes run-to-run noise is the same for all modes and independent
  between runs.
* The development workspace was restarted on a different CPU during the work (SHA-256 4.6×
  slower, no SHA extensions). Results from both machines are kept separately
  (`results/archive/`), and a cross-machine check shows why models must be trained locally.
* Carbon traces are real (2020, DE/GB/FR/US-CAL) except the India trace, which is
  **synthetic** and only illustrates a solar-heavy diurnal shape.
* Average (not marginal) intensity is used; marginal signals may change conclusions.
* Battery DischargeRate refresh is firmware-dependent; short jobs (< ~15 s) cannot be measured
  reliably; concurrent jobs share device energy by CPU-time proportion (approximation).

## 9. Paper blueprint

Title: *R-BACI: Risk-Bounded Carbon- and Battery-Aware Job Scheduling on Client Laptops.*
Abstract: problem (§2) → method (§5) → implementation (Windows controls) → evaluation (real +
trace-driven) → results (fill from `TEST_REPORT.md`) → limitation (L2-style bounds).
Keywords: carbon-aware computing, battery-aware scheduling, EcoQoS, quantile regression, Windows.
Sections: 1 Introduction · 2 Background & Related Work (Table §3) · 3 Gaps & Motivation (§4) ·
4 Problem Formulation (§5) · 5 Prediction Layer (§7) · 6 System Design & Windows Implementation ·
7 Experimental Setup (hardware table, traces, workloads, baselines) · 8 Results (E1–E5, real
runs, prediction accuracy) · 9 Threats to Validity (§8) · 10 Conclusion & Future Work ·
References (§3). Tables/figures to populate: Table prediction accuracy (ML vs table); Fig. E1
carbon saving by trace; Table ablations; Fig. E3 stress; Table battery SoC; Table real runs.

## 10. Version 1.1 — what changed and why

| Problem found in version 1 | Diagnosis | Change | Where it is tested |
|---|---|---|---|
| Uncertainty discount cost carbon and never paid off | A fixed decay distrusts *good* forecasts as much as bad ones, and shrank toward a flat daily mean, discarding the predictable day/night shape | Signal scores its own past forecasts per lead time and uses the Bayes-optimal weight toward the hour-of-day climatology; fixed decay kept as ablation `gt_fixed_conf` | E3; `test_adaptive_*` |
| Deadline check never exercised | All workloads had generous deadlines | Tight-deadline workloads (1.25–3× runtime) in simulation and a portable real job file (`deadline_factor`), escalation switchable off | E5; `experiment --tag tight_* --no-escalate`; `test_tight_deadlines_*` |
| Always-ECO beat GreenThread on battery | Winner's curse: choosing the cheapest of several noisy estimates. Run-to-run noise had been counted as model error, and the simulator gave every mode an independent error | Noise decomposition; simulator uses the measured error structure; empirical-Bayes shrinkage + pessimism for mode choice | E4, E6, E7; `test_shrinkage_*` |
| EcoQoS saving was assumed | No measurement on the laptop | `idle` + `calibrate` + `collect` on battery (Windows/Linux) or RAPL (Linux); H7 verdict computed from measured data | `report` §1, §8 |
| Battery ledger was a concept only | No evidence it changes the numbers | Year-long charging-habit study on real traces, `ledger-track` field logger, `ledger-report` | `ledger-study`; `test_ledger_*` |
| Windows only | — | First-class Linux backend and scripts | `test_linux_*`; `scripts/*linux*` |
