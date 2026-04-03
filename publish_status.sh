#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

json_mode=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --json)
      json_mode=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: ./publish_status.sh [--json]" >&2
      exit 1
      ;;
  esac
done

ready=1

license_present=0
ci_workflow_present=0
prepare_helper_present=0
install_helper_present=0
install_badge_helper_present=0
rehearsal_temp_helper_present=0
rehearsal_temp_copies_present=0
rehearsal_temp_copies_count="0"
rehearsal_temp_total_size_mb="0"
rehearsal_temp_reason_summary=""
rehearsal_temp_reason_counts_json="{}"
rehearsal_temp_failure_copies_count="0"
rehearsal_temp_active_count="0"
rehearsal_temp_active_reason_summary=""
rehearsal_temp_active_reason_counts_json="{}"
suggested_prepare_reason="clean_repo_state"
readme_badge_present=0
readme_badge_matches_expected=0
lean_audit_helper_present=0
lean_audit_passed=0
lean_audit_tmp=""
rehearsal_temp_tmp=""
lean_audit_repo_size_mb=""
lean_audit_size_budget_mb=""
lean_audit_manifest_summary_files_count="0"
lean_audit_manifest_run_artifacts_count="0"
lean_audit_manifest_present=0
lean_audit_run_set_matches=0
lean_audit_no_private_path_leaks=0
lean_audit_no_disallowed_paths=0
holder_hint=""
remote_hint=""
suggested_command=""
suggested_preview_command=""
badge_repo_slug=""
expected_badge_markdown=""
current_badge_line=""
current_branch=""
current_upstream=""
branch_has_upstream=0
branch_ahead_count="0"
branch_behind_count="0"
release_stage="setup_incomplete"
release_stage_reason="blocking_items_present"
release_stage_label="setup incomplete"
suggested_push_command=""
suggested_push_reason=""
suggested_post_publish_action_command=""
suggested_post_publish_action_reason=""
git_initialized=0
origin_configured=0

cleanup() {
  if [[ -n "${lean_audit_tmp:-}" && -f "$lean_audit_tmp" ]]; then
    rm -f "$lean_audit_tmp"
  fi
  if [[ -n "${rehearsal_temp_tmp:-}" && -f "$rehearsal_temp_tmp" ]]; then
    rm -f "$rehearsal_temp_tmp"
  fi
}
trap cleanup EXIT

status_pass() {
  if [[ "$json_mode" -ne 1 ]]; then
    echo "[pass] $1"
  fi
}

status_todo() {
  if [[ "$json_mode" -ne 1 ]]; then
    echo "[todo] $1"
  fi
  ready=0
}

