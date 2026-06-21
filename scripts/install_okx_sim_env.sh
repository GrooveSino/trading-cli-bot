#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-tokyo-cloudserver}"
REMOTE_DIR="${REMOTE_DIR:-~/services/trading-cli-bot}"
SYNC_REMOTE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --remote) SYNC_REMOTE=1; shift ;;
    --remote-host) REMOTE_HOST="$2"; shift 2 ;;
    --remote-dir) REMOTE_DIR="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ROOT}/.env"
REQUIRED=(OKX_SIM_API_KEY OKX_SIM_API_SECRET OKX_SIM_PASSWORD)

for key in "${REQUIRED[@]}"; do
  if [[ -z "${!key:-}" ]]; then
    echo "missing ${key}; export it before running this script" >&2
    exit 1
  fi
done

update_env_file() {
  local path="$1"
  touch "$path"
  chmod 600 "$path"
  python3 - "$path" <<'PY'
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
keys = ["OKX_SIM_API_KEY", "OKX_SIM_API_SECRET", "OKX_SIM_PASSWORD"]
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
seen = set()
updated = []
for line in lines:
    name = line.split("=", 1)[0].strip() if "=" in line and not line.strip().startswith("#") else ""
    if name in keys:
        updated.append(f"{name}={os.environ[name]}")
        seen.add(name)
    else:
        updated.append(line)
for key in keys:
    if key not in seen:
        updated.append(f"{key}={os.environ[key]}")
path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")
PY
}

update_env_file "$ENV_FILE"
echo "updated local OKX sim env: ${ENV_FILE}"

if [[ "$SYNC_REMOTE" == "1" ]]; then
  remote_abs="$(ssh "$REMOTE_HOST" "python3 - <<'PY'
from pathlib import Path
print(Path('$REMOTE_DIR').expanduser())
PY")"
  tmp="$(mktemp)"
  cp "$ENV_FILE" "$tmp"
  scp "$tmp" "$REMOTE_HOST:${remote_abs}/.env" >/dev/null
  rm -f "$tmp"
  ssh "$REMOTE_HOST" "chmod 600 '${remote_abs}/.env'"
  echo "updated remote OKX sim env: ${REMOTE_HOST}:${remote_abs}/.env"
fi
