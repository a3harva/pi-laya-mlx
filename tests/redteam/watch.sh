#!/bin/bash
# Prints one line per new message in laya-claude's inbox and per refused red-team call (bash 3.2 safe).
W=${LAYA_REDTEAM_WORKSPACE:-$HOME/Developer/temp/pi-laya-redteam}
INBOX="$HOME/.pi-mailbox/laya-claude/inbox"
touch "$W/audit.jsonl"
tail -n 0 -F "$W/audit.jsonl" 2>/dev/null | grep --line-buffered '"refused"' | sed -l 's/^/AUDIT-REFUSED: /' &
shopt -s nullglob
seen=" "
for f in "$INBOX"/*.json; do seen="$seen$(basename "$f") "; done
while true; do
  for f in "$INBOX"/*.json; do
    name=$(basename "$f")
    case "$seen" in *" $name "*) continue ;; esac
    seen="$seen$name "
    /usr/bin/python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print('MAIL', d['id'], '|', d['from'], '|', d['subject'], flush=True)" "$f"
  done
  sleep 5
done
