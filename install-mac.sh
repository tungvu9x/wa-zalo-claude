#!/bin/bash
# Cai wa-zalo-claude tren macOS (Apple Silicon hoac Intel), chay truc tiep, khong Docker:
#   PostgreSQL 16 + Cloud (Python 3.12) + Agent (Node) + ket noi Claude Desktop qua MCP.
#
#   ./install-mac.sh            (hoac: bash install-mac.sh)
#   API_PORT=8090 ./install-mac.sh   doi cong neu 8080 dang bi dung
#
# Chay lai nhieu lan duoc: giu nguyen secret, mat khau DB va device da tao.
# Viet cho bash 3.2 (mac dinh cua macOS).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
CLOUD="$ROOT/cloud"
AGENT="$ROOT/agent"
TOOLS="$ROOT/tools"
CLOUD_ENV="$CLOUD/.env"
AGENT_ENV="$AGENT/.env"
API_PORT="${API_PORT:-}"
PG_PORT="${PG_PORT:-5432}"
TENANT_NAME="${TENANT_NAME:-KNXStore}"

step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '    \033[32m%s\033[0m\n' "$1"; }
info() { printf '    %s\n' "$1"; }
die()  { printf '\n\033[31mLOI: %s\033[0m\n' "$1" >&2; exit 1; }

# doc gia tri KEY=... tu file .env (rong neu khong co)
env_get() { [ -f "$1" ] && sed -n "s/^$2=//p" "$1" | tail -1 || true; }
rand_token() { "$PY" -c 'import secrets;print(secrets.token_urlsafe(48))'; }
rand_pass()  { "$PY" -c 'import secrets,string;a=string.ascii_letters+string.digits;print("".join(secrets.choice(a) for _ in range(24)))'; }
fernet_key() { "$PY" -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())'; }

[ "$(uname -s)" = "Darwin" ] || die "Script nay chi danh cho macOS. Windows dung install-windows.ps1"

# ------------------------------------------------------------------ 1. Homebrew
step 'Homebrew'
BREW="$(command -v brew || true)"
[ -z "$BREW" ] && [ -x /opt/homebrew/bin/brew ] && BREW=/opt/homebrew/bin/brew
[ -z "$BREW" ] && [ -x /usr/local/bin/brew ] && BREW=/usr/local/bin/brew
if [ -z "$BREW" ]; then
  die 'Chua co Homebrew. Cai bang lenh sau (can mat khau may Mac), roi chay lai script:
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
fi
ok "$BREW"

brew_install() {
  if "$BREW" list --versions "$1" >/dev/null 2>&1; then ok "$1 da co"; else info "Cai $1..."; "$BREW" install "$1"; fi
}

# ------------------------------------------------------------------ 2. Python, Node, PostgreSQL
step 'Python 3.12, Node.js, PostgreSQL 16'
brew_install python@3.12
PY="$("$BREW" --prefix python@3.12)/bin/python3.12"
[ -x "$PY" ] || die "Khong thay $PY"

NODE_BIN=""
if command -v node >/dev/null 2>&1; then
  major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
  # duong dan that (fnm/nvm tao duong dan tam, mat khi dong terminal)
  [ "$major" -ge 20 ] && NODE_BIN="$(node -p process.execPath)"
fi
if [ -z "$NODE_BIN" ]; then
  brew_install node
  NODE_BIN="$("$BREW" --prefix)/bin/node"
fi
NPM_BIN="$(dirname "$NODE_BIN")/npm"
[ -x "$NPM_BIN" ] || NPM_BIN="$(command -v npm)"
ok "Node $("$NODE_BIN" -v): $NODE_BIN"

brew_install postgresql@16
PG_BIN="$("$BREW" --prefix postgresql@16)/bin"
if ! "$PG_BIN/pg_isready" -q -h 127.0.0.1 -p "$PG_PORT"; then
  info 'Bat PostgreSQL (tu chay lai moi lan dang nhap macOS)...'
  "$BREW" services start postgresql@16 >/dev/null
  for _ in $(seq 1 30); do "$PG_BIN/pg_isready" -q -h 127.0.0.1 -p "$PG_PORT" && break; sleep 1; done
