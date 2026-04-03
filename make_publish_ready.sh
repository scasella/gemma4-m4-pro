#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

license_kind=""
holder="Your Name"
year="$(date +%Y)"
remote_url=""
branch="main"
force_license=0
dry_run=0

is_placeholder_remote() {
  local value="$1"
  [[ "$value" == "https://github.com/you/repo.git" ]] || \
  [[ "$value" == "git@github.com:you/repo.git" ]] || \
  [[ "$value" == *"github.com/you/"* ]] || \
  [[ "$value" == *"example.com/"* ]] || \
  [[ "$value" == *"example/"* ]]
}

show_plan() {
  echo "Publish setup preview"
  echo
  echo "license: $license_kind"
  echo "holder: $holder"
  echo "year: $year"
  echo "branch: $branch"
  echo "remote: $remote_url"
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "git init: no (already initialized)"
  else
    echo "git init: yes"
  fi
  if git remote get-url origin >/dev/null 2>&1; then
    echo "origin action: update existing origin"
  else
    echo "origin action: add new origin"
  fi
  if [[ "$force_license" -eq 1 ]]; then
    echo "license overwrite: forced"
  else
    echo "license overwrite: only if missing"
  fi
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
    --year)
      year="$2"
      shift 2
      ;;
    --remote)
      remote_url="$2"
      shift 2
      ;;
    --branch)
      branch="$2"
      shift 2
      ;;
    --force-license)
      force_license=1
      shift
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: ./make_publish_ready.sh --license <mit|apache-2.0> --holder \"Your Name\" --remote <url> [--year 2026] [--branch main] [--force-license] [--dry-run]" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$license_kind" || -z "$remote_url" ]]; then
  echo "Both --license and --remote are required." >&2
  echo "Example: ./make_publish_ready.sh --license mit --holder \"Your Name\" --remote https://github.com/you/repo.git" >&2
  exit 1
fi

if [[ "$dry_run" -eq 1 ]]; then
  show_plan
  echo
  if [[ "$holder" == "Your Name" ]]; then
    echo "warning: holder is still the placeholder value"
  fi
  if is_placeholder_remote "$remote_url"; then
    echo "warning: remote still looks like a placeholder"
  fi
  exit 0
fi

if [[ "$holder" == "Your Name" ]]; then
  echo "Holder is still the placeholder value 'Your Name'. Use --holder with the real copyright holder." >&2
  exit 1
fi

if is_placeholder_remote "$remote_url"; then
  echo "Remote still looks like a placeholder. Use --remote with the real GitHub repository URL, or preview with --dry-run." >&2
  exit 1
fi

license_args=("$license_kind" --holder "$holder" --year "$year")
if [[ "$force_license" -eq 1 ]]; then
  license_args+=(--force)
fi

echo "Installing license..."
./install_license.sh "${license_args[@]}"

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Git repository already initialized."
else
  echo "Initializing git repository..."
  git init >/dev/null
fi

current_branch="$(git branch --show-current 2>/dev/null || true)"
if [[ "$current_branch" != "$branch" ]]; then
  git checkout -B "$branch" >/dev/null
fi

if git remote get-url origin >/dev/null 2>&1; then
  existing_remote="$(git remote get-url origin)"
  if [[ "$existing_remote" != "$remote_url" ]]; then
    echo "Updating origin remote..."
    git remote set-url origin "$remote_url"
  fi
else
  echo "Adding origin remote..."
  git remote add origin "$remote_url"
fi

echo "Running final publish prep..."
./prepare_public_push.sh

echo
echo "Final publish status:"
./publish_status.sh

echo
echo "Next commands:"
echo "  git add ."
echo "  git commit -m \"Initial public release\""
echo "  git push -u origin $branch"
echo "  ./finish_public_release.sh"