status_warn() {
  if [[ "$json_mode" -ne 1 ]]; then
    echo "[warn] $1"
  fi
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

holder_hint="$(git config user.name 2>/dev/null || git config --global user.name 2>/dev/null || true)"
if [[ -z "$holder_hint" ]]; then
  holder_hint="Your Name"
fi
remote_hint="$(git remote get-url origin 2>/dev/null || true)"
if [[ -z "$remote_hint" ]]; then
  remote_hint="https://github.com/you/repo.git"
fi
holder_hint_escaped="${holder_hint//\"/\\\"}"
remote_hint_escaped="${remote_hint//\"/\\\"}"
suggested_command="./make_publish_ready.sh --license <mit|apache-2.0> --holder \"${holder_hint_escaped}\" --remote \"${remote_hint_escaped}\""

suggested_preview_command="$suggested_command --dry-run"

suggested_lean_audit_command="python3 lean_repo_audit.py"
suggested_lean_audit_json_command="python3 lean_repo_audit.py --json"

suggested_prepare_failure_cleanup_command="./prepare_public_push.sh --clean-failure-rehearsals"
suggested_prepare_command="./prepare_public_push.sh"

suggested_next_action_command="$suggested_preview_command"
suggested_next_action_reason="publish_setup_preview"

if [[ "$json_mode" -ne 1 ]]; then
  echo "Publish status"
  echo
fi
if [[ -f LICENSE ]]; then
  license_present=1
  status_pass "root LICENSE is present"
else
  status_todo "root LICENSE is missing (use ./install_license.sh ...)"
fi

if [[ -f .github/workflows/release-readiness.yml ]]; then
  ci_workflow_present=1
  status_pass "CI workflow is present"
else
  status_todo "CI workflow is missing"
fi

if [[ -f prepare_public_push.sh ]]; then
  prepare_helper_present=1
  status_pass "public push prep helper is present"
else
  status_todo "public push prep helper is missing"
fi

if [[ -f install_license.sh ]]; then
  install_helper_present=1
  status_pass "license install helper is present"
else
  status_todo "license install helper is missing"
fi

if [[ -f install_ci_badge.sh ]]; then
  install_badge_helper_present=1
  status_pass "CI badge helper is present"
else
  status_todo "CI badge helper is missing"
fi

if [[ -f rehearsal_temp_status.sh ]]; then
  rehearsal_temp_helper_present=1
  rehearsal_temp_tmp="$(mktemp)"
  if ./rehearsal_temp_status.sh --json >"$rehearsal_temp_tmp"; then
    while IFS=$'\t' read -r key value; do
      case "$key" in
        count) rehearsal_temp_copies_count="$value" ;;
        total_size_mb) rehearsal_temp_total_size_mb="$value" ;;
        reason_summary) rehearsal_temp_reason_summary="$value" ;;
        reason_counts_json) rehearsal_temp_reason_counts_json="$value" ;;
        failure_count) rehearsal_temp_failure_copies_count="$value" ;;
        active_count) rehearsal_temp_active_count="$value" ;;
        active_reason_summary) rehearsal_temp_active_reason_summary="$value" ;;
        active_reason_counts_json) rehearsal_temp_active_reason_counts_json="$value" ;;
      esac
    done < <(python3 - "$rehearsal_temp_tmp" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1], encoding='utf-8'))
print(f"count\t{data.get('count', 0)}")
print(f"total_size_mb\t{data.get('total_size_mb', 0)}")
reason_counts = data.get('reason_counts', {})
reason_summary = ', '.join(f"{key}={reason_counts[key]}" for key in sorted(reason_counts))
print(f"reason_summary\t{reason_summary}")
print(f"reason_counts_json\t{json.dumps(reason_counts, separators=(',', ':'))}")
print(f"failure_count\t{reason_counts.get('failure', 0)}")
active_reason_counts = data.get('active_reason_counts', {})
active_reason_summary = ', '.join(f"{key}={active_reason_counts[key]}" for key in sorted(active_reason_counts))
print(f"active_count\t{data.get('active_count', 0)}")
print(f"active_reason_summary\t{active_reason_summary}")
print(f"active_reason_counts_json\t{json.dumps(active_reason_counts, separators=(',', ':'))}")
PY
    )
    if [[ "$rehearsal_temp_active_count" != "0" && "$json_mode" -ne 1 ]]; then
      echo "[info] active publish rehearsals are currently running; they are not counted as saved leftovers"
      echo "  active publish rehearsals: ${rehearsal_temp_active_count}"
      if [[ -n "$rehearsal_temp_active_reason_summary" ]]; then
        echo "  active rehearsal states: ${rehearsal_temp_active_reason_summary}"
      fi
    fi
    if [[ "$rehearsal_temp_active_count" != "0" ]]; then
      suggested_prepare_reason="active_rehearsals_running"
    fi
    if [[ "$rehearsal_temp_copies_count" != "0" ]]; then
      rehearsal_temp_copies_present=1
      status_warn "saved publish rehearsal temp copies are present (run ./rehearsal_temp_status.sh to see why they were kept)"
      if [[ "$rehearsal_temp_failure_copies_count" != "0" ]]; then
        suggested_prepare_command="$suggested_prepare_failure_cleanup_command"
        suggested_prepare_reason="failure_rehearsal_copies_present"
        suggested_next_action_command="$suggested_prepare_failure_cleanup_command"
        suggested_next_action_reason="failure_rehearsal_cleanup"
      fi
      if [[ "$json_mode" -ne 1 ]]; then
        echo "  saved rehearsal temp copies: ${rehearsal_temp_copies_count}"
        echo "  saved rehearsal temp size: ${rehearsal_temp_total_size_mb} MB"
        if [[ -n "$rehearsal_temp_reason_summary" ]]; then
          echo "  saved rehearsal temp reasons: ${rehearsal_temp_reason_summary}"
        fi
        echo "  inspect saved rehearsal copies: ./rehearsal_temp_status.sh"
        echo "  remove only failure-kept rehearsal copies: ./rehearsal_temp_status.sh --clean-failures"
        echo "  remove saved rehearsal copies: ./rehearsal_temp_status.sh --clean"
        if [[ "$rehearsal_temp_failure_copies_count" != "0" ]]; then
          echo "  quick one-shot cleanup + prep: $suggested_prepare_failure_cleanup_command"
        fi
      fi
    else
      status_pass "no saved publish rehearsal temp copies are present"
    fi
  else
    status_warn "could not inspect saved publish rehearsal temp copies"
  fi
