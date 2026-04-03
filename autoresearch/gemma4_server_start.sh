#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${MODE:-auto}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-}"
HYPURA_HOST="${HYPURA_HOST:-${HOST}}"
HYPURA_PORT="${HYPURA_PORT:-${PORT:-8080}}"
FLASHMOE_HOST="${FLASHMOE_HOST:-${HOST}}"
FLASHMOE_PORT="${FLASHMOE_PORT:-${PORT:-8097}}"
AUTO_AVAILABLE_GB_OVERRIDE="${AUTO_AVAILABLE_GB_OVERRIDE:-}"
AUTO_SWAP_USED_GB_OVERRIDE="${AUTO_SWAP_USED_GB_OVERRIDE:-}"
AUTO_START_TIMEOUT_S="${AUTO_START_TIMEOUT_S:-180}"
AUTO_START_LOG="${AUTO_START_LOG:-}"
AUTO_STATE_FILE="${AUTO_STATE_FILE:-${SCRIPT_DIR}/results/auto_server_state.json}"
AUTO_LAUNCHER="${SCRIPT_DIR}/serve_gemma4_auto.sh"
STATUS_CMD="${SCRIPT_DIR}/gemma4_server_status.sh"
PRINT_STATUS_AFTER="${PRINT_STATUS_AFTER:-1}"
REPLACE_LIVE_RUNTIME="${REPLACE_LIVE_RUNTIME:-0}"
STOP_CMD="${SCRIPT_DIR}/gemma4_server_stop.sh"

usage() {
  cat <<EOF >&2
Usage: $0 [--mode auto|speed|memory] [--replace]

Modes:
  auto    Reuse a single live runtime first; if both are live, choose by the current machine state.
  speed   Start or reuse the tuned Hypura server.
  memory  Start or reuse the lighter Flash-MoE resident server.

Options:
  --replace  Stop the other live runtime first before reusing or starting the chosen one.

Useful environment overrides:
  AUTO_START_TIMEOUT_S=180      Wait this many seconds for the server to come up.
  AUTO_START_LOG=/tmp/gemma.log Write startup output here instead of an auto-generated log path.
  PORT=8089                     Use one port for both runtimes.
  HYPURA_PORT=8089              Override only the Hypura port.
  FLASHMOE_PORT=8111            Override only the Flash-MoE port.
  AUTO_AVAILABLE_GB_OVERRIDE=8  Simulate a lower-memory machine state for auto mode.
  AUTO_SWAP_USED_GB_OVERRIDE=3  Simulate a higher swap level for auto mode.
EOF
  exit 0
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --mode)
      [[ "$#" -ge 2 ]] || usage
      MODE="$2"
      shift 2
      ;;
    --replace)
      REPLACE_LIVE_RUNTIME="1"
      shift
      ;;
    --help|-h)
      usage
      ;;
    *)
      echo "Unsupported argument: $1" >&2
      usage
      ;;
  esac
done

case "${MODE}" in
  auto|speed|memory)
    ;;
  *)
    echo "Unsupported mode: ${MODE}" >&2
    usage
    ;;
esac

speed_available() {
  HOST="${HYPURA_HOST}" PORT="${HYPURA_PORT}" python3 - <<'PY' >/dev/null 2>&1
import os
from urllib import request

url = f"http://{os.environ['HOST']}:{os.environ['PORT']}/api/tags"
with request.urlopen(url, timeout=2) as response:
    if response.status != 200:
        raise SystemExit(1)
PY
}

memory_available() {
  FLASHMOE_HOST="${FLASHMOE_HOST}" FLASHMOE_PORT="${FLASHMOE_PORT}" python3 - <<'PY' >/dev/null 2>&1
import os
from urllib import request

url = f"http://{os.environ['FLASHMOE_HOST']}:{os.environ['FLASHMOE_PORT']}/health"
with request.urlopen(url, timeout=2) as response:
    if response.status != 200:
        raise SystemExit(1)
PY
}

auto_decision_json() {
  HYPURA_HOST="${HYPURA_HOST}" \
  HYPURA_PORT="${HYPURA_PORT}" \
  FLASHMOE_HOST="${FLASHMOE_HOST}" \
  FLASHMOE_PORT="${FLASHMOE_PORT}" \
  AUTO_AVAILABLE_GB_OVERRIDE="${AUTO_AVAILABLE_GB_OVERRIDE}" \
  AUTO_SWAP_USED_GB_OVERRIDE="${AUTO_SWAP_USED_GB_OVERRIDE}" \
  PRINT_DECISION_JSON=1 \
  MODE="${1}" \
  "${AUTO_LAUNCHER}"
}

load_decision() {
  local decision_mode="$1"
  local decision_json
  decision_json="$(auto_decision_json "${decision_mode}")"
  eval "$(
    DECISION_JSON="${decision_json}" python3 - <<'PY'
import json
import os
import shlex

parsed = json.loads(os.environ["DECISION_JSON"])
assignments = {
    "DECISION_RUNTIME": parsed["chosen_runtime"],
    "DECISION_NAME": parsed["target_name"],
    "DECISION_HOST": parsed["target_host"],
    "DECISION_PORT": str(parsed["target_port"]),
    "DECISION_READY": "1" if parsed["target_ready"] else "0",
    "DECISION_REASON": parsed["reason"],
}
for key, value in assignments.items():
    print(f"{key}={shlex.quote(str(value))}")
PY
  )"
}

wait_for_runtime() {
  local runtime="$1"
  local deadline=$((SECONDS + AUTO_START_TIMEOUT_S))
  while (( SECONDS < deadline )); do
    if [[ "${runtime}" == "speed" ]]; then
      if speed_available; then
        return 0
      fi
    else
      if memory_available; then
        return 0
      fi
    fi
    sleep 1
  done
  return 1
}

