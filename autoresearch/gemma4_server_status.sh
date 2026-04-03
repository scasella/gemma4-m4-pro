#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="${AUTO_STATE_FILE:-${SCRIPT_DIR}/results/auto_server_state.json}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-}"
HYPURA_HOST="${HYPURA_HOST:-${HOST}}"
HYPURA_PORT="${HYPURA_PORT:-${PORT:-8080}}"
FLASHMOE_HOST="${FLASHMOE_HOST:-${HOST}}"
FLASHMOE_PORT="${FLASHMOE_PORT:-${PORT:-8097}}"
PRINT_JSON="${PRINT_JSON:-0}"
RUNTIME_COMPARISON_JSON="${RUNTIME_COMPARISON_JSON:-${SCRIPT_DIR}/results/runtime_comparison.json}"
AUTO_AVAILABLE_GB_OVERRIDE="${AUTO_AVAILABLE_GB_OVERRIDE:-}"
AUTO_SWAP_USED_GB_OVERRIDE="${AUTO_SWAP_USED_GB_OVERRIDE:-}"

usage() {
  cat <<EOF >&2
Usage: $0

Shows:
  - whether Hypura is reachable
  - whether Flash-MoE is reachable
  - whether there is saved auto-start state
  - whether that saved state still matches a live server
  - what auto mode would choose right now

Useful environment overrides:
  PRINT_JSON=1                 Print machine-readable JSON.
  PORT=8089                    Use one port for both runtimes.
  HYPURA_PORT=8089             Override only the Hypura port.
  FLASHMOE_PORT=8111           Override only the Flash-MoE port.
  AUTO_AVAILABLE_GB_OVERRIDE=8 Simulate a lower-memory machine state.
  AUTO_SWAP_USED_GB_OVERRIDE=3 Simulate a higher swap level.
EOF
  exit 0
}

case "${1:-}" in
  --help|-h)
    usage
    ;;
  "")
    ;;
  *)
    echo "Unsupported argument: $1" >&2
    usage
    ;;
esac

STATE_FILE="${STATE_FILE}" \
HYPURA_HOST="${HYPURA_HOST}" \
HYPURA_PORT="${HYPURA_PORT}" \
FLASHMOE_HOST="${FLASHMOE_HOST}" \
FLASHMOE_PORT="${FLASHMOE_PORT}" \
PRINT_JSON="${PRINT_JSON}" \
SCRIPT_DIR="${SCRIPT_DIR}" \
RUNTIME_COMPARISON_JSON="${RUNTIME_COMPARISON_JSON}" \
AUTO_AVAILABLE_GB_OVERRIDE="${AUTO_AVAILABLE_GB_OVERRIDE}" \
AUTO_SWAP_USED_GB_OVERRIDE="${AUTO_SWAP_USED_GB_OVERRIDE}" \
python3 - <<'PY'
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from urllib import error, request


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    result = subprocess.run(["ps", "-p", str(pid)], capture_output=True, text=True, check=False)
    return result.returncode == 0