else
  status_warn "rehearsal temp helper is missing"
fi

if [[ -f lean_repo_audit.py ]]; then
  lean_audit_helper_present=1
  lean_audit_tmp="$(mktemp)"
  if python3 lean_repo_audit.py --json >"$lean_audit_tmp"; then
    lean_audit_passed=1
    status_pass "lean repo audit passes"
  else
    status_todo "lean repo audit is failing"
  fi
  while IFS=$'\t' read -r key value; do
    case "$key" in
      repo_size_mb) lean_audit_repo_size_mb="$value" ;;
      size_budget_mb) lean_audit_size_budget_mb="$value" ;;
      manifest_summary_files_count) lean_audit_manifest_summary_files_count="$value" ;;
      manifest_run_artifacts_count) lean_audit_manifest_run_artifacts_count="$value" ;;
      curated_manifest_present) lean_audit_manifest_present="$value" ;;
      curated_run_set_matches) lean_audit_run_set_matches="$value" ;;
      layout_manifest_present) lean_audit_layout_manifest_present="$value" ;;
      public_layout_matches) lean_audit_public_layout_matches="$value" ;;
      layout_issue_summary) lean_audit_layout_issue_summary="$value" ;;
      no_private_path_leaks) lean_audit_no_private_path_leaks="$value" ;;
      no_disallowed_paths) lean_audit_no_disallowed_paths="$value" ;;
    esac
  done < <(python3 - "$lean_audit_tmp" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding='utf-8'))
checks = data.get('checks', {})
layout_issues = data.get('layout_issues', {})
layout_issue_summary = '; '.join(f"{key}=" + ','.join(layout_issues[key]) for key in sorted(layout_issues))
pairs = [
    ('repo_size_mb', data.get('repo_size_mb', '')),
    ('size_budget_mb', data.get('size_budget_mb', '')),
    ('manifest_summary_files_count', data.get('manifest_summary_files_count', 0)),
    ('manifest_run_artifacts_count', data.get('manifest_run_artifacts_count', 0)),
    ('curated_manifest_present', 1 if checks.get('curated_manifest_present') else 0),
    ('curated_run_set_matches', 1 if checks.get('curated_run_set_matches') else 0),
    ('layout_manifest_present', 1 if checks.get('layout_manifest_present') else 0),
    ('public_layout_matches', 1 if checks.get('public_layout_matches') else 0),
    ('layout_issue_summary', layout_issue_summary),
    ('no_private_path_leaks', 1 if checks.get('no_private_path_leaks') else 0),
    ('no_disallowed_paths', 1 if checks.get('no_disallowed_paths') else 0),
]
for key, value in pairs:
    print(f'{key}\t{value}')
