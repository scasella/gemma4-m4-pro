#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

FLASHMOE_ROOT="${FLASHMOE_ROOT:-${ROOT_DIR}/anemll-flash-llama.cpp-gemma4}"
FLASHMOE_BIN="${FLASHMOE_BIN:-${FLASHMOE_ROOT}/build-smoke/bin/llama-cli}"
MODEL_PATH="${MODEL_PATH:-${ROOT_DIR}/models/gemma-4-26B-A4B-it-Q4_K_M.gguf}"
SIDECAR_DIR="${SIDECAR_DIR:-${ROOT_DIR}/autoresearch/results/flashmoe_smoke/layer_000}"
PROMPT="${PROMPT:-Answer with one digit only: what is 2+2?}"
CONTEXT="${CONTEXT:-512}"
TOKENS="${TOKENS:-8}"
N_GPU_LAYERS="${N_GPU_LAYERS:-0}"
BATCH="${BATCH:-1}"
UBATCH="${UBATCH:-1}"

if [[ ! -x "${FLASHMOE_BIN}" ]]; then
  echo "Flash-MoE binary not found: ${FLASHMOE_BIN}" >&2
  echo "Build it with:" >&2
  echo "  cmake -S ${FLASHMOE_ROOT} -B ${FLASHMOE_ROOT}/build-smoke -DCMAKE_BUILD_TYPE=Release -DGGML_METAL=ON -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_SERVER=ON -DLLAMA_FLASH_MOE_GPU_BANK=ON" >&2
  echo "  cmake --build ${FLASHMOE_ROOT}/build-smoke --target llama-cli -j8" >&2
  exit 1
fi

if [[ ! -f "${MODEL_PATH}" ]]; then
  echo "Model not found: ${MODEL_PATH}" >&2
  exit 1
fi

if [[ ! -f "${SIDECAR_DIR}/manifest.json" ]]; then
  echo "Smoke sidecar not found: ${SIDECAR_DIR}" >&2
  echo "Create it with:" >&2
  echo "  cd ${ROOT_DIR}/autoresearch && uv run flashmoe_probe.py --smoke-layer 0" >&2
  exit 1
fi

exec "${FLASHMOE_BIN}" \
  --color off \
  --simple-io \
  -m "${MODEL_PATH}" \
  --moe-mode resident-bank \
  --moe-sidecar "${SIDECAR_DIR}" \
  -cnv -st -fit on \
  -ub "${UBATCH}" -b "${BATCH}" -ngl "${N_GPU_LAYERS}" -c "${CONTEXT}" \
  --seed 0 --temp 0 \
  -p "${PROMPT}" \
  -n "${TOKENS}"
