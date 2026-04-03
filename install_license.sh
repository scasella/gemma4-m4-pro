#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ $# -lt 1 ]]; then
  echo "Usage: ./install_license.sh <mit|apache-2.0> [--holder \"Your Name\"] [--year 2026] [--force]" >&2
  exit 1
fi

kind="$1"
shift
holder="Your Name"
year="$(date +%Y)"
force=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --holder)
      holder="$2"
      shift 2
      ;;
    --year)
      year="$2"
      shift 2
      ;;
    --force)
      force=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

case "$kind" in
  mit)
    template="LICENSE-MIT.template"
    ;;
  apache|apache-2.0)
    template="LICENSE-APACHE-2.0.template"
    ;;
  *)
    echo "Unsupported license kind: $kind" >&2
    exit 1
    ;;
esac

if [[ -f LICENSE && "$force" -ne 1 ]]; then
  echo "LICENSE already exists. Re-run with --force to overwrite it." >&2
  exit 1
fi

python3 - <<'PY' "$template" "$holder" "$year"
from pathlib import Path
import sys
template, holder, year = sys.argv[1:4]
text = Path(template).read_text(encoding='utf-8')
text = text.replace('{{HOLDER}}', holder).replace('{{YEAR}}', year)
Path('LICENSE').write_text(text, encoding='utf-8')
PY

echo "Installed LICENSE from $template for $holder ($year)."
