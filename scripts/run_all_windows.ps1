# GreenThread - full measurement pipeline on the target laptop.
# Close other apps, set the Windows power mode to "Balanced", disable sleep for the session.
# Steps marked [BATTERY] must be run unplugged; [AC] plugged in.
param([int]$Reps = 5)
$ErrorActionPreference = "Stop"
.\.venv\Scripts\Activate.ps1

Write-Host "1. Capabilities (saved to results\capabilities.json)"
python -m greenthread detect --probe

Write-Host "2. [BATTERY] Idle baseline (2 min, do not touch the laptop)"
python -m greenthread idle --seconds 120

Write-Host "3. [BATTERY] Power-model calibration (~8 min)"
python -m greenthread calibrate --step 60

Write-Host "4. [BATTERY] Training data with measured energy. Start fully charged; ~60-90 min."
Write-Host "   (--scale 3 makes jobs long enough for the battery counter; if the battery drops"
Write-Host "    below 20 %, stop with Ctrl+C, recharge, and rerun - rows are appended.)"
python -m greenthread collect --reps 1 --scale 3

Write-Host "5. Train + evaluate predictors (ML vs table)"
python -m greenthread train

Write-Host "6. [BATTERY] Real-machine comparison, compressed carbon trace"
python -m greenthread --config configs\demo.toml experiment --jobs configs\jobs_demo.json --reps $Reps --tag battery

Write-Host "7. [AC] Plug in, then real-machine comparison on AC"
Read-Host "Plug the charger in and press Enter"
python -m greenthread --config configs\demo.toml experiment --jobs configs\jobs_demo.json --reps $Reps --tag ac

Write-Host "8. Simulation suite using the measured predictor error"
python -m greenthread simulate --seeds 10 --days 14 --metrics results\model_metrics.json --calibrate
Write-Host "Done. See results\"
