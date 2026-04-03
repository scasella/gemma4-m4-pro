#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
AUTORESEARCH_BEST_CANDIDATE="${AUTORESEARCH_BEST_CANDIDATE:-${REPO_DIR}/../autoresearch/results/best_candidate.yaml}"
AUTORESEARCH_CURRENT_STATE_RECORD="${AUTORESEARCH_CURRENT_STATE_RECORD:-${REPO_DIR}/../autoresearch/results/current_state_best.json}"
AUTORESEARCH_CANDIDATE="${AUTORESEARCH_CANDIDATE:-${REPO_DIR}/../autoresearch/candidate.yaml}"
AUTORESEARCH_USE_CURRENT_STATE="${AUTORESEARCH_USE_CURRENT_STATE:-0}"
PRINT_CONFIG_ONLY="${PRINT_CONFIG_ONLY:-0}"

MODEL_PATH="${MODEL_PATH:-}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8080}"
CONTEXT="${CONTEXT:-}"
THREADS="${THREADS:-}"
THREADS_BATCH="${THREADS_BATCH:-}"
BATCH="${BATCH:-}"
UBATCH="${UBATCH:-}"
MIN_FREE_GB="${MIN_FREE_GB:-}"
ORIGINAL_HYPURA_MEMORY_RESERVE_GB="${HYPURA_MEMORY_RESERVE_GB:-}"
ORIGINAL_HYPURA_KEEP_RESIDENT_HEADROOM_GB="${HYPURA_KEEP_RESIDENT_HEADROOM_GB:-}"
ORIGINAL_HYPURA_PRELOAD_HEADROOM_GB="${HYPURA_PRELOAD_HEADROOM_GB:-}"
ORIGINAL_HYPURA_GPU_RUNTIME_OVERHEAD_GB="${HYPURA_GPU_RUNTIME_OVERHEAD_GB:-}"

CONFIG_SOURCE=""
CONFIG_MODE="defaults"
if [[ "${AUTORESEARCH_USE_CURRENT_STATE}" == "1" && -f "${AUTORESEARCH_CURRENT_STATE_RECORD}" ]]; then
  CURRENT_STATE_CANDIDATE="$(
    AUTORESEARCH_CURRENT_STATE_RECORD="${AUTORESEARCH_CURRENT_STATE_RECORD}" python3 - <<'PY'
import json
import os
from pathlib import Path

record_path = Path(os.environ["AUTORESEARCH_CURRENT_STATE_RECORD"]).resolve()
record = json.loads(record_path.read_text(encoding="utf-8"))
candidate_path = record.get("current_state_candidate_path")
if record.get("no_valid_winner") or not candidate_path:
    raise SystemExit(0)
candidate = Path(str(candidate_path)).expanduser()
if not candidate.is_absolute():
    candidate = (record_path.parent / candidate).resolve()
if candidate.exists():
    print(candidate)
PY
  )"
  if [[ -n "${CURRENT_STATE_CANDIDATE}" ]]; then
    CONFIG_SOURCE="${CURRENT_STATE_CANDIDATE}"
    CONFIG_MODE="current-state"
  else
    echo "Current-state config unavailable or invalid; falling back to stable best." >&2
  fi
fi

if [[ -z "${CONFIG_SOURCE}" && -f "${AUTORESEARCH_BEST_CANDIDATE}" ]]; then
  CONFIG_SOURCE="${AUTORESEARCH_BEST_CANDIDATE}"
  CONFIG_MODE="stable-best"
elif [[ -z "${CONFIG_SOURCE}" && -f "${AUTORESEARCH_CANDIDATE}" ]]; then
  CONFIG_SOURCE="${AUTORESEARCH_CANDIDATE}"
  CONFIG_MODE="tracked-candidate"
fi

if [[ -n "${CONFIG_SOURCE}" ]]; then
  eval "$(
    CONFIG_SOURCE="${CONFIG_SOURCE}" python3 - <<'PY'
import os
import shlex
from pathlib import Path

candidate_path = Path(os.environ["CONFIG_SOURCE"]).resolve()
lines = candidate_path.read_text(encoding="utf-8").splitlines()
top = {}
backend = {}
in_backend = False

