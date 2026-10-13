# GreenThread User Manual

GreenThread runs your CPU-heavy jobs (builds, backups, exports, training runs) on a
Windows 11 laptop and decides, for each job, **when** to start it and **how** to run it
(normal, EcoQoS, restricted to fewer cores) so that carbon emissions and battery drain go
down without missing the deadline you set.

## 1. Requirements

| Item | Requirement |
|---|---|
| OS | Windows 11 (Windows 10: LowQoS instead of EcoQoS) or Linux (any modern distro) |
| Python | 3.11 or newer (python.org installer, "Add to PATH" ticked) |
| Rights | Normal user. No admin, no driver, no WSL, no kernel changes |
| Hardware | Any x86-64 laptop. Hybrid CPUs (Intel 12th gen+) additionally get the `ECORES` mode |
| Optional | Electricity Maps API token for live grid data (India zone `IN-SO` by default) |

## 2. Install

**Windows 11**
```powershell
cd greenthread
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1            # asks for the API token (Enter to skip)
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -Token "abc123"
```
Activate later with `.\.venv\Scripts\Activate.ps1`.

**Linux**
```bash
cd greenthread
bash scripts/setup_linux.sh                      # asks for the token
bash scripts/setup_linux.sh --token abc123 --rapl   # --rapl: measured energy via Intel RAPL (sudo, until reboot)
```
Activate later with `source .venv/bin/activate`.

Both create `.venv`, install dependencies, store the token in `secrets/electricitymaps.token`
(git-ignored; an `ELECTRICITYMAPS_TOKEN` environment variable also works), print detected
capabilities with a live control probe, and run the test suite.

## 2a. Full measurement pipeline (fills docs/TEST_REPORT.md)

| | Windows | Linux |
|---|---|---|
| Everything (pauses to tell you when to unplug / plug in) | `scripts\run_all_windows.ps1` | `bash scripts/run_all_linux.sh` |
| Days of background logging (battery ledger + live India grid) | `scripts\track_live_windows.ps1` | `bash scripts/track_live_linux.sh 72` |
| Rebuild the report after any step | `python -m greenthread report` | same |

Start fully charged and allow about 3–4 hours (most of it unattended). `report` rewrites
`docs/TEST_REPORT.md` from `results/`, including the hypothesis verdicts.

## 3. Commands

All commands: `python -m greenthread [--config FILE] [--backend auto|windows|null] <command>`

| Command | What it does |
|---|---|
| `detect [--probe]` | Lists CPU, core topology (CPU Sets, efficiency classes), battery, EcoQoS support, supported modes. `--probe` launches a dummy process and actually applies every control to prove it works. |
| `carbon --hours N` | Shows the current carbon intensity, the forecast, and the *effective* (confidence-discounted) values the scheduler will use. |
| `idle --seconds 120` | **On battery.** Measures idle device power (baseline subtracted from job energy). |
| `calibrate --step 60` | **On battery.** Fits the power model (idle W, W per busy core for normal and EcoQoS) from battery discharge readings. |
| `collect --reps 2 [--scale S]` | Runs the workload matrix under every supported mode, writes `results/training_data.csv`. On battery it also records measured energy. Use `--scale 4` on battery so jobs are long enough to be measured. |
| `train` | Trains the ML predictor and the table baseline, prints held-out accuracy, saves `results/model.pkl`, `results/model_metrics.json`. |
| `run --jobs FILE --policy NAME` | Schedules a job file with one policy and prints per-job results. |
| `experiment --jobs FILE --reps N` | Runs all baselines N times in randomised order and writes `results/experiment_<tag>/summary.md`. |
| `simulate --seeds 10 --days 14 [--metrics results/model_metrics.json]` | Trace-driven simulation suite (baselines, ablations, uncertainty stress, battery levels, prediction-error sweep) with plots. |
| `ledger` | Shows the battery carbon ledger (energy stored and its carbon intensity). |
| `ledger-track --hours 72` | Logs battery charge/discharge and grid carbon every minute and keeps the ledger current. Run during normal use for days. |
| `ledger-report` | Compares battery carbon counted as zero / at time of use / by the ledger on that log. |
| `ledger-study` | Year-long simulation of four charging habits on the recorded grid traces. |
| `carbon-log --hours 24` | (live config) Appends live hourly grid intensity to `results/carbon_log.csv`, a real trace you can replay. |
| `report` | Regenerates `docs/TEST_REPORT.md` from all results. |
| `cross-machine --model PATH` | Applies another machine's model to this machine's data (shows why to retrain locally). |
| `experiment ... --no-escalate` | Turns off runtime escalation to test the planner's deadline check alone. |

Policies: `default`, `heuristic`, `carbon_only`, `battery_only`, `combined_noai`, `greenthread`,
`greenthread_v1` (version 1 for before/after), and ablations `gt_no_uncertainty`,
`gt_fixed_conf`, `gt_no_gate`, `gt_no_quantile`, `gt_no_ml`, `gt_no_defer`, `gt_no_modes`,
`gt_no_carbon`, `gt_no_battery`.

