#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

MODE="${MODE:-auto}"
PRINT_DECISION_ONLY="${PRINT_DECISION_ONLY:-0}"
PRINT_DECISION_JSON="${PRINT_DECISION_JSON:-0}"
RUNTIME_COMPARISON_JSON="${RUNTIME_COMPARISON_JSON:-${SCRIPT_DIR}/results/runtime_comparison.json}"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-}"
HYPURA_HOST="${HYPURA_HOST:-${HOST}}"
HYPURA_PORT="${HYPURA_PORT:-${PORT:-8080}}"
FLASHMOE_HOST="${FLASHMOE_HOST:-${HOST}}"
FLASHMOE_PORT="${FLASHMOE_PORT:-${PORT:-8097}}"

AUTO_AVAILABLE_GB_OVERRIDE="${AUTO_AVAILABLE_GB_OVERRIDE:-}"
AUTO_SWAP_USED_GB_OVERRIDE="${AUTO_SWAP_USED_GB_OVERRIDE:-}"

HYPURA_LAUNCHER="${ROOT_DIR}/hypura-main/scripts/serve-gemma4-m4pro.sh"
FLASHMOE_LAUNCHER="${SCRIPT_DIR}/flashmoe_gemma4_serve.sh"
FLASHMOE_ROOT="${FLASHMOE_ROOT:-${ROOT_DIR}/anemll-flash-llama.cpp-gemma4}"
FLASHMOE_BIN="${FLASHMOE_BIN:-${FLASHMOE_ROOT}/build-smoke/bin/llama-server}"
FLASHMOE_SIDECAR_DIR="${FLASHMOE_SIDECAR_DIR:-${SCRIPT_DIR}/results/flashmoe_full_sidecar}"

HYPURA_LAUNCHABLE="0"
if [[ -x "${HYPURA_LAUNCHER}" && -d "${ROOT_DIR}/hypura-main" ]]; then
  HYPURA_LAUNCHABLE="1"
fi

FLASHMOE_LAUNCHABLE="0"
if [[ -x "${FLASHMOE_LAUNCHER}" && -x "${FLASHMOE_BIN}" && -f "${FLASHMOE_SIDECAR_DIR}/manifest.json" ]]; then
  FLASHMOE_LAUNCHABLE="1"
fi

usage() {
  cat <<EOF >&2
Usage: $0 [--mode auto|speed|memory]

Modes:
  auto    Reuse a single live runtime first; if both are live, pick by the current machine state.
  speed   Force the tuned Hypura server path.
  memory  Force the lighter Flash-MoE resident-server path.

Useful environment overrides:
  PRINT_DECISION_ONLY=1         Print the decision and exit.
  PRINT_DECISION_JSON=1         Print the decision as JSON and exit.
  AUTO_AVAILABLE_GB_OVERRIDE=8  Simulate a lower-memory machine state.
  AUTO_SWAP_USED_GB_OVERRIDE=3  Simulate a higher swap level.
  PORT=8089                     Use one port for the chosen runtime.
  HYPURA_PORT=8089              Override only the Hypura port.
  FLASHMOE_PORT=8111            Override only the Flash-MoE port.
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
    --help|-h)
      usage
      ;;
    *)
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

eval "$(
  MODE="${MODE}" \
  RUNTIME_COMPARISON_JSON="${RUNTIME_COMPARISON_JSON}" \
  HYPURA_HOST="${HYPURA_HOST}" \
  HYPURA_PORT="${HYPURA_PORT}" \
  FLASHMOE_HOST="${FLASHMOE_HOST}" \
  FLASHMOE_PORT="${FLASHMOE_PORT}" \
  AUTO_AVAILABLE_GB_OVERRIDE="${AUTO_AVAILABLE_GB_OVERRIDE}" \
  AUTO_SWAP_USED_GB_OVERRIDE="${AUTO_SWAP_USED_GB_OVERRIDE}" \
  HYPURA_LAUNCHABLE="${HYPURA_LAUNCHABLE}" \
  FLASHMOE_LAUNCHABLE="${FLASHMOE_LAUNCHABLE}" \
  python3 - <<'PY'