PY
  )
  if [[ "$lean_audit_passed" -eq 0 ]]; then
    if [[ -n "${lean_audit_layout_issue_summary:-}" ]]; then
      suggested_prepare_reason="lean_layout_drift_present"
      suggested_next_action_command="$suggested_lean_audit_command"
      suggested_next_action_reason="lean_layout_drift_inspection"
    else
      suggested_prepare_reason="lean_repo_audit_failing"
      suggested_next_action_command="$suggested_lean_audit_command"
      suggested_next_action_reason="lean_repo_audit_inspection"
    fi
  fi
  if [[ "$json_mode" -ne 1 ]]; then
    echo "  lean repo size: ${lean_audit_repo_size_mb} MB / ${lean_audit_size_budget_mb} MB budget"
    echo "  curated saved results: ${lean_audit_manifest_summary_files_count} summary files + ${lean_audit_manifest_run_artifacts_count} run artifacts"
    if [[ "${lean_audit_layout_manifest_present:-0}" -eq 1 ]]; then
      echo "  lean layout manifest: present"
    else
      echo "  lean layout manifest: missing"
    fi
    if [[ "${lean_audit_public_layout_matches:-0}" -eq 1 ]]; then
      echo "  lean toolkit shape: matches expected public file layout"
    else
      echo "  lean toolkit shape: drift detected"
      if [[ -n "${lean_audit_layout_issue_summary:-}" ]]; then
        echo "  lean toolkit drift details: ${lean_audit_layout_issue_summary}"
      fi
      echo "  inspect lean drift: ${suggested_lean_audit_command}"
    fi
  fi
else
  suggested_prepare_reason="lean_repo_audit_helper_missing"
  suggested_next_action_command="$suggested_prepare_command"
  suggested_next_action_reason="lean_repo_audit_helper_missing"
  status_todo "lean repo audit helper is missing"
fi

artifacts=()
for path in \
  autoresearch/.venv \
  autoresearch/__pycache__ \
  autoresearch/results/chat_sessions \
  autoresearch/results/auto_server_state.json \
  hypura-main/target; do
  if [[ -e "$path" ]]; then
    artifacts+=("$path")
  fi