## 4. Writing a job file

```json
{"jobs": [
  {"id": "backup", "kind": "compress", "units": 600, "threads": 1,
   "arrival_s": 0, "relative_deadline_s": 7200, "deferrable": true},
  {"id": "ui", "kind": "probe", "units": 60, "arrival_s": 0, "interactive": true}
]}
```
* `kind`: `hash`, `compress`, `matmul` (CPU workloads) or `probe` (interactive latency probe; `units` = seconds).
* `deferrable: true` is the **only** way a job may be delayed. Without it a job always starts immediately.
* `interactive: true` jobs are never delayed or throttled.
* `deadline_factor: 1.3` instead of `relative_deadline_s` sets the deadline to 1.3 × this
  machine's predicted normal runtime, so a job file is equally tight on any laptop
  (`configs/jobs_tight.json`).

## 5. Configuration (TOML)

```toml
[carbon]
source = "csv"                     # csv | electricitymaps | static
csv_path = "data/traces/de_2020_hourly.csv"
csv_start = "2020-07-07T05:00:00"  # trace time that maps to the start of a run
seconds_per_trace_hour = 3600      # 10 = compress 1 h of grid data into 10 s (demo)
freshness_half_life_s = 3600       # stale data is trusted half as much per hour of age
forecast_half_life_h = 12          # forecasts trusted half as much per 12 h of horizon

[weights]                          # objective weights
carbon = 1.0
battery = 1.0
latency = 0.25
risk = 0.5
defer_overhead = 0.02

[scheduler]
deadline_quantile = 0.9            # deadline must hold at the predicted 90th percentile
safety_margin_s = 2.0
max_concurrent = 1
escalate = true                    # remove EcoQoS/core limits if a deadline becomes at risk
```
Live grid data: `configs/india_live.toml` (zone `IN-SO`; change `zone` for other Indian
regions: `IN-NO`, `IN-WE`, `IN-EA`, `IN-NE`). Without a forecast in your API plan GreenThread
builds a "same hour yesterday" forecast from the 24 h history and learns how far to trust it.

## 6. Demonstration script (Review II)

```powershell
python -m greenthread detect --probe
python -m greenthread carbon --hours 12
python -m greenthread --config configs\demo.toml run --jobs configs\jobs_demo.json --policy default
python -m greenthread --config configs\demo.toml run --jobs configs\jobs_demo.json --policy greenthread
python -m greenthread --config configs\demo.toml experiment --jobs configs\jobs_demo.json --reps 3 --tag demo
```
Show: deferral into the low-carbon window, EcoQoS/CPU-Set actions in the log, the interactive
probe staying NORMAL, the per-job priority/affinity in Task Manager's Details tab while a job runs, and the summary table.

## 7. Failure behaviour (what happens when things are missing)

| Situation | Behaviour |
|---|---|
| No internet / API error / no token | Last cached value is used; its trust is judged by the measured error at that data age, so the effective intensity moves to the hour-of-day climatology (or the prior with no history) and deferral stops paying off. `carbon` shows the error and the learned confidence table. |
| Bad token (401) / endpoint not in plan (403) | Reported once; forecast falls back to "same hour yesterday" from history; nothing crashes. |
| Linux kernel without util-clamp, or intel_pstate governor | ECO = SCHED_BATCH + nice only (noted by `detect`); calibration then shows whether it saves energy. |
| RAPL not readable (Linux) | Energy from battery discharge on battery, power model on AC. `setup_linux.sh --rapl` enables it. |
| No battery or BatteryStatus WMI class missing | Energy is estimated with the utilisation power model; results are labelled `model`. |
| Homogeneous CPU (no E-cores) | `ECORES` mode is not offered; `HALF`/`ECO` are used. |
| Windows 10 | The same API sets LowQoS instead of EcoQoS (labelled in the log). |
| A control call fails | The job still runs; failure is logged in `control_failures`; it never blocks execution. |
| Deadline becomes at risk while throttled | Engine escalates to `BOOST` (HighQoS, all cores, normal priority). |
| No feasible (start, mode) | Job runs immediately in NORMAL mode (`fallback`). |
| Ctrl+C | All child workload processes are killed. |

## 8. Output files (`results/`)

* `capabilities.json` - detection output
* `training_data.csv`, `model.pkl`, `table.pkl`, `model_metrics.json`
* `run_<policy>.csv`, `decisions_<policy>.jsonl` - per-job results and every decision with its cost breakdown
* `experiment_<tag>/results.csv`, `summary.csv`, `summary.md`
* `simulation/*.csv.gz`, `*_summary.csv`, `SIMULATION_RESULTS.md`, `*.png`
* `battery_ledger.json`, `power_model.json`, `idle_w.json`
* `ledger_log.csv`, `ledger_report.json`, `ledger/LEDGER_STUDY.md`
* `carbon_log.csv` (live India trace), `carbon_signal_state.json` (learned forecast trust)
* `archive/<machine>/` — results from other machines
