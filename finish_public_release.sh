#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

repo_slug=""
workflow_file="release-readiness.yml"
dry_run=0

usage() {
  echo "Usage: ./finish_public_release.sh [--repo owner/repo] [--workflow release-readiness.yml] [--dry-run]" >&2
}

pass_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      repo_slug="$2"
      pass_args+=("$1" "$2")
      shift 2
      ;;
    --workflow)
      workflow_file="$2"
      pass_args+=("$1" "$2")
      shift 2
      ;;
    --dry-run)
      dry_run=1
      pass_args+=("$1")
      shift
      ;;
    *)
      usage
      exit 1
      ;;
  esac
done

run_install_badge() {
  if [[ ${#pass_args[@]} -gt 0 ]]; then
    ./install_ci_badge.sh "${pass_args[@]}"
  else
    ./install_ci_badge.sh
  fi
}

readme_hash() {
  shasum -a 256 README.md | awk '{print $1}'
}

if [[ "$dry_run" -eq 1 ]]; then
  echo "Post-push polish preview"
  echo
  echo "This command will:"
  echo "  1. install or refresh the CI badge in README.md"
  echo "  2. run ./publish_status.sh to confirm the repo is fully polished"
  echo "  3. remind you to commit and push README.md if it changed"
  echo
  run_install_badge
  exit 0
fi

before_hash="$(readme_hash)"
echo "Installing or refreshing the CI badge..."
run_install_badge

after_hash="$(readme_hash)"
readme_changed=0
if [[ "$before_hash" != "$after_hash" ]]; then
  readme_changed=1
  echo "README.md changed."
else
  echo "README.md was already up to date."
fi

echo
echo "Publish status after post-push polish:"
status_json="$(./publish_status.sh --json)"
python3 - "$status_json" <<'PY'
import json
import sys
data = json.loads(sys.argv[1])
checks = data.get('checks', {})
if checks.get('readme_ci_badge_matches_expected'):
    print('README badge matches the configured GitHub repo.')
else:
    print('README badge still needs attention.')
PY
python3 - "$status_json" <<'PY'
import json
import sys
data = json.loads(sys.argv[1])
checks = data.get('checks', {})
ready = data.get('ready', False)
badge_ok = checks.get('readme_ci_badge_matches_expected') or (not checks.get('origin_configured') and checks.get('readme_ci_badge_present'))
if not ready or not badge_ok:
    raise SystemExit(1)
PY

echo
if [[ "$readme_changed" -eq 1 ]]; then
  echo "Next commands:"
  echo "  git add README.md"
  echo "  git commit -m \"Add CI badge\""
  echo "  git push"
else
  echo "No README commit is needed."
fi
