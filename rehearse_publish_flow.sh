#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

license_kind="mit"
holder="Example Holder"
remote_url="https://github.com/example-owner/example-repo.git"
keep_temp=0

usage() {
  echo "Usage: ./rehearse_publish_flow.sh [--license mit|apache-2.0] [--holder \"Example Holder\"] [--remote https://github.com/example-owner/example-repo.git] [--keep-temp]" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --license)
      license_kind="$2"
      shift 2
      ;;
    --holder)
      holder="$2"
      shift 2
      ;;
    --remote)
      remote_url="$2"
      shift 2
      ;;
    --keep-temp)
      keep_temp=1
      shift
      ;;
    *)
      usage
      exit 1
      ;;
  esac
done

tmpdir="$(mktemp -d /tmp/gemma-publish-rehearsal.XXXXXX)"
created_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
meta_path="${tmpdir}.meta.json"
write_metadata() {
  local final_state="$1"
  local keep_reason="$2"
  local exit_status="$3"
  local updated_at
  updated_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  python3 - "$meta_path" "$tmpdir" "$created_at" "$updated_at" "$license_kind" "$holder" "$remote_url" "$keep_temp" "$final_state" "$keep_reason" "$exit_status" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

meta_path = Path(sys.argv[1])
payload = {
    'tmpdir': sys.argv[2],
    'created_at': sys.argv[3],
    'updated_at': sys.argv[4],
    'license_kind': sys.argv[5],
    'holder': sys.argv[6],
    'remote_url': sys.argv[7],
    'keep_requested': sys.argv[8] == '1',
    'final_state': sys.argv[9],
    'keep_reason': sys.argv[10],
    'exit_status': int(sys.argv[11]),
}
meta_path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
PY
}
write_metadata "running" "in_progress" 0
cleanup() {
  local status=$?
  if [[ ! -d "$tmpdir" ]]; then
    return
  fi
  if [[ "$keep_temp" -eq 1 ]]; then
    write_metadata "kept" "explicit_keep" "$status"
    echo "Kept rehearsal copy at: $tmpdir"
    return
  fi
  if [[ "$status" -ne 0 ]]; then
    write_metadata "kept" "failure" "$status"
    echo "Rehearsal failed. Temp copy kept at: $tmpdir" >&2
    return
  fi
  write_metadata "cleaned" "success_cleanup" "$status"
  rm -f "$meta_path"
  rm -rf "$tmpdir"
  echo "Cleaned up rehearsal copy."
}
trap cleanup EXIT

echo "Rehearsal temp copy: $tmpdir"
find "$ROOT_DIR" -mindepth 1 -maxdepth 1 ! -name '.git' -exec cp -R {} "$tmpdir"/ \;

echo "Removing local-only artifacts from the rehearsal copy..."
rm -rf "$tmpdir/autoresearch/.venv" "$tmpdir/autoresearch/__pycache__" "$tmpdir/autoresearch/results/chat_sessions" "$tmpdir/autoresearch/results/auto_server_state.json" "$tmpdir/hypura-main/target"
find "$tmpdir" -name '.DS_Store' -delete

cd "$tmpdir"

echo "Running publish setup rehearsal..."
SKIP_STREAMING_REGRESSION_SMOKE=1 ./make_publish_ready.sh --license "$license_kind" --holder "$holder" --remote "$remote_url" --force-license

echo
echo "Running post-push polish rehearsal..."
./finish_public_release.sh

status_json="$(./publish_status.sh --json)"
python3 - "$status_json" <<'PY'
import json
import sys
data = json.loads(sys.argv[1])
checks = data.get('checks', {})
if not data.get('ready'):
    raise SystemExit('publish status is not ready after rehearsal')
if not checks.get('readme_ci_badge_matches_expected'):
    raise SystemExit('README badge does not match expected repo after rehearsal')
PY

echo "Publish rehearsal passed."