import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from urllib import request


def server_ready(url: str) -> bool:
    try:
        with request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def current_memory_state() -> tuple[float, float]:
    available_override = os.environ.get("AUTO_AVAILABLE_GB_OVERRIDE", "").strip()
    swap_override = os.environ.get("AUTO_SWAP_USED_GB_OVERRIDE", "").strip()
    if available_override:
        available_gb = float(available_override)
    else:
        page_size = int(subprocess.check_output(["sysctl", "-n", "hw.pagesize"], text=True).strip())
        vm_stat = subprocess.check_output(["vm_stat"], text=True)
        counts = {}
        for line in vm_stat.splitlines():
            match = re.match(r"^([^:]+):\s+([0-9]+)\.$", line.strip())
            if match:
                counts[match.group(1)] = int(match.group(2))
        available_pages = (
            counts.get("Pages free", 0)
            + counts.get("Pages inactive", 0)
            + counts.get("Pages speculative", 0)
            + counts.get("Pages purgeable", 0)
        )
        available_gb = (available_pages * page_size) / float(1 << 30)

    if swap_override:
        swap_used_gb = float(swap_override)
    else:
        swap_output = subprocess.check_output(["sysctl", "vm.swapusage"], text=True)
        swap_match = re.search(r"used = ([0-9.]+)([MG])", swap_output)
        swap_used_gb = 0.0
        if swap_match:
            value = float(swap_match.group(1))
            unit = swap_match.group(2)
            swap_used_gb = value / 1024.0 if unit == "M" else value
    return available_gb, swap_used_gb


mode = os.environ["MODE"]
comparison_path = Path(os.environ["RUNTIME_COMPARISON_JSON"])
comparison = {}
if comparison_path.exists():
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))

hypura_probe = comparison.get("hypura_server_probe", {})
flash_probe = comparison.get("flashmoe_server_probe", {})
hypura_rss_gb = float((hypura_probe.get("server") or {}).get("rss_gb", 12.5))
flash_rss_gb = float((flash_probe.get("server") or {}).get("rss_gb", 4.5))
hypura_launchable = os.environ.get("HYPURA_LAUNCHABLE", "0") == "1"
flash_launchable = os.environ.get("FLASHMOE_LAUNCHABLE", "0") == "1"

available_gb, swap_used_gb = current_memory_state()
speed_min_available_gb = max(12.0, hypura_rss_gb + 1.5)
memory_min_available_gb = max(5.0, flash_rss_gb + 0.5)
speed_swap_ceiling_gb = 2.0

hypura_ready = server_ready(f"http://{os.environ['HYPURA_HOST']}:{os.environ['HYPURA_PORT']}/api/tags")
flash_ready = server_ready(f"http://{os.environ['FLASHMOE_HOST']}:{os.environ['FLASHMOE_PORT']}/health")

if mode == "speed":
    chosen = "speed"
    reason = "forced speed mode"
elif mode == "memory":
    chosen = "memory"
    reason = "forced memory mode"
