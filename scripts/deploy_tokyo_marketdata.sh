#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-tokyo-cloudserver}"
REMOTE_DIR="${REMOTE_DIR:-~/services/trading-cli-bot}"
SERVICE_NAME="${SERVICE_NAME:-tbot-marketdata-collector.service}"
HTTP_SERVICE_NAME="${HTTP_SERVICE_NAME:-tbot-marketdata-http.service}"
LEGACY_SERVICE_NAME="${LEGACY_SERVICE_NAME:-tbot-marketdata-btcusdt.service}"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --remote-host) REMOTE_HOST="$2"; shift 2 ;;
    --remote-dir) REMOTE_DIR="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] %q ' "$@"; printf '\n'
  else
    "$@"
  fi
}

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_ABS="$(ssh "$REMOTE_HOST" "python3 - <<'PY'
from pathlib import Path
print(Path('$REMOTE_DIR').expanduser())
PY")"
REMOTE_PARENT="$(dirname "$REMOTE_ABS")"

echo "Deploying multi venue/symbol marketdata appliance to ${REMOTE_HOST}:${REMOTE_ABS}"
run ssh "$REMOTE_HOST" "mkdir -p '$REMOTE_PARENT'"
run rsync -az --delete \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '._*' \
  --exclude 'var/marketdata' \
  --exclude 'var/reports' \
  "$ROOT/" "$REMOTE_HOST:$REMOTE_ABS/"

run ssh "$REMOTE_HOST" "cd '$REMOTE_ABS' && python3 -m venv .venv && .venv/bin/python -m pip install -U pip && .venv/bin/python -m pip install -e ."
run ssh "$REMOTE_HOST" "command -v tailscale >/dev/null || echo 'WARNING: tailscale is not installed on remote; HTTP snapshot service will only be useful after tailnet setup.'"

if [[ -f "$ROOT/.env" ]]; then
  run scp "$ROOT/.env" "$REMOTE_HOST:$REMOTE_ABS/.env"
  run ssh "$REMOTE_HOST" "chmod 600 '$REMOTE_ABS/.env'"
fi

SERVICE_BODY="[Unit]
Description=Trading CLI Bot MarketData Appliance Collector
After=network-online.target

[Service]
Type=simple
WorkingDirectory=${REMOTE_ABS}
ExecStart=${REMOTE_ABS}/.venv/bin/python ${REMOTE_ABS}/cli/tbot.py marketdata collector
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"
HTTP_SERVICE_BODY="[Unit]
Description=Trading CLI Bot MarketData Snapshot HTTP
After=network-online.target ${SERVICE_NAME}

[Service]
Type=simple
WorkingDirectory=${REMOTE_ABS}
# Command name is legacy, but the HTTP app serves /snapshot/{venue}/{symbol}.
ExecStart=${REMOTE_ABS}/.venv/bin/python ${REMOTE_ABS}/cli/tbot.py marketdata btcusdt-http
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"

run ssh "$REMOTE_HOST" "mkdir -p ~/.config/systemd/user && cat > ~/.config/systemd/user/${SERVICE_NAME} <<'EOF'
${SERVICE_BODY}
EOF
cat > ~/.config/systemd/user/${HTTP_SERVICE_NAME} <<'EOF'
${HTTP_SERVICE_BODY}
EOF
systemctl --user daemon-reload
systemctl --user disable --now '${LEGACY_SERVICE_NAME}' 2>/dev/null || true
systemctl --user enable '${SERVICE_NAME}'
systemctl --user enable '${HTTP_SERVICE_NAME}'
systemctl --user restart '${SERVICE_NAME}'
systemctl --user restart '${HTTP_SERVICE_NAME}'
loginctl enable-linger \"\$(whoami)\" 2>/dev/null || true
systemctl --user status '${SERVICE_NAME}' --no-pager
systemctl --user status '${HTTP_SERVICE_NAME}' --no-pager
if command -v tailscale >/dev/null; then tailscale ip -4 2>/dev/null | sed 's#^#Tailscale IPv4: #'; fi
if ! grep -q '^TBOT_MARKETDATA_TOKEN=' '$REMOTE_ABS/.env' 2>/dev/null; then echo 'WARNING: TBOT_MARKETDATA_TOKEN is missing in remote .env; HTTP snapshot endpoints will return 503.'; fi"

echo "Done. Remote snapshots: ${REMOTE_HOST}:${REMOTE_ABS}/var/reports/{okx-live,okx-sim}-{btc,eth}-market-snapshot.json"
