#!/usr/bin/env bash
# Run pi with the Laya guard in tmux: left pane pi, right pane the guard server's live verdict log.
#
#   scripts/tmux.sh [project-dir] [-- pi args...]
#
# Inside tmux it splits the current window; outside it creates (or attaches to) session "laya".
# Environment: LAYA_GUARD_PORT (8723), LAYA_TMUX_SESSION (laya), plus every LAYA_GUARD_* variable
# the extension and server read (see README).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${LAYA_GUARD_PORT:-8723}"
SESSION="${LAYA_TMUX_SESSION:-laya}"
SERVE="$ROOT/server/.venv/bin/laya-guard-serve"

WORKDIR="$PWD"
if [[ $# -gt 0 && "$1" != "--" ]]; then WORKDIR="$(cd "$1" && pwd)"; shift; fi
[[ "${1:-}" == "--" ]] && shift
PI_ARGS="$(printf '%q ' "$@")"

command -v tmux >/dev/null || { echo "tmux not found (brew install tmux)" >&2; exit 1; }
command -v pi >/dev/null || { echo "pi not found on PATH" >&2; exit 1; }
[[ -x "$SERVE" ]] || { echo "server not installed; run $ROOT/scripts/setup.sh" >&2; exit 1; }

# Server pane: reuse a server that is already up, otherwise run it in the foreground so its log is visible.
SERVER_CMD="if curl -sf -m1 http://127.0.0.1:$PORT/health >/dev/null; then
  echo 'laya-guard already running on :$PORT, following its log'; tail -n 20 -F ~/.cache/laya-guard/server.log;
else exec $(printf '%q' "$SERVE") --port $PORT; fi"

# pi pane: wait for the model to load, then start pi with the extension.
PI_CMD="printf 'waiting for laya-guard on :$PORT'; for i in \$(seq 1 120); do
  curl -sf -m1 http://127.0.0.1:$PORT/health >/dev/null && break; printf .; sleep 1; done; echo;
export LAYA_GUARD_URL=http://127.0.0.1:$PORT;
exec pi -e $(printf '%q' "$ROOT/extension") $PI_ARGS"

if [[ -n "${TMUX:-}" ]]; then
  tmux split-window -h -d -l 40% -c "$ROOT" "$SERVER_CMD"
  cd "$WORKDIR"
  eval "$PI_CMD"
fi

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux new-session -d -s "$SESSION" -n pi -c "$WORKDIR" -x "$(tput cols)" -y "$(tput lines)" "$PI_CMD"
  tmux split-window -h -d -l 40% -t "$SESSION:pi" -c "$ROOT" "$SERVER_CMD"
fi
exec tmux attach -t "$SESSION"
