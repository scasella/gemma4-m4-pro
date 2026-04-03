#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
FLASHMOE_SERVER_BEST_CANDIDATE_DEFAULT="${SCRIPT_DIR}/results/best_flashmoe_server_candidate.yaml"
FLASHMOE_FALLBACK_CANDIDATE_DEFAULT="${SCRIPT_DIR}/results/best_flashmoe_candidate.yaml"
if [[ -z "${FLASHMOE_BEST_CANDIDATE:-}" ]]; then
  if [[ -f "${FLASHMOE_SERVER_BEST_CANDIDATE_DEFAULT}" ]]; then
    FLASHMOE_BEST_CANDIDATE="${FLASHMOE_SERVER_BEST_CANDIDATE_DEFAULT}"
  else
    FLASHMOE_BEST_CANDIDATE="${FLASHMOE_FALLBACK_CANDIDATE_DEFAULT}"
  fi
fi

FLASHMOE_ROOT="${FLASHMOE_ROOT:-${ROOT_DIR}/anemll-flash-llama.cpp-gemma4}"
FLASHMOE_BIN="${FLASHMOE_BIN:-${FLASHMOE_ROOT}/build-smoke/bin/llama-server}"
MODEL_PATH="${MODEL_PATH:-}"
SIDECAR_DIR="${SIDECAR_DIR:-}"
MOE_SLOT_BANK="${MOE_SLOT_BANK:-}"
N_GPU_LAYERS="${N_GPU_LAYERS:-}"
THREADS="${THREADS:-}"
THREADS_BATCH="${THREADS_BATCH:-}"
BATCH="${BATCH:-}"
UBATCH="${UBATCH:-}"
CONTEXT="${CONTEXT:-}"
CACHE_PROMPT="${CACHE_PROMPT:-}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8097}"
SEED="${SEED:-42}"
TEMP="${TEMP:-0}"
TOP_K="${TOP_K:-1}"
TOP_P="${TOP_P:-1.0}"
PARALLEL="${PARALLEL:-}"
PROBE_JSON="${PROBE_JSON:-${SCRIPT_DIR}/results/flashmoe_best_probe.json}"

if [[ -f "${FLASHMOE_BEST_CANDIDATE}" ]]; then
  eval "$(
    FLASHMOE_BEST_CANDIDATE="${FLASHMOE_BEST_CANDIDATE}" python3 - <<'PY'
import os
import shlex
from pathlib import Path

candidate_path = Path(os.environ["FLASHMOE_BEST_CANDIDATE"]).resolve()
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
sidecar_path = backend.get("sidecar_dir", "results/flashmoe_full_sidecar")
sidecar_path = str((candidate_path.parent / sidecar_path).resolve())

assignments = {
    "AUTO_MODEL_PATH": model_path,
    "AUTO_CONTEXT": top.get("context", "4096"),
    "AUTO_THREADS": backend.get("threads", "8"),
    "AUTO_THREADS_BATCH": backend.get("threads_batch", backend.get("threads", "8")),
    "AUTO_BATCH": backend.get("batch_size", "1"),
    "AUTO_UBATCH": backend.get("ubatch_size", backend.get("batch_size", "1")),
    "AUTO_MOE_SLOT_BANK": backend.get("moe_slot_bank", "16"),
    "AUTO_N_GPU_LAYERS": backend.get("gpu_layers", "0"),
    "AUTO_SIDECAR_DIR": sidecar_path,
    "AUTO_PARALLEL": backend.get("parallel", "4"),
    "AUTO_CACHE_PROMPT": backend.get("cache_prompt", "true"),
}

for key, value in assignments.items():
    print(f"{key}={shlex.quote(str(value))}")
PY
  )"
fi

MODEL_PATH="${MODEL_PATH:-${AUTO_MODEL_PATH:-${ROOT_DIR}/models/gemma-4-26B-A4B-it-Q4_K_M.gguf}}"
SIDECAR_DIR="${SIDECAR_DIR:-${AUTO_SIDECAR_DIR:-${SCRIPT_DIR}/results/flashmoe_full_sidecar}}"
MOE_SLOT_BANK="${MOE_SLOT_BANK:-${AUTO_MOE_SLOT_BANK:-16}}"
N_GPU_LAYERS="${N_GPU_LAYERS:-${AUTO_N_GPU_LAYERS:-0}}"
THREADS="${THREADS:-${AUTO_THREADS:-8}}"
THREADS_BATCH="${THREADS_BATCH:-${AUTO_THREADS_BATCH:-8}}"
BATCH="${BATCH:-${AUTO_BATCH:-1}}"
UBATCH="${UBATCH:-${AUTO_UBATCH:-1}}"
CONTEXT="${CONTEXT:-${AUTO_CONTEXT:-4096}}"
PARALLEL="${PARALLEL:-${AUTO_PARALLEL:-4}}"
CACHE_PROMPT="${CACHE_PROMPT:-${AUTO_CACHE_PROMPT:-true}}"
CACHE_PROMPT="$(printf '%s' "${CACHE_PROMPT}" | tr '[:upper:]' '[:lower:]')"