for raw in lines:
    if not raw.strip() or raw.lstrip().startswith("#"):
        continue
    if raw.startswith("backend_config:"):
        in_backend = True
        continue
    if in_backend and not raw.startswith("  "):
        in_backend = False
    target = backend if in_backend else top
    line = raw.strip()
    if ":" not in line:
        continue
    key, value = line.split(":", 1)
    key = key.strip()
    value = value.strip()
    if not value or value == "{}":
        continue
    if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
        value = value[1:-1]
    target[key] = value

model_path = top.get("model_path", "../models/gemma-4-26B-A4B-it-Q4_K_M.gguf")
model_path = str((candidate_path.parent / model_path).resolve())

assignments = {
    "AUTO_MODEL_PATH": model_path,
    "AUTO_CONTEXT": top.get("context", "4096"),
    "AUTO_MIN_FREE_GB": top.get("min_free_gb", "4.0"),
    "AUTO_THREADS": backend.get("threads", "10"),
    "AUTO_THREADS_BATCH": backend.get("threads_batch", backend.get("threads", "10")),
    "AUTO_BATCH": backend.get("batch_size", "512"),
    "AUTO_UBATCH": backend.get("ubatch_size", backend.get("batch_size", "512")),
    "AUTO_MEMORY_RESERVE_GB": backend.get("memory_reserve_gb", ""),
    "AUTO_KEEP_RESIDENT_HEADROOM_GB": backend.get("keep_resident_headroom_gb", ""),
    "AUTO_PRELOAD_HEADROOM_GB": backend.get("preload_headroom_gb", ""),
    "AUTO_GPU_RUNTIME_OVERHEAD_GB": backend.get("gpu_runtime_overhead_gb", ""),
}

for key, value in assignments.items():
    print(f"{key}={shlex.quote(str(value))}")
PY
  )"
fi

MODEL_PATH="${MODEL_PATH:-${AUTO_MODEL_PATH:-${REPO_DIR}/../models/gemma-4-26B-A4B-it-Q4_K_M.gguf}}"
CONTEXT="${CONTEXT:-${AUTO_CONTEXT:-4096}}"
THREADS="${THREADS:-${AUTO_THREADS:-10}}"
THREADS_BATCH="${THREADS_BATCH:-${AUTO_THREADS_BATCH:-14}}"
BATCH="${BATCH:-${AUTO_BATCH:-512}}"
UBATCH="${UBATCH:-${AUTO_UBATCH:-256}}"
MIN_FREE_GB="${MIN_FREE_GB:-${AUTO_MIN_FREE_GB:-4.0}}"

if [[ ! -f "${MODEL_PATH}" ]]; then
  echo "Model not found: ${MODEL_PATH}" >&2
  echo "Set MODEL_PATH to your Gemma 4 GGUF file." >&2
  exit 1
fi

eval "$(
  python3 - <<'PY'
import re
import shlex
import subprocess

page_size = int(subprocess.check_output(["sysctl", "-n", "hw.pagesize"], text=True).strip())
total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip())
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
available = available_pages * page_size
used = max(total - available, 0)

swap_output = subprocess.check_output(["sysctl", "vm.swapusage"], text=True)
swap_match = re.search(r"used = ([0-9.]+)([MG])", swap_output)
swap_used_gb = 0.0
if swap_match:
    value = float(swap_match.group(1))
    unit = swap_match.group(2)
    swap_used_gb = value / 1024.0 if unit == "M" else value

assignments = {
    "CURRENT_TOTAL_GB": f"{total / float(1 << 30):.3f}",
    "CURRENT_USED_GB": f"{used / float(1 << 30):.3f}",
    "CURRENT_AVAILABLE_GB": f"{available / float(1 << 30):.3f}",
    "CURRENT_SWAP_USED_GB": f"{swap_used_gb:.3f}",
}

for key, value in assignments.items():
    print(f"{key}={shlex.quote(value)}")
PY
)"

cd "${REPO_DIR}"

if [[ ! -x target/release/hypura ]]; then
  cargo build --release
fi

if [[ -z "${HYPURA_MEMORY_RESERVE_GB:-}" && -n "${AUTO_MEMORY_RESERVE_GB:-}" ]]; then
  HYPURA_MEMORY_RESERVE_GB="${AUTO_MEMORY_RESERVE_GB}"