fi
"$PG_BIN/pg_isready" -q -h 127.0.0.1 -p "$PG_PORT" || die "PostgreSQL khong chay o cong $PG_PORT. Xem: brew services info postgresql@16"
ok "PostgreSQL san sang (cong $PG_PORT)"

# ------------------------------------------------------------------ 3. Database
step 'Database wadaily'
psql_su() { "$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -d "${2:-postgres}" -v ON_ERROR_STOP=1 -tAc "$1"; }
# Homebrew tao superuser trung ten user macOS, dang nhap local khong can mat khau
psql_su 'SELECT 1' >/dev/null 2>&1 || die "Khong dang nhap duoc PostgreSQL bang user '$USER'.
  Neu may da co PostgreSQL khac dang chay o cong $PG_PORT, tat no di hoac chay: PG_PORT=<cong> ./install-mac.sh"
DB_PW="$(env_get "$CLOUD_ENV" WADAILY_DB_PASSWORD)"; [ -n "$DB_PW" ] || DB_PW="$(rand_pass)"
if [ "$(psql_su "SELECT 1 FROM pg_roles WHERE rolname='wadaily'")" = "1" ]; then
  psql_su "ALTER ROLE wadaily PASSWORD '$DB_PW'" >/dev/null
else
  psql_su "CREATE ROLE wadaily LOGIN PASSWORD '$DB_PW'" >/dev/null
fi
if [ "$(psql_su "SELECT 1 FROM pg_database WHERE datname='wadaily'")" != "1" ]; then
  psql_su "CREATE DATABASE wadaily OWNER wadaily ENCODING 'UTF8' TEMPLATE template0" >/dev/null
fi
psql_su 'CREATE EXTENSION IF NOT EXISTS unaccent' wadaily >/dev/null
ok 'Xong'

# ------------------------------------------------------------------ 4. Cloud .env
step 'Cau hinh Cloud'
[ -n "$API_PORT" ] || API_PORT="$(env_get "$CLOUD_ENV" API_PORT)"; [ -n "$API_PORT" ] || API_PORT=8080
API="http://127.0.0.1:$API_PORT"
mkdir -p "$ROOT/data/files" "$ROOT/logs" "$TOOLS"
v() { local cur; cur="$(env_get "$CLOUD_ENV" "$1")"; if [ -n "$cur" ]; then echo "$cur"; else "$2"; fi; }
MASTER_KEY="$(v MASTER_KEY fernet_key)"
ADMIN_TOKEN="$(v ADMIN_TOKEN rand_token)"
MCP_TOKEN="$(v MCP_TOKEN rand_token)"
ADMIN_UI_PASSWORD="$(env_get "$CLOUD_ENV" ADMIN_UI_PASSWORD)"; [ -n "$ADMIN_UI_PASSWORD" ] || ADMIN_UI_PASSWORD="$(rand_pass | cut -c1-12)"
MCP_TENANT_ID="$(env_get "$CLOUD_ENV" MCP_TENANT_ID)"
MAX_FILE_MB="$(env_get "$CLOUD_ENV" MAX_FILE_MB)"; [ -n "$MAX_FILE_MB" ] || MAX_FILE_MB=100
write_cloud_env() {
  ( umask 077; cat > "$CLOUD_ENV" <<EOF
# Tao boi install-mac.sh - KHONG chia se file nay
DATABASE_URL=postgresql://wadaily:$DB_PW@127.0.0.1:$PG_PORT/wadaily
MASTER_KEY=$MASTER_KEY
ADMIN_TOKEN=$ADMIN_TOKEN
MCP_TOKEN=$MCP_TOKEN
MCP_TENANT_ID=$MCP_TENANT_ID
ADMIN_UI_PASSWORD=$ADMIN_UI_PASSWORD
STORAGE_DIR=$ROOT/data/files
API_HOST=127.0.0.1
API_PORT=$API_PORT
MAX_FILE_MB=$MAX_FILE_MB
WADAILY_DB_PASSWORD=$DB_PW
EOF
  )
}
write_cloud_env
ok "$CLOUD_ENV"

# ------------------------------------------------------------------ 5. Python venv
step 'Thu vien Python (cloud/.venv)'
[ -x "$CLOUD/.venv/bin/python" ] || "$PY" -m venv "$CLOUD/.venv"
VENV_PY="$CLOUD/.venv/bin/python"
"$VENV_PY" -m pip install -q --disable-pip-version-check --upgrade pip
"$VENV_PY" -m pip install -q --disable-pip-version-check -r "$CLOUD/requirements.txt"
ok 'Da cai'

# ------------------------------------------------------------------ 6. Dang ky tenant + device
step 'Khoi dong Cloud de dang ky thiet bi'
health() { curl -sf -m 3 "$API/healthz" >/dev/null 2>&1; }
CLOUD_PID=""
cleanup() { [ -n "$CLOUD_PID" ] && kill "$CLOUD_PID" 2>/dev/null || true; }
trap cleanup EXIT
if ! health; then
  (cd "$CLOUD" && exec "$VENV_PY" run_cloud.py >"$ROOT/logs/install-cloud.log" 2>&1) &
  CLOUD_PID=$!
  for _ in $(seq 1 60); do health && break; kill -0 "$CLOUD_PID" 2>/dev/null || break; sleep 1; done
  health || die "Cloud khong khoi dong duoc. Xem $ROOT/logs/install-cloud.log"
fi
ok "Cloud chay tai $API"

json_get() { "$PY" -c "import sys,json;print(json.load(sys.stdin)['$1'])"; }
admin_post() { curl -sf -X POST "$API$1" -H "authorization: Bearer $ADMIN_TOKEN" -H 'content-type: application/json' -d "$2"; }
if [ -z "$MCP_TENANT_ID" ]; then
  MCP_TENANT_ID="$(admin_post /admin/tenants "{\"name\":\"$TENANT_NAME\",\"retention_days\":150}" | json_get tenant_id)" \
    || die 'Tao tenant loi'
  write_cloud_env
  ok "Tao tenant $MCP_TENANT_ID"
fi
DEVICE_ID="$(env_get "$AGENT_ENV" DEVICE_ID)"
DEVICE_SECRET="$(env_get "$AGENT_ENV" DEVICE_SECRET)"
if [ -z "$DEVICE_ID" ] || [ -z "$DEVICE_SECRET" ]; then
  DEV_NAME="$(scutil --get ComputerName 2>/dev/null | tr -cd '[:alnum:] ._-' || true)"
  [ -n "$DEV_NAME" ] || DEV_NAME="$(hostname -s | tr -cd '[:alnum:]._-')"
  [ -n "$DEV_NAME" ] || DEV_NAME=mac
  resp="$(admin_post /admin/devices "{\"tenant_id\":\"$MCP_TENANT_ID\",\"name\":\"$DEV_NAME\"}")" \
    || die 'Tao device loi'
  DEVICE_ID="$(echo "$resp" | json_get device_id)"
  DEVICE_SECRET="$(echo "$resp" | json_get device_secret)"
  ok "Tao device $DEVICE_ID"
else
  ok "Giu device $DEVICE_ID"
fi
cleanup; CLOUD_PID=""

# ------------------------------------------------------------------ 7. Agent
step 'Agent (npm install, tai Chromium cho WhatsApp)'
ENABLE_WHATSAPP="$(env_get "$AGENT_ENV" ENABLE_WHATSAPP)"; [ -n "$ENABLE_WHATSAPP" ] || ENABLE_WHATSAPP=true
ENABLE_ZALO="$(env_get "$AGENT_ENV" ENABLE_ZALO)"; [ -n "$ENABLE_ZALO" ] || ENABLE_ZALO=true
CHROME_PATH="$(env_get "$AGENT_ENV" CHROME_PATH)"
( umask 077; cat > "$AGENT_ENV" <<EOF
# Tao boi install-mac.sh - KHONG chia se file nay
CLOUD_URL=$API
DEVICE_ID=$DEVICE_ID
DEVICE_SECRET=$DEVICE_SECRET
DATA_DIR=./data
ENABLE_WHATSAPP=$ENABLE_WHATSAPP
ENABLE_ZALO=$ENABLE_ZALO
MAX_FILE_MB=$MAX_FILE_MB
EOF
  [ -n "$CHROME_PATH" ] && echo "CHROME_PATH=$CHROME_PATH" >> "$AGENT_ENV" || true
)
NODE_DIR="$(dirname "$NODE_BIN")"
export PATH="$NODE_DIR:$PATH"
(cd "$AGENT" && "$NPM_BIN" install --no-audit --no-fund)
ok 'Da cai'

# ------------------------------------------------------------------ 8. Claude Desktop
step 'Ket noi Claude Desktop'
"$NPM_BIN" install --prefix "$TOOLS/mcp-remote" mcp-remote@0.16.6 --no-audit --no-fund >/dev/null
PROXY="$TOOLS/mcp-remote/node_modules/mcp-remote/dist/proxy.js"
[ -f "$PROXY" ] || die "Khong thay $PROXY"
CLAUDE_CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
if [ "${SKIP_CLAUDE_CONFIG:-}" = "1" ]; then
  info 'Bo qua (SKIP_CLAUDE_CONFIG=1)'
else
  "$PY" - "$CLAUDE_CFG" "$NODE_BIN" "$PROXY" "$API/mcp" "$MCP_TOKEN" <<'PYEOF'
import json, os, shutil, sys, time
path, node, proxy, url, token = sys.argv[1:]
os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
conf = {}
if os.path.exists(path):
    shutil.copy2(path, f"{path}.bak-{time.strftime('%Y%m%d%H%M%S')}")
    raw = open(path, encoding="utf-8").read().strip()
    conf = json.loads(raw) if raw else {}
conf.setdefault("mcpServers", {})["wa-zalo-daily"] = {
    "command": node,
    "args": [proxy, url, "--header", "Authorization:${AUTH_HEADER}"],
    "env": {"AUTH_HEADER": f"Bearer {token}"},
}
with open(path, "w", encoding="utf-8") as f:
    json.dump(conf, f, ensure_ascii=False, indent=2)
print(f"    Da ghi {path}")
PYEOF
  [ -d "/Applications/Claude.app" ] || info 'Chua thay Claude Desktop: tai tai https://claude.ai/download (config da ghi san).'
fi

# ------------------------------------------------------------------ 9. Script chay
step 'Luu duong dan cho start-mac.command'
cat > "$TOOLS/env.sh" <<EOF
# Tao boi install-mac.sh
NODE_BIN="$NODE_BIN"
VENV_PY="$VENV_PY"
PG_BIN="$PG_BIN"
BREW="$BREW"
API_PORT="$API_PORT"
EOF
chmod +x "$ROOT/start-mac.command" "$ROOT/stop-mac.command" 2>/dev/null || true
ok "$TOOLS/env.sh"

printf '\n\033[32mCAI DAT XONG\033[0m\n'
printf '\033[33mTrang quan tri: %s/admin   Mat khau: %s\033[0m\n' "$API" "$ADMIN_UI_PASSWORD"
echo "(mat khau luu trong cloud/.env, dong ADMIN_UI_PASSWORD)"
cat <<EOF
Buoc tiep theo:
  1. Mo Finder, vao $ROOT, double-click start-mac.command
     (hoac chay: ./start-mac.command). Trang quan tri tu mo, quet QR WhatsApp/Zalo o muc 'Dang nhap tai khoan'.
  2. Thoat han Claude Desktop (Cmd+Q) roi mo lai.
  3. Hoi Claude: "goi system_status cua wa-zalo-daily".
EOF
