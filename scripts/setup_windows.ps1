# GreenThread - one-time setup on Windows 11 (run in PowerShell from the project folder)
$ErrorActionPreference = "Stop"
python --version
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m greenthread detect --probe
python -m pytest -q
