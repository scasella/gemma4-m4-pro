#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

MODE="${MODE:-speed}"
BASE_HOST="${HOST:-127.0.0.1}"
BASE_PORT="${PORT:-}"
HYPURA_HOST="${HYPURA_HOST:-${BASE_HOST}}"
HYPURA_PORT="${HYPURA_PORT:-${BASE_PORT:-8080}}"
FLASHMOE_HOST="${FLASHMOE_HOST:-${BASE_HOST}}"
FLASHMOE_PORT="${FLASHMOE_PORT:-${BASE_PORT:-8097}}"
FLASHMOE_ASK_MODE="${FLASHMOE_ASK_MODE:-auto}"
STREAM="${STREAM:-auto}"
AUTO_START_SERVER="${AUTO_START_SERVER:-1}"
AUTO_START_TIMEOUT_S="${AUTO_START_TIMEOUT_S:-180}"
AUTO_START_LOG="${AUTO_START_LOG:-}"
AUTO_STATE_FILE="${AUTO_STATE_FILE:-${SCRIPT_DIR}/results/auto_server_state.json}"
REPLACE_OTHER_RUNTIME="${REPLACE_OTHER_RUNTIME:-0}"
SPEED_CMD="${ROOT_DIR}/hypura-main/scripts/ask-gemma4-m4pro.sh"
SPEED_SERVER_HINT="${ROOT_DIR}/hypura-main/scripts/serve-gemma4-m4pro.sh"
MEMORY_CMD="${SCRIPT_DIR}/flashmoe_gemma4_ask.sh"
SERVER_START_CMD="${SCRIPT_DIR}/gemma4_server_start.sh"
AUTO_LAUNCHER="${SCRIPT_DIR}/serve_gemma4_auto.sh"

usage() {
  cat <<EOF >&2
Usage: $0 [--mode speed|memory|auto] [--replace] [--stream|--no-stream] "your prompt here"
Or pipe a prompt into stdin.

Modes:
  speed   Use the tuned Hypura server path. Auto-starts it unless AUTO_START_SERVER=0.
  memory  Use the Flash-MoE alternate. Prefers the resident server and auto-starts it unless AUTO_START_SERVER=0.
  auto    Reuse a single live runtime first; if both are live, choose by the current machine state. Auto-starts it unless AUTO_START_SERVER=0.

Options:
  --replace    Stop the other live runtime first before starting the chosen one.
  --stream     Stream tokens while the answer is being generated.
  --no-stream  Disable token streaming.
EOF
  exit 1
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --mode)
      [[ "$#" -ge 2 ]] || usage
      MODE="$2"
      shift 2
      ;;
    --replace)
      REPLACE_OTHER_RUNTIME="1"
      shift
      ;;
    --stream)
      STREAM="1"
      shift
      ;;
    --no-stream)
      STREAM="0"
      shift
      ;;
    --help|-h)
      usage
      ;;
    --)
      shift
      break
      ;;
    *)
      break
      ;;
  esac
done

case "${MODE}" in
  speed|memory|auto)
    ;;
  *)
    echo "Unsupported mode: ${MODE}" >&2
    usage
    ;;
esac

if [[ "$#" -gt 0 ]]; then
  PROMPT="$*"
elif [[ ! -t 0 ]]; then
  PROMPT="$(cat)"
else
  usage
fi

if [[ "${STREAM}" == "auto" ]]; then
  if [[ -t 1 ]]; then
    STREAM="1"
  else
    STREAM="0"
  fi
fi

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

memory_server_available() {
  FLASHMOE_HOST="${FLASHMOE_HOST}" FLASHMOE_PORT="${FLASHMOE_PORT}" python3 - <<'PY' >/dev/null 2>&1
import os
from urllib import request

url = f"http://{os.environ['FLASHMOE_HOST']}:{os.environ['FLASHMOE_PORT']}/health"
with request.urlopen(url, timeout=2) as response:
    if response.status != 200:
        raise SystemExit(1)
PY
}

run_speed() {
  if ! speed_available; then
    echo "The Hypura server is not reachable at ${HYPURA_HOST}:${HYPURA_PORT}." >&2
    echo "Start it with: ${SPEED_SERVER_HINT}" >&2
    return 1
  fi
  HOST="${HYPURA_HOST}" PORT="${HYPURA_PORT}" STREAM="${STREAM}" "${SPEED_CMD}" "${PROMPT}"
}