case "${CACHE_PROMPT}" in
  true|false) ;;
  *)
    echo "CACHE_PROMPT must be 'true' or 'false', got: ${CACHE_PROMPT}" >&2
    exit 1
    ;;
esac

if [[ ! -x "${FLASHMOE_BIN}" ]]; then
  echo "Flash-MoE server binary not found: ${FLASHMOE_BIN}" >&2
  exit 1
fi

if [[ ! -f "${MODEL_PATH}" ]]; then
  echo "Model not found: ${MODEL_PATH}" >&2
  exit 1
fi

if [[ ! -f "${SIDECAR_DIR}/manifest.json" ]]; then
  echo "Full routed sidecar not found: ${SIDECAR_DIR}" >&2
  exit 1
fi

if [[ "${PRINT_CONFIG_ONLY:-0}" == "1" ]]; then
  cat <<EOF
Flash-MoE resident server config
  binary:       ${FLASHMOE_BIN}
  model:        ${MODEL_PATH}
  sidecar:      ${SIDECAR_DIR}
  slot bank:    ${MOE_SLOT_BANK}
  gpu layers:   ${N_GPU_LAYERS}
  threads:      ${THREADS}
  prompt thr:   ${THREADS_BATCH}
  context:      ${CONTEXT}
  batch:        ${BATCH}
  micro-batch:  ${UBATCH}
  prompt cache: ${CACHE_PROMPT}
  host:         ${HOST}
  port:         ${PORT}
  parallel:     ${PARALLEL}
EOF
  exit 0
fi

(
  cd "${SCRIPT_DIR}"
  uv run flashmoe_probe.py --sidecar "${SIDECAR_DIR}" --output "${PROBE_JSON}" >/dev/null
)

PRECHECK="$(
  PROBE_JSON="${PROBE_JSON}" python3 - <<'PY'
import json
import os
from pathlib import Path

probe = json.loads(Path(os.environ["PROBE_JSON"]).read_text(encoding="utf-8"))
sidecar = probe.get("sidecar_state") or {}
if sidecar.get("slot_bank_testable"):
    print("ok")
else:
    missing = sidecar.get("missing_count")
    first = sidecar.get("first_missing") or []
    detail = ", ".join(first[:3]) if first else "unknown"
    print(f"missing:{missing}:{detail}")
PY
)"

if [[ "${PRECHECK}" != "ok" ]]; then
  IFS=":" read -r _ MISSING DETAIL <<<"${PRECHECK}"
  echo "Flash-MoE preflight failed." >&2
  echo "The full routed sidecar is incomplete for slot-bank mode." >&2
  echo "Missing routed tensors: ${MISSING}" >&2
  echo "First missing: ${DETAIL}" >&2
  echo "Probe: ${PROBE_JSON}" >&2
  exit 1
fi

export DYLD_LIBRARY_PATH="${FLASHMOE_ROOT}/build-smoke/bin${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"

if [[ "${CACHE_PROMPT}" == "true" ]]; then
  CACHE_PROMPT_FLAG="--cache-prompt"
else
  CACHE_PROMPT_FLAG="--no-cache-prompt"
fi

exec "${FLASHMOE_BIN}" \
  --log-colors off \
  -m "${MODEL_PATH}" \
  --moe-mode slot-bank \
  --moe-sidecar "${SIDECAR_DIR}" \
  --moe-slot-bank "${MOE_SLOT_BANK}" \
  --threads "${THREADS}" \
  --threads-batch "${THREADS_BATCH}" \
  --parallel "${PARALLEL}" \
  "${CACHE_PROMPT_FLAG}" \
  -fit on \
  -ub "${UBATCH}" -b "${BATCH}" -ngl "${N_GPU_LAYERS}" -c "${CONTEXT}" \
  --seed "${SEED}" --temp "${TEMP}" --top-k "${TOP_K}" --top-p "${TOP_P}" \
  --host "${HOST}" --port "${PORT}" --no-webui