fi

RESERVE_SOURCE="dynamic"
if [[ -z "${HYPURA_MEMORY_RESERVE_GB:-}" ]]; then
  HYPURA_MEMORY_RESERVE_GB="$(CURRENT_USED_GB="${CURRENT_USED_GB}" MIN_FREE_GB="${MIN_FREE_GB}" python3 - <<'PY'
import os

reserve_gb = float(os.environ["CURRENT_USED_GB"]) + float(os.environ["MIN_FREE_GB"])
print(f"{reserve_gb:.3f}")
PY
  )"
elif [[ -n "${ORIGINAL_HYPURA_MEMORY_RESERVE_GB}" ]]; then
  RESERVE_SOURCE="env"
else
  RESERVE_SOURCE="candidate"
fi

export HYPURA_MEMORY_RESERVE_GB
KEEP_RESIDENT_HEADROOM_GB="${HYPURA_KEEP_RESIDENT_HEADROOM_GB:-${AUTO_KEEP_RESIDENT_HEADROOM_GB:-${HYPURA_MEMORY_RESERVE_GB}}}"
PRELOAD_HEADROOM_GB="${HYPURA_PRELOAD_HEADROOM_GB:-${AUTO_PRELOAD_HEADROOM_GB:-$(KEEP_RESIDENT_HEADROOM_GB="${KEEP_RESIDENT_HEADROOM_GB}" python3 - <<'PY'
import os
print(f"{float(os.environ['KEEP_RESIDENT_HEADROOM_GB']) + 2.0:.3f}")
PY
)}}"
export HYPURA_KEEP_RESIDENT_HEADROOM_GB="${KEEP_RESIDENT_HEADROOM_GB}"
export HYPURA_PRELOAD_HEADROOM_GB="${PRELOAD_HEADROOM_GB}"
if [[ -z "${HYPURA_GPU_RUNTIME_OVERHEAD_GB:-}" && -n "${AUTO_GPU_RUNTIME_OVERHEAD_GB:-}" ]]; then
  export HYPURA_GPU_RUNTIME_OVERHEAD_GB="${AUTO_GPU_RUNTIME_OVERHEAD_GB}"
fi

echo "Launch config:" >&2
echo "  Mode: ${CONFIG_MODE}" >&2
echo "  Source: ${CONFIG_SOURCE:-"(none)"}" >&2
echo "  Host: ${HOST}" >&2
echo "  Port: ${PORT}" >&2
echo "  Model: ${MODEL_PATH}" >&2
echo "  Context: ${CONTEXT}" >&2
echo "  Threads: ${THREADS}" >&2
echo "  Prompt threads: ${THREADS_BATCH}" >&2
echo "  Batch: ${BATCH}" >&2
echo "  Micro-batch: ${UBATCH}" >&2
echo "  Min free floor: ${MIN_FREE_GB} GB" >&2
echo "  Memory reserve: ${HYPURA_MEMORY_RESERVE_GB} GB (${RESERVE_SOURCE})" >&2
echo "  Keep resident headroom: ${KEEP_RESIDENT_HEADROOM_GB} GB" >&2
echo "  Preload headroom: ${PRELOAD_HEADROOM_GB} GB" >&2
echo "  Current memory: ${CURRENT_USED_GB} GB used / ${CURRENT_TOTAL_GB} GB total" >&2
echo "  Current available: ${CURRENT_AVAILABLE_GB} GB" >&2
echo "  Current swap used: ${CURRENT_SWAP_USED_GB} GB" >&2
if [[ -n "${HYPURA_GPU_RUNTIME_OVERHEAD_GB:-}" ]]; then
  echo "  GPU runtime overhead: ${HYPURA_GPU_RUNTIME_OVERHEAD_GB} GB" >&2
fi

if [[ "${PRINT_CONFIG_ONLY}" == "1" ]]; then
  exit 0
fi

exec target/release/hypura serve "${MODEL_PATH}" \
  --host "${HOST}" \
  --port "${PORT}" \
  --context "${CONTEXT}" \
  --threads "${THREADS}" \
  --threads-batch "${THREADS_BATCH}" \
  --batch "${BATCH}" \
  --ubatch "${UBATCH}"
