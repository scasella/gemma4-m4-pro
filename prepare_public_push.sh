#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

clean_failure_rehearsals=0
saw_active_rehearsals=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean-failure-rehearsals)
      clean_failure_rehearsals=1
      shift
      ;;
    *)
      echo "Usage: ./prepare_public_push.sh [--clean-failure-rehearsals]" >&2
      exit 1
      ;;
  esac
done

if [[ "$clean_failure_rehearsals" -eq 1 && -f rehearsal_temp_status.sh ]]; then
  echo "Cleaning failure-kept publish rehearsal temp copies..."
  ./rehearsal_temp_status.sh --clean-failures
  echo
fi

if [[ -f rehearsal_temp_status.sh ]]; then
  temp_status_json="$(./rehearsal_temp_status.sh --json)"
  while IFS=$'\t' read -r key value; do
    case "$key" in
      active_count) temp_active_count="$value" ;;
      active_reason_summary) temp_active_reason_summary="$value" ;;
    esac
  done < <(python3 - "$temp_status_json" <<'PY'
import json
import sys
data = json.loads(sys.argv[1])
active_reason_counts = data.get('active_reason_counts', {})
active_reason_summary = ', '.join(f"{key}={active_reason_counts[key]}" for key in sorted(active_reason_counts))
print(f"active_count\t{data.get('active_count', 0)}")
print(f"active_reason_summary\t{active_reason_summary}")
PY
  )
  if [[ "${temp_active_count:-0}" != "0" ]]; then
    saw_active_rehearsals=1
    echo "Active publish rehearsals are currently running outside the repo."
    echo "  count: ${temp_active_count}"
    if [[ -n "${temp_active_reason_summary:-}" ]]; then
      echo "  states: ${temp_active_reason_summary}"
    fi
    echo "  inspect: ./rehearsal_temp_status.sh"
    echo "  note: rerun ./prepare_public_push.sh after active rehearsals finish if you want the quietest final publish check"
    echo
  fi
fi

echo "Running release preflight..."
(cd autoresearch && python3 release_readiness_check.py)

echo "Removing local-only verification artifacts..."
rm -rf autoresearch/.venv autoresearch/__pycache__ hypura-main/target
rm -f autoresearch/results/auto_server_state.json
rm -rf autoresearch/results/chat_sessions
find autoresearch -type d -name '__pycache__' -prune -exec rm -rf {} +
find autoresearch -type f -name '*.pyc' -delete

echo "Running lean repo audit..."
python3 lean_repo_audit.py

if [[ -f rehearsal_temp_status.sh ]]; then
  temp_status_json="$(./rehearsal_temp_status.sh --json)"
  while IFS=$'\t' read -r key value; do
    case "$key" in
      count) temp_copy_count="$value" ;;
      total_size_mb) temp_total_size_mb="$value" ;;
      reason_summary) temp_reason_summary="$value" ;;
      active_count) temp_active_count="$value" ;;
      active_reason_summary) temp_active_reason_summary="$value" ;;
    esac
  done < <(python3 - "$temp_status_json" <<'PY'
import json
import sys
data = json.loads(sys.argv[1])
reason_counts = data.get('reason_counts', {})
reason_summary = ', '.join(f"{key}={reason_counts[key]}" for key in sorted(reason_counts))
active_reason_counts = data.get('active_reason_counts', {})
active_reason_summary = ', '.join(f"{key}={active_reason_counts[key]}" for key in sorted(active_reason_counts))
print(f"count\t{data.get('count', 0)}")
print(f"total_size_mb\t{data.get('total_size_mb', 0)}")
print(f"reason_summary\t{reason_summary}")
print(f"active_count\t{data.get('active_count', 0)}")
print(f"active_reason_summary\t{active_reason_summary}")
PY
  )
  if [[ "${temp_active_count:-0}" != "0" ]]; then
    echo
    if [[ "$saw_active_rehearsals" -eq 1 ]]; then
      echo "Active publish rehearsals are still running outside the repo."
    else
      echo "Active publish rehearsals are currently running outside the repo."
    fi
    echo "  count: ${temp_active_count}"
    if [[ -n "${temp_active_reason_summary:-}" ]]; then
      echo "  states: ${temp_active_reason_summary}"
    fi
    echo "  inspect: ./rehearsal_temp_status.sh"
    echo "  note: rerun ./prepare_public_push.sh after active rehearsals finish if you want the quietest final publish check"
  fi
  if [[ "${temp_copy_count:-0}" != "0" ]]; then
    echo
    echo "Saved publish rehearsal temp copies are still present outside the repo."
    echo "  count: ${temp_copy_count}"
    echo "  total size: ${temp_total_size_mb} MB"
    if [[ -n "${temp_reason_summary:-}" ]]; then
      echo "  reasons: ${temp_reason_summary}"
    fi
    echo "  inspect: ./rehearsal_temp_status.sh"
    echo "  clean failures only: ./rehearsal_temp_status.sh --clean-failures"
    echo "  clean all: ./rehearsal_temp_status.sh --clean"
  fi
fi

if [[ ! -f LICENSE ]]; then
  echo
  echo "No root LICENSE file found yet."
  echo "Choose one before pushing: see LICENSE_OPTIONS.md"
fi

echo
echo "Public push prep complete."
