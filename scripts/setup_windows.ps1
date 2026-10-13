# GreenThread - one-time setup on Windows 11 (PowerShell, from the project folder):
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
# Optional: -Token "<your Electricity Maps API token>"  (stored in secrets\, git-ignored)
param([string]$Token = "")
$ErrorActionPreference = "Stop"
python --version
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if ($Token -eq "") { $Token = Read-Host "Electricity Maps API token for live India data (Enter to skip)" }
if ($Token -ne "") {
    New-Item -ItemType Directory -Force -Path secrets | Out-Null
    Set-Content -Path secrets\electricitymaps.token -Value $Token.Trim() -NoNewline
    Write-Host "Token saved to secrets\electricitymaps.token (not committed to git)."
    python -m greenthread --config configs\india_live.toml carbon --hours 6
}
python -m greenthread detect --probe
python -m pytest -q | Tee-Object -FilePath results\pytest_output.txt
