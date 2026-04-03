#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="${AUTO_STATE_FILE:-${SCRIPT_DIR}/results/auto_server_state.json}"
STATUS_CMD="${SCRIPT_DIR}/gemma4_server_status.sh"
REMOVE_STATE="${REMOVE_STATE:-1}"
RUNTIME="${RUNTIME:-auto}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-}"
HYPURA_HOST="${HYPURA_HOST:-${HOST}}"
HYPURA_PORT="${HYPURA_PORT:-${PORT:-8080}}"
FLASHMOE_HOST="${FLASHMOE_HOST:-${HOST}}"
FLASHMOE_PORT="${FLASHMOE_PORT:-${PORT:-8097}}"

usage() {
  cat <<EOF >&2
Usage: $0 [--runtime auto|hypura|flashmoe|speed|memory|all]

Defaults:
  auto     Stop the saved auto-started runtime if one is recorded.
           Otherwise, if exactly one live runtime is detected, stop that one.
           If both runtimes are live, ask for an explicit choice.

Examples:
  $0
  $0 --runtime hypura
  $0 --runtime flashmoe
  $0 --runtime all

Useful environment overrides:
  REMOVE_STATE=0      Keep the saved auto-start state file.
  PORT=8089           Use one port for both runtimes.
  HYPURA_PORT=8089    Override only the Hypura port.
  FLASHMOE_PORT=8111  Override only the Flash-MoE port.
EOF
  exit 0
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --runtime)
      [[ "$#" -ge 2 ]] || usage
      RUNTIME="$2"
      shift 2
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

case "${RUNTIME}" in
  auto|hypura|flashmoe|speed|memory|all)
    ;;
  *)
    echo "Unsupported runtime: ${RUNTIME}" >&2
    usage
    ;;
esac

STATUS_JSON="$(
  AUTO_STATE_FILE="${STATE_FILE}" \
  HYPURA_HOST="${HYPURA_HOST}" \
  HYPURA_PORT="${HYPURA_PORT}" \
  FLASHMOE_HOST="${FLASHMOE_HOST}" \
  FLASHMOE_PORT="${FLASHMOE_PORT}" \
  PRINT_JSON=1 \
  "${STATUS_CMD}"
)"

AUTO_STATE_FILE="${STATE_FILE}" \
REMOVE_STATE="${REMOVE_STATE}" \
REQUESTED_RUNTIME="${RUNTIME}" \
STATUS_JSON="${STATUS_JSON}" \
python3 - <<'PY'
import json
import os
import signal
import subprocess
import time
from pathlib import Path


def runtime_key(value: str) -> str:
    lowered = value.strip().lower()
    if lowered in {"hypura", "speed"}:
        return "hypura"
    if lowered in {"flashmoe", "memory"}:
        return "flashmoe"
    return lowered


def pid_alive(pid: int) -> bool:
    result = subprocess.run(["ps", "-p", str(pid)], capture_output=True, text=True, check=False)
    return result.returncode == 0


def listeners_for_port(port: int) -> list[int]:
    result = subprocess.run(
        ["lsof", "-ti", f"TCP:{port}", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
        check=False,
    )
    pids: list[int] = []
    for line in result.stdout.splitlines():
        value = line.strip()
        if value.isdigit():
            pids.append(int(value))
    return pids


status = json.loads(os.environ["STATUS_JSON"])
requested = runtime_key(os.environ["REQUESTED_RUNTIME"])
state = status.get("state", {})
state_status = str(state.get("status", "missing"))
state_runtime = runtime_key(str(state.get("runtime", "")))

runtime_map = {
    "hypura": dict(status.get("hypura", {})),
    "flashmoe": dict(status.get("flashmoe", {})),
}

live = [name for name, payload in runtime_map.items() if payload.get("reachable")]

targets: list[str] = []
if requested == "all":
    targets = [name for name in ("hypura", "flashmoe") if runtime_map[name].get("reachable")]
    if not targets and state_status in {"active", "stale"} and state_runtime in runtime_map:
        targets = [state_runtime]
elif requested == "auto":
    if state_status in {"active", "stale"} and state_runtime in runtime_map:
        targets = [state_runtime]
    elif len(live) == 1:
        targets = [live[0]]
    elif len(live) > 1:
        print("Both Hypura and Flash-MoE are live. Use --runtime hypura, --runtime flashmoe, or --runtime all.")
        raise SystemExit(1)
else:
    targets = [requested]

targets = [name for name in targets if name in runtime_map]
targets = list(dict.fromkeys(targets))

if not targets:
    if state_status == "missing":
        print("No Gemma server to stop.")
    else:
        print("No live Gemma server matched the request.")
        if state_status in {"active", "stale", "unreadable"}:
            print(f"Saved state remains at {state.get('path', '')}.")
    raise SystemExit(0)

state_path = Path(str(state.get("path", os.environ["AUTO_STATE_FILE"])))
remove_state = os.environ.get("REMOVE_STATE", "1") == "1"
state_removed = False
stopped_any = False

for name in targets:
    payload = runtime_map[name]
    display_name = "Hypura" if name == "hypura" else "Flash-MoE"
    host = str(payload.get("host", "127.0.0.1"))
    port = int(payload.get("port", 0) or 0)
    pids: list[int] = []

    listener_pid = payload.get("listener_pid")
    if isinstance(listener_pid, int):
        pids.append(listener_pid)

    if state_runtime == name:
        for key in ("server_pid", "launcher_pid"):
            value = state.get(key)
            if isinstance(value, int):
                pids.append(value)
        state_port = int(state.get("port", 0) or 0)
        if state_port and state_port != port:
            port = state_port
        state_host = str(state.get("host", "") or host)
        host = state_host

    if port:
        pids.extend(listeners_for_port(port))

    unique_pids: list[int] = []
    for pid in pids:
        if pid in unique_pids:
            continue
        if not pid_alive(pid):
            continue
        unique_pids.append(pid)

    if not unique_pids:
        if port:
            print(f"No live {display_name} server found at {host}:{port}.")
        else:
            print(f"No live {display_name} server found.")
    else:
        stopped_any = True
        for pid in unique_pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue

        deadline = time.time() + 10
        still_alive = unique_pids[:]
        while time.time() < deadline:
            still_alive = [pid for pid in unique_pids if pid_alive(pid)]
            if not still_alive:
                break
            time.sleep(0.5)

        for pid in still_alive:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        if port:
            print(f"Stopped {display_name} server at {host}:{port}.")
        else:
            print(f"Stopped {display_name} server.")

    should_remove_state = (
        remove_state
        and state_path.exists()
        and (
            requested in {"auto", "all"}
            or state_runtime == name
        )
    )
    if should_remove_state and not state_removed:
        try:
            state_path.unlink()
            state_removed = True
            print(f"Removed state file: {state_path}")
        except FileNotFoundError:
            state_removed = True

if not stopped_any and state_status in {"active", "stale"} and state_path.exists() and remove_state:
    try:
        state_path.unlink()
        print(f"Removed stale state file: {state_path}")
    except FileNotFoundError:
        pass
elif state_path.exists() and not state_removed and remove_state and requested in {"auto", "all"} and state_status in {"active", "stale"}:
    try:
        state_path.unlink()
        print(f"Removed state file: {state_path}")
    except FileNotFoundError:
        pass
elif state_path.exists() and not state_removed and requested in {"auto", "all"} and not remove_state:
    print(f"State file kept: {state_path}")
PY
