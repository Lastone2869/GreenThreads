# Long-running background logging on Windows (leave it running for days of normal use):
#  - battery charge/discharge + live India grid carbon -> results\ledger_log.csv (battery carbon ledger)
#  - live grid intensity history -> results\carbon_log.csv (becomes a real replayable India trace)
# Stop any time with Ctrl+C, then: python -m greenthread ledger-report ; python -m greenthread report
param([double]$Hours = 72)
.\.venv\Scripts\Activate.ps1
$cfg = "configs\india_live.toml"
if (-not (Test-Path secrets\electricitymaps.token)) { Write-Host "No token: grid intensity will be replayed from the CSV trace"; $cfg = "configs\demo_wallclock.toml" }
if ($cfg -eq "configs\india_live.toml") {
    Start-Process -NoNewWindow python -ArgumentList "-m greenthread --config $cfg carbon-log --hours $Hours --interval 1800"
}
python -m greenthread --config $cfg ledger-track --hours $Hours --interval 60
