#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

repo_slug=""
workflow_file="release-readiness.yml"
dry_run=0

usage() {
  echo "Usage: ./install_ci_badge.sh [--repo owner/repo] [--workflow release-readiness.yml] [--dry-run]" >&2
}

infer_repo_slug() {
  local remote
  remote="$(git remote get-url origin 2>/dev/null || true)"
  if [[ -z "$remote" ]]; then
    return 1
  fi
  remote="${remote%.git}"
  if [[ "$remote" =~ ^https://github.com/([^/]+/[^/]+)$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  if [[ "$remote" =~ ^git@github.com:([^/]+/[^/]+)$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  if [[ "$remote" =~ ^ssh://git@github.com/([^/]+/[^/]+)$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      repo_slug="$2"
      shift 2
      ;;
    --workflow)
      workflow_file="$2"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    *)
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$repo_slug" ]]; then
  if ! repo_slug="$(infer_repo_slug)"; then
    echo "Could not infer GitHub repo from origin. Use --repo owner/repo." >&2
    exit 1
  fi
fi

badge_markdown="[![Release Readiness](https://github.com/${repo_slug}/actions/workflows/${workflow_file}/badge.svg)](https://github.com/${repo_slug}/actions/workflows/${workflow_file})"
readme_path="README.md"
if [[ ! -f "$readme_path" ]]; then
  echo "README.md not found." >&2
  exit 1
fi

python3 - "$readme_path" "$badge_markdown" "$dry_run" <<'PY'
from pathlib import Path
import sys

readme_path = Path(sys.argv[1])
badge = sys.argv[2]
dry_run = sys.argv[3] == '1'
text = readme_path.read_text(encoding='utf-8')
lines = text.splitlines()
workflow_marker = '/actions/workflows/release-readiness.yml'
badge_index = next((i for i, line in enumerate(lines) if workflow_marker in line and 'badge.svg' in line), None)
heading_index = next((i for i, line in enumerate(lines) if line.startswith('# ')), None)
if heading_index is None:
    raise SystemExit('README.md does not start with a markdown heading.')
updated = lines[:]
if badge_index is not None:
    updated[badge_index] = badge
else:
    updated = updated[:heading_index + 1] + ['', badge, ''] + updated[heading_index + 1:]
    while len(updated) > 2 and updated[1] == '' and updated[2] == '':
        del updated[1]
result = '\n'.join(updated).rstrip() + '\n'
if dry_run:
    print('Badge preview:')
    print(badge)
else:
    readme_path.write_text(result, encoding='utf-8')
    print(f'Updated {readme_path} with the CI badge.')
PY
