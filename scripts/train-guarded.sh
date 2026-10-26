#!/usr/bin/env bash
# Runs research/finetune.py with a memory watchdog: if macOS reports less than MIN_FREE_PCT (default 10) percent of
# memory free for two checks in a row, the training run is stopped so the machine stays usable. (Swap usage is not
# used: it keeps stale pages long after pressure is gone.) All arguments go to finetune.py.
#   scripts/train-guarded.sh --out checkpoints/v8 --evidence ... > train.log 2>&1
set -uo pipefail
cd "$(dirname "$0")/.."
MIN_FREE_PCT=${MIN_FREE_PCT:-10}
USE_TF=0 caffeinate -i server/.venv/bin/python research/finetune.py "$@" &
pid=$!
strikes=0
while kill -0 "$pid" 2>/dev/null; do
  sleep 30
  free=$(memory_pressure | sed -nE 's/.*free percentage: ([0-9]+)%.*/\1/p')
  if (( ${free:-100} < MIN_FREE_PCT )); then strikes=$((strikes + 1)); else strikes=0; fi
  if (( strikes >= 2 )); then
    echo "WATCHDOG: free memory ${free}% < ${MIN_FREE_PCT}% twice; stopping training (pid $pid)"
    pkill -P "$pid" 2>/dev/null; kill "$pid" 2>/dev/null
    exit 3
  fi
done
wait "$pid"