def listener_pid(port: int) -> int | None:
    result = subprocess.run(
        ["lsof", "-ti", f"TCP:{port}", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in result.stdout.splitlines():
        value = line.strip()
        if value.isdigit():
            return int(value)
    return None


def probe_json(url: str) -> tuple[bool, dict[str, Any] | None, str | None]:
    try:
        with request.urlopen(url, timeout=2) as response:
            body = response.read().decode("utf-8")
            if response.status != 200:
                return False, None, f"http {response.status}"
            try:
                return True, json.loads(body), None
            except json.JSONDecodeError:
                return True, None, None
    except error.HTTPError as exc:
        return False, None, f"http {exc.code}"
    except Exception as exc:
        return False, None, str(exc)


def auto_recommendation() -> dict[str, Any]:
    script_path = Path(os.environ["SCRIPT_DIR"]) / "serve_gemma4_auto.sh"
    env = os.environ.copy()
    env["PRINT_DECISION_JSON"] = "1"
    env["MODE"] = "auto"
    result = subprocess.run(
        [str(script_path)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        return {
            "available": False,
            "error": (result.stderr or result.stdout or f"exit {result.returncode}").strip(),
        }
    try:
        payload = json.loads(result.stdout)
    except Exception as exc:
        return {
            "available": False,
            "error": f"invalid JSON from auto launcher: {exc}",
            "raw_stdout": result.stdout.strip(),
        }
    payload["available"] = True
    payload["error"] = ""
    return payload


def summarize_hypura(host: str, port: int) -> dict[str, Any]:
    reachable, payload, error_text = probe_json(f"http://{host}:{port}/api/tags")
    model_name = ""
    if payload:
        for item in payload.get("models") or []:
            model_name = str(item.get("name") or item.get("model") or "")
            if model_name:
                break
    return {
        "runtime": "Hypura",
        "host": host,
        "port": port,
        "reachable": reachable,
        "listener_pid": listener_pid(port),
        "model_name": model_name,
        "error": error_text or "",
    }


def summarize_flashmoe(host: str, port: int) -> dict[str, Any]:
    reachable, _, error_text = probe_json(f"http://{host}:{port}/health")
    return {
        "runtime": "Flash-MoE",
        "host": host,
        "port": port,
        "reachable": reachable,
        "listener_pid": listener_pid(port),
        "error": error_text or "",
    }


state_path = Path(os.environ["STATE_FILE"])
state_exists = state_path.exists()
state_data: dict[str, Any] = {}
state_error = ""
if state_exists:
    try:
        state_data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        state_error = str(exc)

hypura = summarize_hypura(os.environ["HYPURA_HOST"], int(os.environ["HYPURA_PORT"]))
flashmoe = summarize_flashmoe(os.environ["FLASHMOE_HOST"], int(os.environ["FLASHMOE_PORT"]))
live_runtimes = [item["runtime"] for item in (hypura, flashmoe) if item["reachable"]]
auto_choice = auto_recommendation()

state_runtime = str(state_data.get("runtime", "")) if state_data else ""
state_host = str(state_data.get("host", "")) if state_data else ""
state_port = int(state_data.get("port", 0) or 0) if state_data else 0
state_launcher_pid = state_data.get("launcher_pid")
state_server_pid = state_data.get("server_pid")
state_reason = str(state_data.get("reason", "")) if state_data else ""
state_log_path = str(state_data.get("log_path", "")) if state_data else ""

state_runtime_lookup = {
    "speed": hypura,
    "memory": flashmoe,
}
state_target = state_runtime_lookup.get(state_runtime)
state_matches_live = bool(
    state_target
    and state_target["reachable"]
    and state_target["host"] == state_host
    and state_target["port"] == state_port
)

if not state_exists:
    state_status = "missing"
elif state_error:
    state_status = "unreadable"
elif state_matches_live:
    state_status = "active"
else:
    state_status = "stale"

payload = {
    "state": {
        "status": state_status,
        "path": str(state_path),
        "exists": state_exists,
        "error": state_error,
        "runtime": state_runtime,
        "host": state_host,
        "port": state_port,
        "reason": state_reason,
        "log_path": state_log_path,
        "launcher_pid": state_launcher_pid,
        "launcher_pid_alive": pid_alive(state_launcher_pid if isinstance(state_launcher_pid, int) else None),
        "server_pid": state_server_pid,
        "server_pid_alive": pid_alive(state_server_pid if isinstance(state_server_pid, int) else None),
        "matches_live_runtime": state_matches_live,
    },
    "hypura": hypura,
    "flashmoe": flashmoe,
    "live_runtimes": live_runtimes,
    "auto_recommendation": auto_choice,
}

if os.environ.get("PRINT_JSON") == "1":
    print(json.dumps(payload, indent=2))
    raise SystemExit(0)

print("Gemma server status")
print()

print("Auto-start state")
print(f"  Status:       {state_status}")
print(f"  State file:   {state_path}")
if state_error:
    print(f"  Error:        {state_error}")
if state_data:
    print(f"  Runtime:      {state_runtime or '(unknown)'}")
    print(f"  Host:         {state_host or '(unknown)'}")
    print(f"  Port:         {state_port or '(unknown)'}")
    print(f"  Matches live: {'yes' if state_matches_live else 'no'}")
    if isinstance(state_server_pid, int):
        print(f"  Server PID:   {state_server_pid} ({'alive' if pid_alive(state_server_pid) else 'not running'})")
    if isinstance(state_launcher_pid, int):
        print(f"  Launcher PID: {state_launcher_pid} ({'alive' if pid_alive(state_launcher_pid) else 'not running'})")
    if state_reason:
        print(f"  Reason:       {state_reason}")
    if state_log_path:
        print(f"  Log:          {state_log_path}")
print()

print("Detected runtimes")
for item in (hypura, flashmoe):
    print(f"  {item['runtime']}:")
    print(f"    Endpoint:    {item['host']}:{item['port']}")
    print(f"    Reachable:   {'yes' if item['reachable'] else 'no'}")
    if item["listener_pid"] is not None:
        print(f"    Listener:    {item['listener_pid']}")
    if item.get("model_name"):
        print(f"    Model:       {item['model_name']}")
    if item.get("error") and not item["reachable"]:
        print(f"    Error:       {item['error']}")
print()

print("Auto mode right now")
if auto_choice.get("available"):
    print(f"  Chosen runtime:     {auto_choice.get('target_name', '(unknown)')}")
    print(f"  Reason:             {auto_choice.get('reason', '')}")
    print(f"  Available memory:   {auto_choice.get('available_gb', 0.0):.2f} GB")
    print(f"  Swap used:          {auto_choice.get('swap_used_gb', 0.0):.2f} GB")
    print(
        "  Thresholds:         "
        f"Hypura {auto_choice.get('speed_min_available_gb', 0.0):.2f} GB,"
        f" Flash-MoE {auto_choice.get('memory_min_available_gb', 0.0):.2f} GB,"
        f" swap ceiling {auto_choice.get('speed_swap_ceiling_gb', 0.0):.2f} GB"
    )
    print(
        f"  Target endpoint:    {auto_choice.get('target_host', '(unknown)')}:"
        f"{auto_choice.get('target_port', '(unknown)')}"
    )
    print(f"  Target reachable:   {'yes' if auto_choice.get('target_ready') else 'no'}")
else:
    print(f"  Error:              {auto_choice.get('error', 'unknown error')}")
print()

if live_runtimes:
    print(f"Live now: {', '.join(live_runtimes)}")
else:
    print("Live now: none")
PY