done
if [[ ${#artifacts[@]} -eq 0 ]]; then
  status_pass "no local-only verification junk is present"
else
  if [[ "$suggested_prepare_reason" == "clean_repo_state" || "$suggested_prepare_reason" == "active_rehearsals_running" ]]; then
    suggested_prepare_reason="local_only_artifacts_present"
  fi
  if [[ "$suggested_next_action_reason" == "publish_setup_preview" || "$suggested_next_action_reason" == "active_rehearsals_running" ]]; then
    suggested_next_action_command="$suggested_prepare_command"
    suggested_next_action_reason="local_only_artifact_cleanup"
  fi
  status_warn "local-only artifacts are present: ${artifacts[*]}"
fi

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git_initialized=1
  status_pass "git repository is initialized"
  if git remote get-url origin >/dev/null 2>&1; then
    origin_configured=1
    status_pass "git remote origin is configured"
    current_branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
    if [[ -z "$current_branch" || "$current_branch" == "HEAD" ]]; then
      current_branch="main"
    fi
    current_upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
    if [[ -n "$current_upstream" ]]; then
      branch_has_upstream=1
      read -r branch_behind_count branch_ahead_count <<<"$(git rev-list --left-right --count "${current_upstream}...HEAD" 2>/dev/null || printf '0 0')"
      if [[ "$branch_ahead_count" != "0" ]]; then
        suggested_push_command="git push origin ${current_branch}"
        suggested_push_reason="unpushed_commits"
      fi
    else
      suggested_push_command="git push -u origin ${current_branch}"
      suggested_push_reason="first_push_required"
    fi
  else
    status_todo "git remote origin is not configured yet"
  fi
else
  status_todo "git repository is not initialized yet"
fi

if [[ -f README.md ]]; then
  current_badge_line="$(python3 - <<'PY'
from pathlib import Path
text = Path('README.md').read_text(encoding='utf-8').splitlines()
for line in text:
    if 'badge.svg' in line and '/actions/workflows/release-readiness.yml' in line:
        print(line)
        break
PY
  )"
fi
if [[ -n "$current_badge_line" ]]; then
  readme_badge_present=1
fi
if [[ "$origin_configured" -eq 1 ]] && badge_repo_slug="$(infer_repo_slug)"; then
  expected_badge_markdown="[![Release Readiness](https://github.com/${badge_repo_slug}/actions/workflows/release-readiness.yml/badge.svg)](https://github.com/${badge_repo_slug}/actions/workflows/release-readiness.yml)"
fi
if [[ "$readme_badge_present" -eq 1 ]]; then
  if [[ -n "$expected_badge_markdown" ]]; then
    if [[ "$current_badge_line" == "$expected_badge_markdown" ]]; then
      readme_badge_matches_expected=1
      status_pass "README CI badge matches the configured GitHub repo"
    else
      status_warn "README CI badge does not match the configured GitHub repo (run ./finish_public_release.sh)"
    fi
  else
    status_pass "README CI badge is already installed"
  fi
else
  if [[ "$origin_configured" -eq 1 ]]; then
    status_warn "README CI badge is not installed yet (run ./finish_public_release.sh after the first push)"
  elif [[ "$json_mode" -ne 1 ]]; then
    echo "[info] README CI badge will be installable after the public repo URL is configured"
  fi
fi

if [[ "$ready" -eq 1 ]]; then
  if [[ -n "$suggested_push_command" ]]; then
    suggested_next_action_command="$suggested_push_command"
    suggested_next_action_reason="$suggested_push_reason"
    if [[ "$origin_configured" -eq 1 && "$readme_badge_matches_expected" -eq 0 ]]; then
      suggested_post_publish_action_command="./finish_public_release.sh"
      suggested_post_publish_action_reason="readme_badge_polish"
    fi
  elif [[ "$origin_configured" -eq 1 && "$readme_badge_matches_expected" -eq 0 ]]; then
    suggested_next_action_command="./finish_public_release.sh"
    suggested_next_action_reason="readme_badge_polish"
    suggested_post_publish_action_command=""
    suggested_post_publish_action_reason=""
  else
    suggested_next_action_command=""
    suggested_next_action_reason="ready_no_further_tooling_steps"
    suggested_post_publish_action_command=""
    suggested_post_publish_action_reason=""
  fi
fi

if [[ "$ready" -eq 1 ]]; then
  if [[ -n "$suggested_push_command" ]]; then
    release_stage="ready_to_push"
    if [[ -n "$suggested_push_reason" ]]; then
      release_stage_reason="$suggested_push_reason"
    else
      release_stage_reason="push_required"
    fi
  elif [[ "$suggested_next_action_reason" == "readme_badge_polish" ]]; then
    release_stage="post_push_polish"
    release_stage_reason="readme_badge_polish"
  else
    release_stage="fully_finished"
    release_stage_reason="ready_no_further_tooling_steps"
  fi
else
  if [[ -n "$suggested_next_action_reason" ]]; then
    release_stage_reason="$suggested_next_action_reason"
  fi
fi
case "$release_stage" in
  setup_incomplete) release_stage_label="setup incomplete" ;;
  ready_to_push) release_stage_label="ready to push" ;;
  post_push_polish) release_stage_label="post-push polish" ;;
  fully_finished) release_stage_label="fully finished" ;;
  *) release_stage_label="$release_stage" ;;
esac

