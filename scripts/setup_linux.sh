#!/usr/bin/env bash
# GreenThread - one-time setup on Linux (Ubuntu/Fedora/Arch...), from the project folder:
#   bash scripts/setup_linux.sh [--token <Electricity Maps token>] [--rapl]
#   --rapl  lets your user read Intel RAPL energy counters (measured energy even on AC).
#           Needs sudo; resets at reboot. Security note: readable RAPL enables power
#           side-channel attacks (CVE-2020-8694), so only do this on your own machine.
set -euo pipefail
TOKEN=""; RAPL=0
while [[ $# -gt 0 ]]; do case "$1" in --token) TOKEN="$2"; shift 2;; --rapl) RAPL=1; shift;; *) echo "unknown $1"; exit 1;; esac; done
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if [[ $RAPL == 1 ]]; then
  sudo chmod a+r /sys/class/powercap/intel-rapl:*/energy_uj && echo "RAPL readable until reboot"
fi
if [[ -z "$TOKEN" && -t 0 ]]; then read -r -p "Electricity Maps API token for live India data (Enter to skip): " TOKEN || true; fi
if [[ -n "$TOKEN" ]]; then
  mkdir -p secrets && printf '%s' "$TOKEN" > secrets/electricitymaps.token && chmod 600 secrets/electricitymaps.token
  echo "Token saved to secrets/electricitymaps.token (not committed to git)."
  python -m greenthread --config configs/india_live.toml carbon --hours 6 || true
fi
gov=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo unknown)
echo "cpufreq governor: $gov (ECO lowers frequency only with 'schedutil' + kernel util-clamp support)"
python -m greenthread detect --probe
mkdir -p results && python -m pytest -q | tee results/pytest_output.txt
