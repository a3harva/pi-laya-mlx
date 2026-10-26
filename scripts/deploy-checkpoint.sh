#!/usr/bin/env bash
# Restarts the laya-guard server on 127.0.0.1:8723 with the given checkpoint directory.
#   scripts/deploy-checkpoint.sh checkpoints/v2
#   scripts/deploy-checkpoint.sh checkpoints/v2,checkpoints/v4   (cautious ensemble: block if any member would)
set -euo pipefail
cd "$(dirname "$0")/.."
checkpoint=""
IFS=',' read -ra parts <<< "$1"
for part in "${parts[@]}"; do
  dir=$(cd "$part" && pwd)
  [[ -f "$dir/config.json" ]] || { echo "not a checkpoint: $dir"; exit 1; }
  checkpoint="${checkpoint:+$checkpoint,}$dir"
done
port=${LAYA_GUARD_PORT:-8723}
pid=$(lsof -t -iTCP:"$port" -sTCP:LISTEN || true)
if [[ -n "$pid" ]]; then
  ps -o command= -p "$pid" | grep -q laya-guard-serve || { echo "port $port is held by something else (pid $pid)"; exit 1; }
  kill "$pid"
  while kill -0 "$pid" 2>/dev/null; do sleep 0.5; done
fi
nohup server/.venv/bin/laya-guard-serve --port "$port" --model "$checkpoint" >> server/.server.log 2>&1 &
until curl -sf "localhost:$port/health" >/dev/null; do sleep 1; done
curl -s "localhost:$port/health"; echo