load_decision "${MODE}"

stop_other_runtime_if_requested() {
  if [[ "${REPLACE_LIVE_RUNTIME}" != "1" ]]; then
    return 0
  fi

  local other_runtime=""
  if [[ "${DECISION_RUNTIME}" == "speed" ]]; then
    if memory_available; then
      other_runtime="flashmoe"
    fi
  else
    if speed_available; then
      other_runtime="hypura"
    fi
  fi

  if [[ -n "${other_runtime}" ]]; then
    echo "Stopping ${other_runtime} before starting ${DECISION_NAME}." >&2
    HYPURA_HOST="${HYPURA_HOST}" \
    HYPURA_PORT="${HYPURA_PORT}" \
    FLASHMOE_HOST="${FLASHMOE_HOST}" \
    FLASHMOE_PORT="${FLASHMOE_PORT}" \
    AUTO_STATE_FILE="${AUTO_STATE_FILE}" \
    "${STOP_CMD}" --runtime "${other_runtime}"
    load_decision "${MODE}"
  fi
}

stop_other_runtime_if_requested

if [[ "${DECISION_READY}" == "1" ]]; then
  echo "${DECISION_NAME} is already running at ${DECISION_HOST}:${DECISION_PORT}." >&2
  echo "Reason: ${DECISION_REASON}" >&2
  if [[ "${PRINT_STATUS_AFTER}" == "1" ]]; then
    HYPURA_HOST="${HYPURA_HOST}" \
    HYPURA_PORT="${HYPURA_PORT}" \
    FLASHMOE_HOST="${FLASHMOE_HOST}" \
    FLASHMOE_PORT="${FLASHMOE_PORT}" \
    AUTO_STATE_FILE="${AUTO_STATE_FILE}" \
    "${STATUS_CMD}"
  fi
  exit 0
fi

LOG_PATH="${AUTO_START_LOG}"
if [[ -z "${LOG_PATH}" ]]; then
  LOG_PATH="/tmp/gemma4-server-start-${MODE}-${DECISION_RUNTIME}-$(date +%Y%m%dT%H%M%S).log"
fi

echo "Starting ${DECISION_NAME} on ${DECISION_HOST}:${DECISION_PORT}." >&2
echo "Reason: ${DECISION_REASON}" >&2
echo "Log: ${LOG_PATH}" >&2

nohup env \
  HYPURA_HOST="${HYPURA_HOST}" \
  HYPURA_PORT="${HYPURA_PORT}" \
  FLASHMOE_HOST="${FLASHMOE_HOST}" \
  FLASHMOE_PORT="${FLASHMOE_PORT}" \
  AUTO_AVAILABLE_GB_OVERRIDE="${AUTO_AVAILABLE_GB_OVERRIDE}" \
  AUTO_SWAP_USED_GB_OVERRIDE="${AUTO_SWAP_USED_GB_OVERRIDE}" \
  MODE="${MODE}" \
  "${AUTO_LAUNCHER}" >"${LOG_PATH}" 2>&1 &
LAUNCHER_PID=$!
disown "${LAUNCHER_PID}" 2>/dev/null || true

mkdir -p "$(dirname "${AUTO_STATE_FILE}")"
AUTO_STATE_FILE="${AUTO_STATE_FILE}" \
DECISION_RUNTIME="${DECISION_RUNTIME}" \
DECISION_HOST="${DECISION_HOST}" \
DECISION_PORT="${DECISION_PORT}" \
DECISION_REASON="${DECISION_REASON}" \
AUTO_START_LOG="${LOG_PATH}" \
LAUNCHER_PID="${LAUNCHER_PID}" \
python3 - <<'PY'
import json
import os
import time
from pathlib import Path

payload = {
    "runtime": os.environ["DECISION_RUNTIME"],
    "host": os.environ["DECISION_HOST"],
    "port": int(os.environ["DECISION_PORT"]),
    "reason": os.environ["DECISION_REASON"],
    "log_path": os.environ["AUTO_START_LOG"],
    "launcher_pid": int(os.environ["LAUNCHER_PID"]),
    "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
Path(os.environ["AUTO_STATE_FILE"]).write_text(json.dumps(payload, indent=2), encoding="utf-8")
PY

if ! wait_for_runtime "${DECISION_RUNTIME}"; then
  echo "Timed out waiting for ${DECISION_NAME} to start." >&2
  echo "Last log lines from ${LOG_PATH}:" >&2
  tail -n 40 "${LOG_PATH}" >&2 || true
  exit 1
fi

AUTO_STATE_FILE="${AUTO_STATE_FILE}" python3 - <<'PY'
import json
import os
import subprocess
from pathlib import Path

path = Path(os.environ["AUTO_STATE_FILE"])
if not path.exists():
    raise SystemExit(0)
data = json.loads(path.read_text(encoding="utf-8"))
port = int(data["port"])
result = subprocess.run(
    ["lsof", "-ti", f"TCP:{port}", "-sTCP:LISTEN"],
    capture_output=True,
    text=True,
    check=False,
)
pids = [line.strip() for line in result.stdout.splitlines() if line.strip().isdigit()]
if pids:
    data["server_pid"] = int(pids[0])
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
PY

echo "${DECISION_NAME} is ready." >&2
if [[ "${PRINT_STATUS_AFTER}" == "1" ]]; then
  HYPURA_HOST="${HYPURA_HOST}" \
  HYPURA_PORT="${HYPURA_PORT}" \
  FLASHMOE_HOST="${FLASHMOE_HOST}" \
  FLASHMOE_PORT="${FLASHMOE_PORT}" \
  AUTO_STATE_FILE="${AUTO_STATE_FILE}" \
  "${STATUS_CMD}"
fi