else:
    if hypura_ready and flash_ready:
        if available_gb >= speed_min_available_gb and swap_used_gb <= speed_swap_ceiling_gb:
            chosen = "speed"
            reason = (
                "Both runtimes are reachable, and the current machine state still justifies "
                f"Hypura ({available_gb:.2f} GB available, {swap_used_gb:.2f} GB swap)"
            )
        else:
            chosen = "memory"
            reason = (
                "Both runtimes are reachable, and the current machine state now favors "
                f"Flash-MoE ({available_gb:.2f} GB available, {swap_used_gb:.2f} GB swap)"
            )
    elif hypura_ready:
        chosen = "speed"
        reason = "Hypura is already reachable, so auto mode will reuse it"
    elif flash_ready:
        chosen = "memory"
        reason = "Flash-MoE is already reachable, so auto mode will reuse it"
    elif hypura_launchable and not flash_launchable:
        chosen = "speed"
        reason = "Hypura is launchable in this checkout, while the optional Flash-MoE runtime is not configured"
    elif flash_launchable and not hypura_launchable:
        chosen = "memory"
        reason = "Flash-MoE is launchable in this checkout, while Hypura is not configured"
    elif not hypura_launchable and not flash_launchable:
        chosen = "speed"
        reason = "Neither runtime is fully configured in this checkout yet; auto mode is falling back to the speed path"
    elif available_gb >= speed_min_available_gb and swap_used_gb <= speed_swap_ceiling_gb:
        chosen = "speed"
        reason = (
            f"available {available_gb:.2f} GB meets the Hypura threshold "
            f"{speed_min_available_gb:.2f} GB and swap {swap_used_gb:.2f} GB stays below "
            f"{speed_swap_ceiling_gb:.2f} GB"
        )
    else:
        chosen = "memory"
        reason = (
            f"available {available_gb:.2f} GB or swap {swap_used_gb:.2f} GB does not justify "
            f"the Hypura threshold ({speed_min_available_gb:.2f} GB available, "
            f"{speed_swap_ceiling_gb:.2f} GB swap ceiling)"
        )

for key, value in {
    "AUTO_CHOSEN": chosen,
    "AUTO_REASON": reason,
    "AUTO_AVAILABLE_GB": f"{available_gb:.3f}",
    "AUTO_SWAP_USED_GB": f"{swap_used_gb:.3f}",
    "AUTO_SPEED_MIN_AVAILABLE_GB": f"{speed_min_available_gb:.3f}",
    "AUTO_MEMORY_MIN_AVAILABLE_GB": f"{memory_min_available_gb:.3f}",
    "AUTO_SPEED_SWAP_CEILING_GB": f"{speed_swap_ceiling_gb:.3f}",
    "AUTO_HYPURA_READY": "1" if hypura_ready else "0",
    "AUTO_FLASHMOE_READY": "1" if flash_ready else "0",
    "AUTO_HYPURA_LAUNCHABLE": "1" if hypura_launchable else "0",
    "AUTO_FLASHMOE_LAUNCHABLE": "1" if flash_launchable else "0",
    "AUTO_HYPURA_RSS_GB": f"{hypura_rss_gb:.3f}",
    "AUTO_FLASHMOE_RSS_GB": f"{flash_rss_gb:.3f}",
}.items():
    print(f"{key}={shlex.quote(str(value))}")
PY
)"

if [[ "${AUTO_CHOSEN}" == "speed" ]]; then
  TARGET_NAME="Hypura"
  TARGET_HOST="${HYPURA_HOST}"
  TARGET_PORT="${HYPURA_PORT}"
  TARGET_READY="${AUTO_HYPURA_READY}"
  TARGET_CMD="${HYPURA_LAUNCHER}"
else
  TARGET_NAME="Flash-MoE"
  TARGET_HOST="${FLASHMOE_HOST}"
  TARGET_PORT="${FLASHMOE_PORT}"
  TARGET_READY="${AUTO_FLASHMOE_READY}"
  TARGET_CMD="${FLASHMOE_LAUNCHER}"
fi

if [[ "${PRINT_DECISION_JSON}" != "1" ]]; then
  echo "Auto launcher decision:" >&2
  echo "  Mode: ${MODE}" >&2
  echo "  Chosen runtime: ${TARGET_NAME}" >&2
  echo "  Reason: ${AUTO_REASON}" >&2
  echo "  Available memory: ${AUTO_AVAILABLE_GB} GB" >&2
  echo "  Swap used: ${AUTO_SWAP_USED_GB} GB" >&2
  echo "  Hypura resident estimate: ${AUTO_HYPURA_RSS_GB} GB" >&2
  echo "  Flash-MoE resident estimate: ${AUTO_FLASHMOE_RSS_GB} GB" >&2
  echo "  Hypura launchable here: ${AUTO_HYPURA_LAUNCHABLE}" >&2
  echo "  Flash-MoE launchable here: ${AUTO_FLASHMOE_LAUNCHABLE}" >&2
  echo "  Hypura availability threshold: ${AUTO_SPEED_MIN_AVAILABLE_GB} GB" >&2
  echo "  Flash-MoE availability threshold: ${AUTO_MEMORY_MIN_AVAILABLE_GB} GB" >&2
  echo "  Target host: ${TARGET_HOST}" >&2
  echo "  Target port: ${TARGET_PORT}" >&2