if [[ "$json_mode" -eq 1 ]]; then
  json_args=("$ready" "$license_present" "$ci_workflow_present" "$prepare_helper_present" "$install_helper_present" "$install_badge_helper_present" "$rehearsal_temp_helper_present" "$rehearsal_temp_copies_present" "$rehearsal_temp_copies_count" "$rehearsal_temp_total_size_mb" "$rehearsal_temp_reason_summary" "$rehearsal_temp_reason_counts_json" "$rehearsal_temp_failure_copies_count" "$rehearsal_temp_active_count" "$rehearsal_temp_active_reason_summary" "$rehearsal_temp_active_reason_counts_json" "$suggested_prepare_reason" "$readme_badge_present" "$readme_badge_matches_expected" "$holder_hint" "$remote_hint" "$suggested_prepare_command" "$suggested_prepare_failure_cleanup_command" "$suggested_command" "$suggested_preview_command" "$suggested_next_action_command" "$suggested_next_action_reason" "$release_stage" "$release_stage_reason" "$release_stage_label" "$suggested_push_command" "$suggested_post_publish_action_command" "$suggested_post_publish_action_reason" "$expected_badge_markdown" "$suggested_lean_audit_command" "$suggested_lean_audit_json_command" "$lean_audit_helper_present" "$lean_audit_passed" "$git_initialized" "$origin_configured" "$lean_audit_tmp")
  if [[ ${#artifacts[@]} -gt 0 ]]; then
    json_args+=("${artifacts[@]}")
  fi
  python3 - "${json_args[@]}" <<'PY'
import json
from pathlib import Path
import sys

ready = sys.argv[1] == "1"
license_present = sys.argv[2] == "1"
ci_workflow_present = sys.argv[3] == "1"
prepare_helper_present = sys.argv[4] == "1"
install_helper_present = sys.argv[5] == "1"
install_badge_helper_present = sys.argv[6] == "1"
rehearsal_temp_helper_present = sys.argv[7] == "1"
rehearsal_temp_copies_present = sys.argv[8] == "1"
rehearsal_temp_copies_count = int(sys.argv[9])
rehearsal_temp_total_size_mb = float(sys.argv[10])
rehearsal_temp_reason_summary = sys.argv[11]
rehearsal_temp_reason_counts = json.loads(sys.argv[12])
rehearsal_temp_failure_copies_count = int(sys.argv[13])
rehearsal_temp_active_count = int(sys.argv[14])
rehearsal_temp_active_reason_summary = sys.argv[15]
rehearsal_temp_active_reason_counts = json.loads(sys.argv[16])
suggested_prepare_reason = sys.argv[17]
readme_badge_present = sys.argv[18] == "1"
readme_badge_matches_expected = sys.argv[19] == "1"
holder_hint = sys.argv[20]
remote_hint = sys.argv[21]
suggested_prepare_command = sys.argv[22]
suggested_prepare_failure_cleanup_command = sys.argv[23]
suggested_command = sys.argv[24]
suggested_preview_command = sys.argv[25]
suggested_next_action_command = sys.argv[26]
suggested_next_action_reason = sys.argv[27]
release_stage = sys.argv[28]
release_stage_reason = sys.argv[29]
release_stage_label = sys.argv[30]
suggested_push_command = sys.argv[31]
suggested_post_publish_action_command = sys.argv[32]
suggested_post_publish_action_reason = sys.argv[33]
expected_badge_markdown = sys.argv[34]
suggested_lean_audit_command = sys.argv[35]
suggested_lean_audit_json_command = sys.argv[36]
lean_audit_helper_present = sys.argv[37] == "1"
lean_audit_passed = sys.argv[38] == "1"
git_initialized = sys.argv[39] == "1"
origin_configured = sys.argv[40] == "1"
lean_audit_path = sys.argv[41]
artifacts = sys.argv[42:]
lean_audit = None
if lean_audit_path and Path(lean_audit_path).exists():
    lean_audit = json.loads(Path(lean_audit_path).read_text(encoding='utf-8'))
blocking_items = []
if not license_present:
    blocking_items.append('root LICENSE is missing')
if not ci_workflow_present:
    blocking_items.append('CI workflow is missing')
if not prepare_helper_present:
    blocking_items.append('public push prep helper is missing')
if not install_helper_present:
    blocking_items.append('license install helper is missing')
if not install_badge_helper_present:
    blocking_items.append('CI badge helper is missing')
if not lean_audit_helper_present:
    blocking_items.append('lean repo audit helper is missing')
elif not lean_audit_passed:
    layout_issue_summary = '; '.join(
        f"{key}=" + ','.join(value)
        for key, value in sorted((lean_audit or {}).get('layout_issues', {}).items())
    )
    if layout_issue_summary:
        blocking_items.append(f'lean toolkit shape drift: {layout_issue_summary}')
    else:
        blocking_items.append('lean repo audit is failing')
if not git_initialized:
    blocking_items.append('git repository is not initialized')
elif not origin_configured:
    blocking_items.append('git remote origin is not configured')

data = {
    "ready": ready,
    "release_stage": release_stage,
    "release_stage_reason": release_stage_reason,
    "release_stage_label": release_stage_label,
    "blocking_items": blocking_items,
    "checks": {
        "license_present": license_present,
        "ci_workflow_present": ci_workflow_present,
        "prepare_public_push_present": prepare_helper_present,
        "install_license_present": install_helper_present,
        "install_ci_badge_present": install_badge_helper_present,
        "rehearsal_temp_helper_present": rehearsal_temp_helper_present,
        "rehearsal_temp_copies_present": rehearsal_temp_copies_present,
        "readme_ci_badge_present": readme_badge_present,
        "readme_ci_badge_matches_expected": readme_badge_matches_expected,
        "lean_repo_audit_present": lean_audit_helper_present,
        "lean_repo_audit_passed": lean_audit_passed,
        "lean_layout_manifest_present": bool(lean_audit and lean_audit.get('checks', {}).get('layout_manifest_present')),
        "lean_public_layout_matches": bool(lean_audit and lean_audit.get('checks', {}).get('public_layout_matches')),
        "git_initialized": git_initialized,
        "origin_configured": origin_configured,
        "local_only_artifacts_present": bool(artifacts),
    },
    "hints": {
        "holder": holder_hint,
        "remote": remote_hint,
    },
    "suggested_make_publish_ready_command": suggested_command,
    "suggested_make_publish_ready_preview_command": suggested_preview_command,
    "suggested_prepare_public_push_command": suggested_prepare_command,
    "suggested_prepare_public_push_reason": suggested_prepare_reason,
    "suggested_prepare_public_push_failure_cleanup_command": suggested_prepare_failure_cleanup_command,
    "suggested_next_action_command": suggested_next_action_command,
    "suggested_next_action_reason": suggested_next_action_reason,
    "suggested_push_command": suggested_push_command,
    "suggested_post_publish_action_command": suggested_post_publish_action_command,
    "suggested_post_publish_action_reason": suggested_post_publish_action_reason,
    "suggested_publish_rehearsal_command": "./rehearse_publish_flow.sh",
    "suggested_rehearsal_temp_status_command": "./rehearsal_temp_status.sh",
    "suggested_rehearsal_temp_failure_cleanup_command": "./rehearsal_temp_status.sh --clean-failures",
    "suggested_rehearsal_temp_cleanup_command": "./rehearsal_temp_status.sh --clean",
    "suggested_lean_audit_command": suggested_lean_audit_command,
    "suggested_lean_audit_json_command": suggested_lean_audit_json_command,
    "suggested_finish_public_release_command": "./finish_public_release.sh",
    "suggested_install_ci_badge_command": "./install_ci_badge.sh",
    "expected_ci_badge_markdown": expected_badge_markdown,
    "lean_layout_issue_summary": '; '.join(
        f"{key}=" + ','.join(value)
        for key, value in sorted((lean_audit or {}).get('layout_issues', {}).items())
    ),
    "lean_layout_issues": (lean_audit or {}).get('layout_issues', {}),
    "rehearsal_temp_copies_count": rehearsal_temp_copies_count,
    "rehearsal_temp_total_size_mb": rehearsal_temp_total_size_mb,
    "rehearsal_temp_reason_summary": rehearsal_temp_reason_summary,
    "rehearsal_temp_reason_counts": rehearsal_temp_reason_counts,
    "rehearsal_temp_failure_copies_count": rehearsal_temp_failure_copies_count,
    "rehearsal_temp_active_count": rehearsal_temp_active_count,
    "rehearsal_temp_active_reason_summary": rehearsal_temp_active_reason_summary,
    "rehearsal_temp_active_reason_counts": rehearsal_temp_active_reason_counts,
    "lean_audit": lean_audit,
    "local_only_artifacts": artifacts,
}
print(json.dumps(data, indent=2))
PY
else
  echo
  if [[ "$ready" -eq 1 ]]; then
    echo "Ready to publish: yes"
    echo "Release stage: $release_stage_label"
    if [[ -n "$suggested_next_action_command" ]]; then
      echo "Suggested next action:"
      echo "  $suggested_next_action_command"
    fi
    if [[ "$rehearsal_temp_copies_present" -eq 1 ]]; then
      echo "Saved rehearsal temp cleanup:"
      echo "  ./rehearsal_temp_status.sh"
      echo "  ./rehearsal_temp_status.sh --clean-failures"
      echo "  ./rehearsal_temp_status.sh --clean"
      if [[ "$rehearsal_temp_failure_copies_count" != "0" ]]; then
        echo "  ./prepare_public_push.sh --clean-failure-rehearsals"
      fi
    fi
    if [[ -n "$suggested_post_publish_action_command" ]]; then
      echo "Post-publish polish still remaining:"
      echo "  $suggested_post_publish_action_command"
    elif [[ -z "$suggested_next_action_command" ]]; then
      echo "No further publish-tooling steps remain."
    fi
  else
    echo "Ready to publish: not yet"
    echo "Release stage: $release_stage_label"
    blockers=()
    if [[ "$license_present" -eq 0 ]]; then
      blockers+=("root LICENSE is missing")
    fi
    if [[ "$ci_workflow_present" -eq 0 ]]; then
      blockers+=("CI workflow is missing")
    fi
    if [[ "$prepare_helper_present" -eq 0 ]]; then
      blockers+=("public push prep helper is missing")
    fi
    if [[ "$install_helper_present" -eq 0 ]]; then
      blockers+=("license install helper is missing")
    fi
    if [[ "$install_badge_helper_present" -eq 0 ]]; then
      blockers+=("CI badge helper is missing")
    fi
    if [[ "$lean_audit_helper_present" -eq 0 ]]; then
      blockers+=("lean repo audit helper is missing")
    elif [[ "$lean_audit_passed" -eq 0 ]]; then
      if [[ -n "${lean_audit_layout_issue_summary:-}" ]]; then
        blockers+=("lean toolkit shape drift: ${lean_audit_layout_issue_summary}")
      else
        blockers+=("lean repo audit is failing")
      fi
    fi
    if [[ "$git_initialized" -eq 0 ]]; then
      blockers+=("git repository is not initialized")
    elif [[ "$origin_configured" -eq 0 ]]; then
      blockers+=("git remote origin is not configured")
    fi
    if [[ ${#blockers[@]} -gt 0 ]]; then
      echo "Current blockers:"
      for blocker in "${blockers[@]}"; do
        echo "  - $blocker"
      done
    fi
    if [[ "$rehearsal_temp_copies_present" -eq 1 ]]; then
      echo "Saved rehearsal temp cleanup:"
      echo "  ./rehearsal_temp_status.sh"
      echo "  ./rehearsal_temp_status.sh --clean-failures"
      echo "  ./rehearsal_temp_status.sh --clean"
      if [[ "$rehearsal_temp_failure_copies_count" != "0" ]]; then
        echo "  $suggested_prepare_failure_cleanup_command"
      fi
    fi
    if [[ "${lean_audit_public_layout_matches:-1}" -eq 0 ]]; then
      echo "Lean drift inspection:"
      echo "  $suggested_lean_audit_command"
    fi
    echo "Suggested next action:"
    echo "  $suggested_next_action_command"
    echo "Suggested prep command:"
    echo "  $suggested_prepare_command"
    echo "Suggested preview command:"
    echo "  $suggested_preview_command"
    echo "Suggested real command:"
    echo "  $suggested_command"
    echo "Optional full rehearsal:"
    echo "  ./rehearse_publish_flow.sh"
  fi
fi

if [[ "$ready" -eq 1 ]]; then
  exit 0
else
  exit 1
fi
