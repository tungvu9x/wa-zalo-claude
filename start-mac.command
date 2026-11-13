#!/bin/bash
# Chay wa-zalo-claude tren macOS: kiem tra PostgreSQL, bat Cloud + Agent chay nen, mo trang quan tri.
# Double-click file nay trong Finder. Dung: stop-mac.command. Log: logs/cloud.log, logs/agent.log
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1
if [ ! -f tools/env.sh ] || [ ! -f cloud/.env ] || [ ! -f agent/.env ]; then
  echo "Chua cai dat. Chay ./install-mac.sh truoc."; read -r -p "Enter de thoat"; exit 1
fi
. tools/env.sh
mkdir -p logs
API="http://127.0.0.1:${API_PORT:-8080}"
health() { curl -sf -m 3 "$API/healthz" >/dev/null 2>&1; }
alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

# PostgreSQL (brew services tu bat khi dang nhap; phong khi bi tat)
if ! "$PG_BIN/pg_isready" -q -h 127.0.0.1; then
  echo "Bat PostgreSQL..."
  "$BREW" services start postgresql@16 >/dev/null
  for _ in $(seq 1 20); do "$PG_BIN/pg_isready" -q -h 127.0.0.1 && break; sleep 1; done
fi

# Cloud
if health; then
  echo "Cloud da chay ($API)"
else
  echo "Bat Cloud..."
  (cd cloud && nohup "$VENV_PY" run_cloud.py >> ../logs/cloud.log 2>&1 & echo $! > ../logs/cloud.pid)
  for _ in $(seq 1 60); do health && break; sleep 1; done
  if ! health; then
    echo "Cloud khong len sau 60 giay. Xem 20 dong cuoi logs/cloud.log:"; tail -20 logs/cloud.log
    read -r -p "Enter de thoat"; exit 1
  fi
  echo "Cloud san sang ($API)"
fi

# Agent; caffeinate giu may khong ngu khi de yen trong luc agent chay (gap nap may van ngu)
if alive logs/agent.pid; then
  echo "Agent da chay"
else
  echo "Bat Agent..."
  (cd agent && nohup "$NODE_BIN" src/index.js >> ../logs/agent.log 2>&1 & echo $! > ../logs/agent.pid)
  nohup caffeinate -i -w "$(cat logs/agent.pid)" >/dev/null 2>&1 &
fi

open "$API/admin"
echo ""
echo "Xong. Co the dong cua so Terminal nay; Cloud va Agent van chay nen."
echo "Dung: double-click stop-mac.command"
