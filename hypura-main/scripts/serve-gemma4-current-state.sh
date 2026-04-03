#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
AUTORESEARCH_DIR="${AUTORESEARCH_DIR:-${REPO_DIR}/../autoresearch}"
CURRENT_STATE_REPEAT="${CURRENT_STATE_REPEAT:-}"
PRINT_CONFIG_ONLY="${PRINT_CONFIG_ONLY:-0}"
REFRESH_CURRENT_STATE="${REFRESH_CURRENT_STATE:-auto}"
CURRENT_STATE_RECORD="${AUTORESEARCH_CURRENT_STATE_RECORD:-${AUTORESEARCH_DIR}/results/current_state_best.json}"

if [[ ! -d "${AUTORESEARCH_DIR}" ]]; then
  echo "autoresearch repo not found: ${AUTORESEARCH_DIR}" >&2
  exit 1
fi

REFRESH_ARGS=()
if (($# > 0)); then
  REFRESH_ARGS=("$@")
fi
HAS_REPEAT=0
for arg in "$@"; do
  if [[ "${arg}" == "--repeat" || "${arg}" == --repeat=* ]]; then
    HAS_REPEAT=1
    break
  fi
done

if [[ -n "${CURRENT_STATE_REPEAT}" && ${HAS_REPEAT} -eq 0 ]]; then
  if ((${#REFRESH_ARGS[@]} > 0)); then
    REFRESH_ARGS=(--repeat "${CURRENT_STATE_REPEAT}" "${REFRESH_ARGS[@]}")
  else
    REFRESH_ARGS=(--repeat "${CURRENT_STATE_REPEAT}")
  fi
  HAS_REPEAT=1
fi

if [[ ${HAS_REPEAT} -eq 0 ]]; then
  if ((${#REFRESH_ARGS[@]} > 0)); then
    REFRESH_ARGS=(--repeat 1 "${REFRESH_ARGS[@]}")
  else
    REFRESH_ARGS=(--repeat 1)
  fi
fi

SHOULD_REFRESH=1
case "${REFRESH_CURRENT_STATE}" in
  1|true|TRUE|yes|YES)
    SHOULD_REFRESH=1
    ;;
  0|false|FALSE|no|NO)
    SHOULD_REFRESH=0
    ;;
  auto|AUTO|"")
    if [[ "${PRINT_CONFIG_ONLY}" == "1" && -f "${CURRENT_STATE_RECORD}" ]]; then
      SHOULD_REFRESH=0
    fi
    ;;
  *)
    echo "Invalid REFRESH_CURRENT_STATE value: ${REFRESH_CURRENT_STATE}" >&2
    echo "Use 1/0/auto." >&2
    exit 1
    ;;
esac

if [[ ${SHOULD_REFRESH} -eq 1 ]]; then
  echo "Refreshing current-state recommendation..." >&2
  (
    cd "${AUTORESEARCH_DIR}"
    uv run refresh_current_state.py "${REFRESH_ARGS[@]}"
  )
else
  echo "Using existing current-state recommendation without refresh." >&2
fi

echo "Launching Gemma with current-state preference..." >&2
AUTORESEARCH_USE_CURRENT_STATE=1 exec "${SCRIPT_DIR}/serve-gemma4-m4pro.sh"