run_memory() {
  FLASHMOE_HOST="${FLASHMOE_HOST}" \
  FLASHMOE_PORT="${FLASHMOE_PORT}" \
  FLASHMOE_ASK_MODE="${FLASHMOE_ASK_MODE}" \
  STREAM="${STREAM}" \
  "${MEMORY_CMD}" "${PROMPT}"
}

auto_runtime_choice() {
  local decision_json
  decision_json="$(
    HYPURA_HOST="${HYPURA_HOST}" \
    HYPURA_PORT="${HYPURA_PORT}" \
    FLASHMOE_HOST="${FLASHMOE_HOST}" \
    FLASHMOE_PORT="${FLASHMOE_PORT}" \
    AUTO_AVAILABLE_GB_OVERRIDE="${AUTO_AVAILABLE_GB_OVERRIDE:-}" \
    AUTO_SWAP_USED_GB_OVERRIDE="${AUTO_SWAP_USED_GB_OVERRIDE:-}" \
    PRINT_DECISION_JSON=1 \
    MODE="auto" \
    "${AUTO_LAUNCHER}"
  )"
  DECISION_JSON="${decision_json}" python3 - <<'PY'
import json
import os

print(json.loads(os.environ["DECISION_JSON"])["chosen_runtime"])
PY
}

start_server_mode() {
  local start_mode="$1"
  HYPURA_HOST="${HYPURA_HOST}" \
  HYPURA_PORT="${HYPURA_PORT}" \
  FLASHMOE_HOST="${FLASHMOE_HOST}" \
  FLASHMOE_PORT="${FLASHMOE_PORT}" \
  AUTO_START_TIMEOUT_S="${AUTO_START_TIMEOUT_S}" \
  AUTO_START_LOG="${AUTO_START_LOG}" \
  AUTO_STATE_FILE="${AUTO_STATE_FILE}" \
  REPLACE_LIVE_RUNTIME="${REPLACE_OTHER_RUNTIME}" \
  PRINT_STATUS_AFTER=0 \
  "${SERVER_START_CMD}" --mode "${start_mode}"
}

case "${MODE}" in
  speed)
    if [[ "${REPLACE_OTHER_RUNTIME}" == "1" ]]; then
      if speed_available || [[ "${AUTO_START_SERVER}" == "1" ]]; then
        start_server_mode "speed"
      fi
    elif ! speed_available && [[ "${AUTO_START_SERVER}" == "1" ]]; then
      start_server_mode "speed"
    fi
    run_speed
    ;;
  memory)
    if [[ "${REPLACE_OTHER_RUNTIME}" == "1" ]]; then
      if memory_server_available || [[ "${AUTO_START_SERVER}" == "1" ]]; then
        start_server_mode "memory"
      fi
    elif ! memory_server_available && [[ "${AUTO_START_SERVER}" == "1" ]]; then
      start_server_mode "memory"
    fi
    run_memory
    ;;
  auto)
    local_auto_runtime="$(auto_runtime_choice)"
    if [[ "${REPLACE_OTHER_RUNTIME}" == "1" ]]; then
      if { [[ "${local_auto_runtime}" == "speed" ]] && speed_available; } || \
         { [[ "${local_auto_runtime}" == "memory" ]] && memory_server_available; } || \
         [[ "${AUTO_START_SERVER}" == "1" ]]; then
        start_server_mode "auto"
        local_auto_runtime="$(auto_runtime_choice)"
      fi
    fi
    if [[ "${local_auto_runtime}" == "speed" ]] && ! speed_available && [[ "${AUTO_START_SERVER}" == "1" ]]; then
      start_server_mode "auto"
      local_auto_runtime="$(auto_runtime_choice)"
    elif [[ "${local_auto_runtime}" == "memory" ]] && ! memory_server_available && [[ "${AUTO_START_SERVER}" == "1" ]]; then
      start_server_mode "auto"
      local_auto_runtime="$(auto_runtime_choice)"
    fi
    if [[ "${local_auto_runtime}" == "speed" ]]; then
      if ! speed_available && [[ "${AUTO_START_SERVER}" != "1" ]]; then
        run_memory
        exit $?
      fi
      run_speed
    elif [[ "${local_auto_runtime}" == "memory" ]]; then
      if memory_server_available; then
        FLASHMOE_ASK_MODE=server run_memory
      else
        run_memory
      fi
    else
      echo "Auto mode could not determine a runnable runtime." >&2
      exit 1
    fi
    ;;
esac
