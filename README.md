# GreenThread — Carbon- and Battery-Aware Multicore Job Scheduler for Windows 11

GreenThread is a user-mode scheduler for CPU-heavy jobs on a Windows 11 laptop. For every
job it decides **when to start** (only if the job is marked deferrable) and **how to run**
(OS default, EcoQoS, restricted to half the cores, or efficient cores on hybrid CPUs), using
learned runtime predictions and the grid's carbon intensity, so that attributable carbon and
battery drain fall while deadlines and interactive responsiveness are protected.

No Linux, WSL, VM, dual boot, admin rights or kernel changes: only documented Win32 APIs.

## The algorithm in one paragraph (R-BACI)

Risk-bounded Battery- And Carbon-Intensity-aware scheduling picks the (start offset, mode)
minimising a normalised cost of carbon + battery + latency + deferral overhead, subject to
**start + predicted 90th-percentile runtime + margin ≤ deadline**. Carbon forecasts are
blended toward the long-run mean as they get older or further ahead, and their uncertainty is
penalised. On battery, emissions are charged at the carbon intensity of the energy *stored in
the battery* (the battery carbon ledger), so battery use is neither "zero carbon" nor counted
twice. If a throttled job's deadline comes at risk the engine escalates it to full speed.
Full formulation: [`docs/RESEARCH.md`](docs/RESEARCH.md) §5.

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
| `backends/posix.py` | Same interface on Linux (nice/affinity) for development and CI |
| `carbon.py` | CSV replay (time compression, forecast-error and outage injection), Electricity Maps client with cache, confidence-discounted `CarbonSignal` |
| `accounting.py` | Battery carbon ledger, attribution rule, power model |
| `telemetry.py` | Battery sampler, idle baseline, power-model calibration |
| `workload.py` | Reproducible workloads (SHA-256, zlib, NumPy matmul, interactive latency probe) |
| `model.py` | ML quantile predictor + table baseline + evaluation |
| `policy.py` | R-BACI and all baselines/ablations |
| `engine.py` | Real-process scheduling loop with escalation and replanning |
| `collect.py`, `experiments.py`, `simulator.py` | Data collection, real experiments, trace-driven simulation |
| `cli.py` | `python -m greenthread …` |

## Quick start (Windows 11)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1   # venv, deps, detect, tests
python -m greenthread detect --probe
python -m greenthread collect --reps 2        # ~20-40 min, builds training data on YOUR laptop
python -m greenthread train
python -m greenthread --config configs\demo.toml run --jobs configs\jobs_demo.json --policy greenthread
```
Full measured pipeline (battery + AC, calibration, experiments, simulation):
`scripts\run_all_windows.ps1`. Details: [`docs/USER_MANUAL.md`](docs/USER_MANUAL.md).

## What has been verified, and where

* 37 automated tests: unit (ABI parsing, carbon mapping, ledger maths, attribution, config),
  policy properties (interactive never deferred, deadline quantile respected, fallback,
  vectorised objective = brute force, stale signal ⇒ no deferral), ML, integration with real
  child processes, Windows backend call sequences against a fake `kernel32`.
* Real runs and ML training on the development machine (Linux VM) and the trace-driven
  simulation suite — results and their limits in [`docs/TEST_REPORT.md`](docs/TEST_REPORT.md).
* **Not yet executed on Windows hardware.** The Windows backend is tested against a mocked
  kernel32; the first run on the laptop (`setup_windows.ps1`) is the real check.

## Data

`data/traces/{de,gb,fr,us-cal}_2020_hourly.csv`: hourly means of the 2020 carbon-intensity
series published with *Let's Wait Awhile* (Wiesner et al., Middleware '21; ENTSO-E/CAISO
data). `in-so_synthetic_2020_hourly.csv` is **synthetic** (illustrative solar-heavy shape),
not measured data — use live mode or replace it with exported Electricity Maps history.
