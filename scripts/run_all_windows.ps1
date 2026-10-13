# GreenThread - full measurement pipeline on the target Windows laptop.
# Before: close other apps, Windows power mode "Balanced", sleep disabled, laptop fully charged.
# Steps marked [BATTERY] run unplugged, [AC] plugged in; the script pauses and tells you.
# Re-running is safe: training rows are appended and every step overwrites only its own output.
param([int]$Reps = 5, [switch]$SkipBattery)
$ErrorActionPreference = "Stop"
.\.venv\Scripts\Activate.ps1
function Pause-For($msg) { Write-Host ""; Read-Host ">>> $msg, then press Enter" | Out-Null }
$live = Test-Path secrets\electricitymaps.token

Write-Host "1. Capabilities + control probe"
python -m greenthread detect --probe

if (-not $SkipBattery) {
    Pause-For "UNPLUG the charger [BATTERY]"
    Write-Host "2. Idle baseline (2 min, do not touch the laptop)"
    python -m greenthread idle --seconds 120
    Write-Host "3. Power-model calibration (~8 min) - confirms or overturns the EcoQoS saving"
    python -m greenthread calibrate --step 60
    Write-Host "4. Training data with MEASURED energy (~60-90 min). If the battery drops below 20 %,"
    Write-Host "   press Ctrl+C, recharge, and run this script again with -SkipBattery after step 4 completes."
    python -m greenthread collect --reps 1 --scale 3
    Write-Host "5. Train predictors"
    python -m greenthread train
    Write-Host "6. Real comparison on battery"
    python -m greenthread --config configs\demo.toml experiment --jobs configs\jobs_demo.json --reps $Reps --tag battery
    Write-Host "7. Tight deadlines on battery, escalation off (tests the deadline check)"
    python -m greenthread --config configs\demo.toml experiment --jobs configs\jobs_tight.json --reps $Reps --tag tight_battery --no-escalate --policies default,heuristic,gt_no_quantile,greenthread
}
Pause-For "PLUG IN the charger [AC]"
if (-not (Test-Path results\model.pkl)) {
    python -m greenthread collect --reps 2
    python -m greenthread train
}
Write-Host "8. Real comparison on AC (compressed real carbon trace)"
python -m greenthread --config configs\demo.toml experiment --jobs configs\jobs_demo.json --reps $Reps --tag ac
Write-Host "9. Tight deadlines on AC, escalation off"
python -m greenthread --config configs\demo.toml experiment --jobs configs\jobs_tight.json --reps $Reps --tag tight_ac --no-escalate --policies default,heuristic,gt_no_quantile,greenthread
Write-Host "10. Simulation suite with this laptop's measured errors and calibration (~30-60 min)"
python -m greenthread simulate --seeds 10 --days 14 --metrics results\model_metrics.json --calibrate
Write-Host "11. Battery carbon ledger: year-long study on recorded traces"
python -m greenthread ledger-study
if ($live) {
    Write-Host "12. Live India grid data: 1 h sample now (leave scripts\track_live_windows.ps1 running for days for more)"
    python -m greenthread --config configs\india_live.toml carbon --hours 12
    python -m greenthread --config configs\india_live.toml carbon-log --hours 1 --interval 1800
}
python -m pytest -q | Tee-Object -FilePath results\pytest_output.txt
Write-Host "13. Regenerate docs\TEST_REPORT.md from all results"
python -m greenthread report
Write-Host "Done. Open docs\TEST_REPORT.md"
