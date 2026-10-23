#!/usr/bin/env bash
# Leave running for days of normal use (e.g. inside tmux): battery ledger + live grid log.
# Stop with Ctrl+C, then: python -m greenthread ledger-report && python -m greenthread report
set -euo pipefail
HOURS=${1:-72}
source .venv/bin/activate
CFG=configs/india_live.toml
[[ -f secrets/electricitymaps.token ]] || { echo "No token: grid intensity replayed from CSV trace"; CFG=configs/demo_wallclock.toml; }
if [[ $CFG == configs/india_live.toml ]]; then
  python -m greenthread --config "$CFG" carbon-log --hours "$HOURS" --interval 1800 &
  LOGPID=$!
  trap 'kill $LOGPID 2>/dev/null || true' EXIT
fi
python -m greenthread --config "$CFG" ledger-track --hours "$HOURS" --interval 60
