#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export MODE="${MODE:-auto}"

exec "${SCRIPT_DIR}/autoresearch/gemma4_answer.sh" "$@"