fi

if [[ "${PRINT_DECISION_JSON}" == "1" ]]; then
  TARGET_NAME="${TARGET_NAME}" \
  TARGET_HOST="${TARGET_HOST}" \
  TARGET_PORT="${TARGET_PORT}" \
  TARGET_READY="${TARGET_READY}" \
  REQUESTED_MODE="${MODE}" \
  AUTO_CHOSEN="${AUTO_CHOSEN}" \
  AUTO_REASON="${AUTO_REASON}" \
  AUTO_AVAILABLE_GB="${AUTO_AVAILABLE_GB}" \
  AUTO_SWAP_USED_GB="${AUTO_SWAP_USED_GB}" \
  AUTO_SPEED_MIN_AVAILABLE_GB="${AUTO_SPEED_MIN_AVAILABLE_GB}" \
  AUTO_MEMORY_MIN_AVAILABLE_GB="${AUTO_MEMORY_MIN_AVAILABLE_GB}" \
  AUTO_SPEED_SWAP_CEILING_GB="${AUTO_SPEED_SWAP_CEILING_GB}" \
  AUTO_HYPURA_LAUNCHABLE="${AUTO_HYPURA_LAUNCHABLE}" \
  AUTO_FLASHMOE_LAUNCHABLE="${AUTO_FLASHMOE_LAUNCHABLE}" \
  AUTO_HYPURA_RSS_GB="${AUTO_HYPURA_RSS_GB}" \
  AUTO_FLASHMOE_RSS_GB="${AUTO_FLASHMOE_RSS_GB}" \
  python3 - <<'PY'
import json
import os

print(json.dumps({
    "requested_mode": os.environ["REQUESTED_MODE"],
    "chosen_runtime": os.environ["AUTO_CHOSEN"],
    "target_name": os.environ["TARGET_NAME"],
    "target_host": os.environ["TARGET_HOST"],
    "target_port": int(os.environ["TARGET_PORT"]),
    "target_ready": os.environ["TARGET_READY"] == "1",
    "reason": os.environ["AUTO_REASON"],
    "available_gb": float(os.environ["AUTO_AVAILABLE_GB"]),
    "swap_used_gb": float(os.environ["AUTO_SWAP_USED_GB"]),
    "hypura_launchable": os.environ["AUTO_HYPURA_LAUNCHABLE"] == "1",
    "flashmoe_launchable": os.environ["AUTO_FLASHMOE_LAUNCHABLE"] == "1",
    "speed_min_available_gb": float(os.environ["AUTO_SPEED_MIN_AVAILABLE_GB"]),
    "memory_min_available_gb": float(os.environ["AUTO_MEMORY_MIN_AVAILABLE_GB"]),
    "speed_swap_ceiling_gb": float(os.environ["AUTO_SPEED_SWAP_CEILING_GB"]),
    "hypura_rss_gb": float(os.environ["AUTO_HYPURA_RSS_GB"]),
    "flashmoe_rss_gb": float(os.environ["AUTO_FLASHMOE_RSS_GB"]),
}))
PY
  exit 0
fi

if [[ "${PRINT_DECISION_ONLY}" == "1" ]]; then
  exit 0
fi

if [[ "${TARGET_READY}" == "1" ]]; then
  echo "${TARGET_NAME} is already reachable at ${TARGET_HOST}:${TARGET_PORT}." >&2
  exit 0
fi

exec env HOST="${TARGET_HOST}" PORT="${TARGET_PORT}" "${TARGET_CMD}"
