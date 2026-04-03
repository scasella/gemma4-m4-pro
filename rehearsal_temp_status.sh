#!/usr/bin/env bash
set -euo pipefail

json_mode=0
clean_mode="none"

usage() {
  echo "Usage: ./rehearsal_temp_status.sh [--json] [--clean] [--clean-failures]" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --json)
      json_mode=1
      shift
      ;;
    --clean)
      if [[ "$clean_mode" != "none" ]]; then
        usage
        exit 1
      fi
      clean_mode="all"
      shift
      ;;
    --clean-failures)
      if [[ "$clean_mode" != "none" ]]; then
        usage
        exit 1
      fi
      clean_mode="failures"
      shift
      ;;
    *)
      usage
      exit 1
      ;;
  esac
done

summary_tmp="$(mktemp)"
cleanup() {
  if [[ -f "$summary_tmp" ]]; then
    rm -f "$summary_tmp"
  fi
}
trap cleanup EXIT

python3 - "$summary_tmp" "$clean_mode" <<'PY'
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

summary_path = Path(sys.argv[1])
clean_mode = sys.argv[2]
current_repo = Path.cwd().resolve()
paths = [
    path for path in Path('/tmp').glob('gemma-publish-rehearsal.*')
    if path.is_dir() and path.resolve() != current_repo
]
entries = []
for path in paths:
    size_bytes = sum(file.stat().st_size for file in path.rglob('*') if file.is_file())
    stat = path.stat()
    meta_path = Path(f"{path}.meta.json")
    metadata = None
    if meta_path.exists():
        try:
            metadata = json.loads(meta_path.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            metadata = {'error': 'invalid_metadata'}
    keep_reason = (metadata or {}).get('keep_reason', 'unknown')
    final_state = (metadata or {}).get('final_state', 'unknown')
    entries.append({
        'path': str(path),
        'meta_path': str(meta_path),
        'name': path.name,
        'size_bytes': size_bytes,
        'size_mb': round(size_bytes / (1024 * 1024), 2),
        'modified': datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat().replace('+00:00', 'Z'),
        'modified_epoch': stat.st_mtime,
        'keep_reason': keep_reason,
        'final_state': final_state,
        'metadata': metadata,
    })
entries.sort(key=lambda entry: entry['modified_epoch'], reverse=True)

def is_active(entry: dict[str, object]) -> bool:
    reason = entry.get('keep_reason')
    final_state = entry.get('final_state')
    return reason == 'in_progress' or final_state == 'running'

active_entries = [entry for entry in entries if is_active(entry)]
entries = [entry for entry in entries if not is_active(entry)]

removed_count = 0
removed_total_size_bytes = 0
removed_reason_counts: dict[str, int] = {}
def should_remove(entry: dict[str, object]) -> bool:
    if clean_mode == 'all':
        return True
    if clean_mode == 'failures':
        return entry.get('keep_reason') == 'failure'
    return False

if clean_mode != 'none':
    kept_entries = []
    for entry in entries:
        if not should_remove(entry):
            kept_entries.append(entry)
            continue
        shutil.rmtree(entry['path'])
        meta_path = Path(entry['meta_path'])
        if meta_path.exists():
            meta_path.unlink()
        removed_count += 1
        removed_total_size_bytes += entry['size_bytes']
        reason = entry.get('keep_reason', 'unknown')
        removed_reason_counts[reason] = removed_reason_counts.get(reason, 0) + 1
    entries = kept_entries

remaining_reason_counts: dict[str, int] = {}
for entry in entries:
    reason = entry.get('keep_reason', 'unknown')
    remaining_reason_counts[reason] = remaining_reason_counts.get(reason, 0) + 1

active_reason_counts: dict[str, int] = {}
for entry in active_entries:
    reason = entry.get('keep_reason', 'unknown')
    active_reason_counts[reason] = active_reason_counts.get(reason, 0) + 1

summary = {
    'clean_mode': clean_mode,
    'cleaned': clean_mode != 'none',
    'removed_count': removed_count,
    'removed_total_size_mb': round(removed_total_size_bytes / (1024 * 1024), 2),
    'removed_reason_counts': removed_reason_counts,
    'count': len(entries),
    'total_size_mb': round(sum(entry['size_bytes'] for entry in entries) / (1024 * 1024), 2),
    'reason_counts': remaining_reason_counts,
    'active_count': len(active_entries),
    'active_total_size_mb': round(sum(entry['size_bytes'] for entry in active_entries) / (1024 * 1024), 2),
    'active_reason_counts': active_reason_counts,
    'entries': [
        {
            'path': entry['path'],
            'name': entry['name'],
            'size_mb': entry['size_mb'],
            'modified': entry['modified'],
            'metadata': entry['metadata'],
        }
        for entry in entries
    ],
    'active_entries': [
        {
            'path': entry['path'],
            'name': entry['name'],
            'size_mb': entry['size_mb'],
            'modified': entry['modified'],
            'metadata': entry['metadata'],
        }
        for entry in active_entries
    ],
}
summary_path.write_text(json.dumps(summary, indent=2), encoding='utf-8')
PY

if [[ "$json_mode" -eq 1 ]]; then
  cat "$summary_tmp"
else
  python3 - "$summary_tmp" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding='utf-8'))
entries = data.get('entries', [])
active_entries = data.get('active_entries', [])
if data.get('cleaned'):
    removed_count = data.get('removed_count', 0)
    removed_total_size_mb = data.get('removed_total_size_mb', 0)
    clean_mode = data.get('clean_mode', 'none')
    if removed_count:
        if clean_mode == 'failures':
            print(f"Removed {removed_count} failure-kept publish rehearsal temp copies ({removed_total_size_mb:.2f} MB).")
        else:
            print(f"Removed {removed_count} saved publish rehearsal temp copies ({removed_total_size_mb:.2f} MB).")
    else:
        if clean_mode == 'failures':
            print('No failure-kept publish rehearsal temp copies found.')
        else:
            print('No saved publish rehearsal temp copies found.')
elif not entries:
    print('No saved publish rehearsal temp copies found.')
else:
    print('Saved publish rehearsal temp copies')
    print()
    print(f"count: {data.get('count', 0)}")
    print(f"total size: {data.get('total_size_mb', 0):.2f} MB")
    reason_counts = data.get('reason_counts', {})
    if reason_counts:
        parts = [f"{key}={reason_counts[key]}" for key in sorted(reason_counts)]
        print(f"reasons: {', '.join(parts)}")
    for entry in entries:
        metadata = entry.get('metadata') or {}
        keep_reason = metadata.get('keep_reason', 'unknown')
        final_state = metadata.get('final_state', 'unknown')
        print(f"- {entry['path']} | {entry['size_mb']:.2f} MB | state={final_state} | reason={keep_reason} | {entry['modified']}")
if active_entries:
    if entries:
        print()
    print('Active publish rehearsals')
    print()
    print(f"count: {data.get('active_count', 0)}")
    print(f"total size: {data.get('active_total_size_mb', 0):.2f} MB")
    active_reason_counts = data.get('active_reason_counts', {})
    if active_reason_counts:
        parts = [f"{key}={active_reason_counts[key]}" for key in sorted(active_reason_counts)]
        print(f"states: {', '.join(parts)}")
    for entry in active_entries:
        metadata = entry.get('metadata') or {}
        keep_reason = metadata.get('keep_reason', 'unknown')
        final_state = metadata.get('final_state', 'unknown')
        print(f"- {entry['path']} | {entry['size_mb']:.2f} MB | state={final_state} | reason={keep_reason} | {entry['modified']}")
PY
fi
