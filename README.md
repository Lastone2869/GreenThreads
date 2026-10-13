# GreenThread — Carbon- and Battery-Aware Multicore Job Scheduler (Windows 11 and Linux)

GreenThread is a user-mode scheduler for CPU-heavy jobs on a Windows 11 laptop. For every
job it decides **when to start** (only if the job is marked deferrable) and **how to run**
(OS default, EcoQoS, restricted to half the cores, or efficient cores on hybrid CPUs), using
learned runtime predictions and the grid's carbon intensity, so that attributable carbon and
battery drain fall while deadlines and interactive responsiveness are protected.

Runs natively on Windows 11 (documented Win32 APIs) and Linux (user-mode syscalls and sysfs). No WSL, VM, dual boot, admin rights, driver or kernel changes.

## The algorithm in one paragraph (R-BACI)

Risk-bounded Battery- And Carbon-Intensity-aware scheduling picks the (start offset, mode)
minimising a normalised cost of carbon + battery + latency + deferral overhead, subject to
**start + predicted 90th-percentile runtime + margin ≤ deadline**. The carbon signal scores
its own past forecasts and trusts each lead time exactly as much as its measured error allows,
falling back to the typical value for that hour of day. A non-normal mode is chosen only if its
predicted saving, shrunk toward what that mode usually achieves, beats the remaining
uncertainty. On battery, emissions are charged at the carbon intensity of the energy *stored in
the battery* (the battery carbon ledger), so battery use is neither "zero carbon" nor counted
twice. If a throttled job's deadline comes at risk the engine escalates it to full speed.
Full formulation: [`docs/RESEARCH.md`](docs/RESEARCH.md) §5; what changed in 1.1: §10.

## Architecture

```
            job file / API
                 │
       ┌─────────▼─────────┐  predictions per mode   ┌──────────────────────┐
       │  Engine (engine)  │◄────────────────────────│ Layer 1: ML predictor │
       │ admit · plan ·    │                         │ GBDT quantile (model) │
       │ launch · monitor  │──► decide(job, ctx) ───►│ Layer 2: R-BACI policy│
       │ escalate · account│                         │ (policy)              │
       └──┬─────────┬──────┘                         └──────────▲───────────┘
          │         │ effective intensity, σ                    │
          │   ┌─────▼──────────────┐   ┌───────────────────────┴──┐
          │   │ CarbonSignal       │   │ Telemetry: power status,  │
          │   │ CSV replay / live  │   │ battery discharge, idle   │
          │   │ Electricity Maps   │   │ baseline, power model     │
          │   └────────────────────┘   └───────────────────────────┘
          │ apply(pid, mode)
   ┌──────▼────────────────────────────────────────────────────────────┐
   │ Backend (backends/windows.py, ctypes)                              │
   │ SetProcessInformation(EcoQoS/HighQoS) · SetPriorityClass ·        │
   │ SetProcessDefaultCpuSets / SetProcessAffinityMask · Job Objects · │
   │ GetSystemCpuSetInformation · GetSystemPowerStatus · WMI Battery   │
   └───────────────────────────────────────────────────────────────────┘
   Accounting (accounting.py): grid path on AC, battery-ledger path on battery
```

| Module | Responsibility |
|---|---|
| `backends/windows.py` | Win32 controls and discovery (64-bit-safe ctypes signatures, fixed-width structs) |
| `backends/posix.py` | Linux: SCHED_BATCH + util-clamp, sysfs topology/hybrid detection, sysfs battery, RAPL |
| `carbon.py` | CSV replay (time compression, forecast-error and outage injection), Electricity Maps client with cache, confidence-discounted `CarbonSignal` |
| `accounting.py` | Battery carbon ledger, attribution rule, power model |
| `telemetry.py` | Battery sampler, idle baseline, power-model calibration |
| `workload.py` | Reproducible workloads (SHA-256, zlib, NumPy matmul, interactive latency probe) |
| `model.py` | ML quantile predictor + table baseline + evaluation |
| `policy.py` | R-BACI and all baselines/ablations |
| `engine.py` | Real-process scheduling loop with escalation and replanning |
| `collect.py`, `experiments.py`, `simulator.py` | Data collection, real experiments, trace-driven simulation |
| `ledger_study.py` | Battery-ledger field logger, report, and year-long charging-habit study |
| `report.py` | Rebuilds `docs/TEST_REPORT.md` and hypothesis verdicts from `results/` |
| `cli.py` | `python -m greenthread …` |

## Quick start

```powershell
# Windows 11
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1     # venv, deps, token, detect, tests
powershell -ExecutionPolicy Bypass -File scripts\run_all_windows.ps1   # full pipeline -> docs\TEST_REPORT.md
```
```bash
# Linux
bash scripts/setup_linux.sh --rapl          # --rapl optional: measured energy on AC (Intel)
bash scripts/run_all_linux.sh               # full pipeline -> docs/TEST_REPORT.md
```
Details: [`docs/USER_MANUAL.md`](docs/USER_MANUAL.md).

## What has been verified, and where

* Automated tests (unit, policy properties, ML, real child processes on Linux, Windows backend
  call sequences against a fake `kernel32`, Linux backend against fake sysfs trees, live API
  client against mocked responses): `python -m pytest -q`.
* Real runs, ML training and the simulation suite on the development machine (Linux VM) — see
  [`docs/TEST_REPORT.md`](docs/TEST_REPORT.md), generated by `python -m greenthread report`.
* **Not yet executed on Windows hardware or with a live API token.** Both need the target laptop:
  run `scripts\run_all_windows.ps1` (or the Linux script) and `report` replaces every number.

## Data

`data/traces/{de,gb,fr,us-cal}_2020_hourly.csv`: hourly means of the 2020 carbon-intensity
series published with *Let's Wait Awhile* (Wiesner et al., Middleware '21; ENTSO-E/CAISO
data). `in-so_synthetic_2020_hourly.csv` is **synthetic** (illustrative solar-heavy shape),
not measured data — use live mode or replace it with exported Electricity Maps history.
