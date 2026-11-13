#!/bin/bash
# Dung Cloud va Agent wa-zalo-claude tren macOS. Tin nhan dang cho van nam trong outbox cua agent.
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1
for name in agent cloud; do
  pidf="logs/$name.pid"
  if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then
    pid="$(cat "$pidf")"
    kill "$pid"
    for _ in $(seq 1 15); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
    echo "Da dung $name"
  else
    echo "$name khong chay"
  fi
  rm -f "$pidf"
done
