#!/usr/bin/env bash
# GreenThread - full measurement pipeline on a Linux laptop (mirror of run_all_windows.ps1).
# Before: close other apps, laptop fully charged, screen-off/suspend disabled.
# Steps marked [BATTERY] run unplugged, [AC] plugged in; the script pauses and tells you.
#   bash scripts/run_all_linux.sh [--reps 5] [--skip-battery]
set -euo pipefail
REPS=5; SKIPBAT=0
while [[ $# -gt 0 ]]; do case "$1" in --reps) REPS="$2"; shift 2;; --skip-battery) SKIPBAT=1; shift;; *) exit 1;; esac; done
source .venv/bin/activate
pause_for() { echo; read -r -p ">>> $1, then press Enter " _; }
GT="python -m greenthread"
has_battery=$($GT detect | python -c "import json,sys; print(int(json.load(sys.stdin)['capabilities']['battery_present']))")

echo "1. Capabilities + control probe"; $GT detect --probe
if [[ $has_battery == 1 && $SKIPBAT == 0 ]]; then
  pause_for "UNPLUG the charger [BATTERY]"
  echo "2. Idle baseline (2 min, hands off)";            $GT idle --seconds 120
  echo "3. Power-model calibration (~8 min)";            $GT calibrate --step 60
  echo "4. Training data with measured energy";          $GT collect --reps 1 --scale 3
  echo "5. Train";                                       $GT train
  echo "6. Real comparison on battery";                  $GT --config configs/demo.toml experiment --jobs configs/jobs_demo.json --reps "$REPS" --tag battery
  echo "7. Tight deadlines on battery, no escalation";   $GT --config configs/demo.toml experiment --jobs configs/jobs_tight.json --reps "$REPS" --tag tight_battery --no-escalate --policies default,heuristic,gt_no_quantile,greenthread
  pause_for "PLUG IN the charger [AC]"
else
  echo "(no battery or --skip-battery: AC-only pipeline; energy is RAPL-measured if readable, else modelled)"
  $GT idle --seconds 60 || true
  $GT calibrate --step 45 || echo "calibration skipped (needs battery or readable RAPL)"
fi
if [[ ! -f results/model.pkl ]]; then $GT collect --reps 2; $GT train; fi
echo "8. Real comparison on AC";                         $GT --config configs/demo.toml experiment --jobs configs/jobs_demo.json --reps "$REPS" --tag ac
echo "9. Tight deadlines on AC, no escalation";          $GT --config configs/demo.toml experiment --jobs configs/jobs_tight.json --reps "$REPS" --tag tight_ac --no-escalate --policies default,heuristic,gt_no_quantile,greenthread
echo "10. Simulation suite (~30-60 min)";                $GT simulate --seeds 10 --days 14 --metrics results/model_metrics.json --calibrate
echo "11. Ledger year-long study";                       $GT ledger-study
if [[ -f secrets/electricitymaps.token ]]; then
  echo "12. Live India grid sample"
  $GT --config configs/india_live.toml carbon --hours 12
  $GT --config configs/india_live.toml carbon-log --hours 1 --interval 1800
fi
python -m pytest -q | tee results/pytest_output.txt
echo "13. Regenerate docs/TEST_REPORT.md and docs/DASHBOARD.html"; $GT report; $GT dashboard
