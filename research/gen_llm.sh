#!/bin/bash
# Usage: gen_llm.sh <category> <tag> [focus]  -> research/data/gen/<category>_<tag>.jsonl (retries until non-empty)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
cat=$1; tag=$2; focus=${3:-}
[ -f "$HERE/focus/${cat}_${tag}.txt" ] && focus="$(cat "$HERE/focus/${cat}_${tag}.txt")"
out="$HERE/data/gen/${cat}_${tag}.jsonl"
ask="Generate 150 lines, ALL with category \"$cat\".${focus:+ Focus on: $focus.} About 75% bash, the rest write/edit/read where it fits the category."
for attempt in 1 2 3; do
  (cd "${GEN_CWD:-/tmp}" && exec pi -ne -nc -ns -np --no-mcp -nt --no-session --thinking low --system-prompt "You output only JSON Lines." \
     -p "$(cat "$HERE/gen_prompt.md")

$ask") > "$out" 2> "$out.err" &
  pid=$!
  # Watchdog: a stalled request (sleep, network drop) must not hang the run.
  ( sleep 600; kill "$pid" 2>/dev/null ) & watchdog=$!
  wait "$pid"; kill "$watchdog" 2>/dev/null
  [ "$(grep -c '^{' "$out")" -gt 20 ] && break
  sleep $((attempt * 20))
done
echo "$cat $tag: $(grep -c '^{' "$out") rows"
