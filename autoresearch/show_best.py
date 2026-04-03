"""
Show the current best validated benchmark result and config status.

Usage:
    uv run show_best.py
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

import psutil
import yaml


ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
RUNS_DIR = RESULTS_DIR / "runs"
BEST_PATH = RESULTS_DIR / "best.json"
BEST_CANDIDATE_PATH = RESULTS_DIR / "best_candidate.yaml"
CURRENT_STATE_PATH = RESULTS_DIR / "current_state_best.json"
TRACKED_CANDIDATE_PATH = ROOT / "candidate.yaml"
LAUNCHER_PATH = ROOT.parent / "hypura-main" / "scripts" / "serve-gemma4-m4pro.sh"
CURRENT_STATE_LAUNCHER_PATH = ROOT.parent / "hypura-main" / "scripts" / "serve-gemma4-current-state.sh"
FLASHMOE_CANDIDATE_PATH = ROOT / "candidates" / "flashmoe-slot-bank-16.yaml"
FLASHMOE_LAUNCHER_PATH = ROOT / "flashmoe_gemma4_best.sh"
FLASHMOE_SERVER_LAUNCHER_PATH = ROOT / "flashmoe_gemma4_serve.sh"
AUTO_LAUNCHER_PATH = ROOT / "serve_gemma4_auto.sh"
SERVER_START_PATH = ROOT / "gemma4_server_start.sh"
ANSWER_WRAPPER_PATH = ROOT / "gemma4_answer.sh"
AUTO_SERVER_STATUS_PATH = ROOT / "gemma4_server_status.sh"
AUTO_SERVER_STOP_PATH = ROOT / "gemma4_server_stop.sh"
CHAT_CLIENT_PATH = ROOT / "gemma4_chat.py"
BEST_FLASHMOE_PATH = RESULTS_DIR / "best_flashmoe.json"
BEST_FLASHMOE_CANDIDATE_PATH = RESULTS_DIR / "best_flashmoe_candidate.yaml"
BEST_FLASHMOE_SERVER_PATH = RESULTS_DIR / "best_flashmoe_server.json"
BEST_FLASHMOE_SERVER_CANDIDATE_PATH = RESULTS_DIR / "best_flashmoe_server_candidate.yaml"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a top-level mapping.")
    return data


def resolve_record_path(value: str | Path, base: Path) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = (base / path).resolve()
    return path


def normalize_candidate(candidate: dict[str, Any], candidate_path: Path) -> dict[str, Any]:
    normalized = json.loads(json.dumps(candidate))
    model_value = normalized.get("model_path")
    if model_value:
        model_path = Path(str(model_value)).expanduser()
        if not model_path.is_absolute():
            model_path = (candidate_path.parent / model_path).resolve()
        normalized["model_path"] = str(model_path)
    return normalized


def flatten_mapping(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in data.items():
        label = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flat.update(flatten_mapping(value, label))
        else:
            flat[label] = value
    return flat


def diff_candidates(best_candidate: dict[str, Any], tracked_candidate: dict[str, Any]) -> list[str]:
    best_flat = flatten_mapping(best_candidate)
    tracked_flat = flatten_mapping(tracked_candidate)
    keys = sorted(set(best_flat) | set(tracked_flat))
    diffs: list[str] = []
    for key in keys:
        best_value = best_flat.get(key)
        tracked_value = tracked_flat.get(key)
        if best_value != tracked_value:
            diffs.append(f"{key}: best={best_value!r}, tracked={tracked_value!r}")
    return diffs


def core_signature(candidate: dict[str, Any]) -> dict[str, Any]:
    backend_config = candidate.get("backend_config", {})
    return {
        "backend": candidate.get("backend"),
        "model_path": candidate.get("model_path"),
        "context": candidate.get("context"),
        "max_tokens": candidate.get("max_tokens"),
        "threads": backend_config.get("threads"),
        "threads_batch": backend_config.get("threads_batch"),
        "batch_size": backend_config.get("batch_size"),
        "ubatch_size": backend_config.get("ubatch_size"),
    }


def recent_same_core_runs(best_candidate: dict[str, Any], limit: int = 12) -> list[dict[str, Any]]:
    target_signature = core_signature(best_candidate)
    matches: list[dict[str, Any]] = []
    for artifact_path in sorted(RUNS_DIR.glob("*.json"), reverse=True):
        artifact = load_json(artifact_path)
        candidate_path = resolve_record_path(
            artifact.get("candidate_path", TRACKED_CANDIDATE_PATH),
            ROOT,
        )
        normalized_candidate = normalize_candidate(
            dict(artifact.get("candidate", {})),
            candidate_path,
        )
        if core_signature(normalized_candidate) != target_signature:
            continue
        summary = artifact.get("summary", {})
        matches.append(
            {
                "run_id": artifact.get("run_id", artifact_path.stem),
                "status": artifact.get("status", ""),
                "description": normalized_candidate.get("description", ""),
                "gen_tok_s": float(summary.get("gen_tok_s", 0.0)),
                "prompt_tok_s": float(summary.get("prompt_tok_s", 0.0)),
                "ttft_ms": float(summary.get("ttft_ms", 0.0)),
                "artifact_path": str(artifact_path),
            }
        )
        if len(matches) >= limit:
            break
    return matches


def best_backend_run(backend_name: str, require_measured_runs: int = 3) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None

    def key_for(item: dict[str, Any]) -> tuple[float, float, float, float]:
        summary = item.get("summary", {})
        return (
            float(summary.get("gen_tok_s", 0.0)),
            float(summary.get("prompt_tok_s", 0.0)),
            -float(summary.get("ttft_ms", float("inf"))),
            -float(summary.get("load_s", float("inf"))),
        )

    for artifact_path in sorted(RUNS_DIR.glob("*.json"), reverse=True):
        artifact = load_json(artifact_path)
        if artifact.get("backend") != backend_name:
            continue
        if artifact.get("status") not in {"keep", "discard"}:
            continue
        candidate = artifact.get("candidate", {})
        if int(candidate.get("measured_runs", 0)) < require_measured_runs:
            continue
        artifact["artifact_path"] = str(artifact_path)
        if best is None or key_for(artifact) > key_for(best):
            best = artifact
    return best


def current_machine_state(min_free_gb: float) -> dict[str, float]:
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    total_gb = memory.total / float(1 << 30)
    available_gb = memory.available / float(1 << 30)
    used_gb = (memory.total - memory.available) / float(1 << 30)
    swap_used_gb = swap.used / float(1 << 30)
    reserve_gb = used_gb + min_free_gb
    return {
        "total_gb": total_gb,
        "used_gb": used_gb,
        "available_gb": available_gb,
        "swap_used_gb": swap_used_gb,
        "reserve_gb": reserve_gb,
    }


def main() -> int:
    if not BEST_PATH.exists():
        raise FileNotFoundError(f"{BEST_PATH} does not exist. Run a benchmark first.")
    if not BEST_CANDIDATE_PATH.exists():
        raise FileNotFoundError(
            f"{BEST_CANDIDATE_PATH} does not exist. Run `uv run sync_best.py` first."
        )

    best = load_json(BEST_PATH)
    best_candidate = normalize_candidate(load_yaml(BEST_CANDIDATE_PATH), BEST_CANDIDATE_PATH)

    tracked_candidate = None
    tracked_diffs: list[str] = []
    if TRACKED_CANDIDATE_PATH.exists():
        tracked_candidate = normalize_candidate(
            load_yaml(TRACKED_CANDIDATE_PATH),
            TRACKED_CANDIDATE_PATH,
        )
        tracked_diffs = diff_candidates(best_candidate, tracked_candidate)

    summary = best["summary"]
    constraints = best.get("constraints", {})

    print("Current best validated Gemma runtime")
    print(f"  Run ID:         {best['run_id']}")
    print(f"  Backend:        {best['backend']}")
    print(f"  Gen tok/s:      {summary['gen_tok_s']:.2f}")
    print(f"  Prompt tok/s:   {summary['prompt_tok_s']:.2f}")
    print(f"  TTFT:           {summary['ttft_ms']:.1f} ms")
    print(f"  Load time:      {summary['load_s']:.2f} s")
    print(f"  Measured runs:  {best.get('measured_runs', 0)}")
    print(f"  Free-memory floor: {constraints.get('min_free_gb', 'n/a')} GB")
    print(f"  Swap limit:        {constraints.get('max_swap_delta_gb', 'n/a')} GB")
    print(f"  Artifact:       {best['artifact_path']}")
    print(f"  Stable config:  {BEST_CANDIDATE_PATH}")
    print()

    machine_state = current_machine_state(float(constraints.get("min_free_gb", 4.0)))
    print("Current machine state")
    print(f"  Used memory:    {machine_state['used_gb']:.2f} GB / {machine_state['total_gb']:.2f} GB")
    print(f"  Available:      {machine_state['available_gb']:.2f} GB")
    print(f"  Swap used:      {machine_state['swap_used_gb']:.2f} GB")
    print(f"  Launch reserve: {machine_state['reserve_gb']:.2f} GB")
    print()

    print("Stable best config")
    print(f"  Model:          {best_candidate.get('model_path', '')}")
    print(f"  Context:        {best_candidate.get('context', '')}")
    backend_config = best_candidate.get("backend_config", {})
    print(f"  Threads:        {backend_config.get('threads', '')}")
    print(f"  Prompt threads: {backend_config.get('threads_batch', '')}")
    print(f"  Batch:          {backend_config.get('batch_size', '')}")
    print(f"  Micro-batch:    {backend_config.get('ubatch_size', '')}")
    print()

    if CURRENT_STATE_PATH.exists():
        current_state = load_json(CURRENT_STATE_PATH)
        winner = current_state.get("winner", {})
        relative = float(current_state.get("relative_to_historical", 0.0))
        degraded = bool(current_state.get("degraded_vs_historical", False))
        machine_snapshot = current_state.get("machine_state", {})
        print("Current-state recommendation")
        print(f"  Updated at:     {current_state.get('updated_at', '')}")
        if current_state.get("refresh_skipped"):
            print("  Refresh:        skipped")
            if current_state.get("skip_reason"):
                print(f"  Reason:         {current_state.get('skip_reason')}")
        if current_state.get("no_valid_winner"):
            print("  Winner:         no valid contender")
            print("  State:          current machine state violates the benchmark guardrails")
        else:
            print(f"  Winner:         {winner.get('combo_key', '')}")
            print(f"  Median gen:     {float(winner.get('median_gen_tok_s', 0.0)):.2f} tok/s")
            print(f"  Median prompt:  {float(winner.get('median_prompt_tok_s', 0.0)):.2f} tok/s")
            print(f"  Median TTFT:    {float(winner.get('median_ttft_ms', 0.0)):.1f} ms")
            if relative > 0:
                print(f"  vs historical:  {relative:.2%}")
            if degraded:
                print("  State:          degraded vs historical best")
        if machine_snapshot:
            print(
                "  Machine:        "
                f"used {float(machine_snapshot.get('used_gb', 0.0)):.2f} GB, "
                f"available {float(machine_snapshot.get('available_gb', 0.0)):.2f} GB, "
                f"swap {float(machine_snapshot.get('swap_used_gb', 0.0)):.2f} GB"
            )
        print(f"  Sweep summary:  {current_state.get('sweep_summary_path', '')}")
        print(f"  Candidate file: {current_state.get('current_state_candidate_path', '(none)')}")
        print()

    if BEST_FLASHMOE_PATH.exists() and BEST_FLASHMOE_CANDIDATE_PATH.exists():
        flash_record = load_json(BEST_FLASHMOE_PATH)
        flashmoe_best = load_json(resolve_record_path(flash_record["artifact_path"], ROOT))
        flashmoe_best["artifact_path"] = str(flash_record["artifact_path"])
        flash_candidate = normalize_candidate(
            load_yaml(BEST_FLASHMOE_CANDIDATE_PATH),
            BEST_FLASHMOE_CANDIDATE_PATH,
        )
    else:
        flashmoe_best = best_backend_run("flashmoe")
        flash_candidate = None
    if flashmoe_best is not None:
        flash_summary = flashmoe_best.get("summary", {})
        if flash_candidate is None:
            flash_candidate_path = resolve_record_path(
                flashmoe_best.get("candidate_path", FLASHMOE_CANDIDATE_PATH),
                ROOT,
            )
            flash_candidate = normalize_candidate(
                dict(flashmoe_best.get("candidate", {})),
                flash_candidate_path,
            )
        flash_backend = flash_candidate.get("backend_config", {})
        print("Best Flash-MoE alternate")
        print(f"  Run ID:         {flashmoe_best.get('run_id', '')}")
        print(f"  Gen tok/s:      {float(flash_summary.get('gen_tok_s', 0.0)):.2f}")
        print(f"  Prompt tok/s:   {float(flash_summary.get('prompt_tok_s', 0.0)):.2f}")
        print(f"  TTFT:           {float(flash_summary.get('ttft_ms', 0.0)):.1f} ms")
        print(f"  Load time:      {float(flash_summary.get('load_s', 0.0)):.2f} s")
        print(f"  Min free:       {float(flash_summary.get('min_free_gb', 0.0)):.2f} GB")
        print(f"  Swap delta:     {float(flash_summary.get('swap_delta_gb', 0.0)):.2f} GB")
        print(f"  Context:        {flash_candidate.get('context', '')}")
        print(f"  Slot bank:      {flash_backend.get('moe_slot_bank', '')}")
        print(f"  GPU layers:     {flash_backend.get('gpu_layers', '')}")
        print(f"  Artifact:       {flashmoe_best.get('artifact_path', '')}")
        print(f"  Candidate:      {BEST_FLASHMOE_CANDIDATE_PATH if BEST_FLASHMOE_CANDIDATE_PATH.exists() else FLASHMOE_CANDIDATE_PATH}")
        print()

    if BEST_FLASHMOE_SERVER_PATH.exists() and BEST_FLASHMOE_SERVER_CANDIDATE_PATH.exists():
        flashmoe_server_record = load_json(BEST_FLASHMOE_SERVER_PATH)
        flashmoe_server_best = load_json(
            resolve_record_path(flashmoe_server_record["artifact_path"], ROOT)
        )
        flashmoe_server_best["artifact_path"] = str(flashmoe_server_record["artifact_path"])
        flash_server_candidate = normalize_candidate(
            load_yaml(BEST_FLASHMOE_SERVER_CANDIDATE_PATH),
            BEST_FLASHMOE_SERVER_CANDIDATE_PATH,
        )
    else:
        flashmoe_server_best = best_backend_run("flashmoe_server")
        flash_server_candidate = None
    if flashmoe_server_best is not None:
        flash_server_summary = flashmoe_server_best.get("summary", {})
        if flash_server_candidate is None:
            flash_server_candidate_path = resolve_record_path(
                flashmoe_server_best.get(
                    "candidate_path",
                    ROOT / "candidates" / "flashmoe-server-slot-bank-16.yaml",
                ),
                ROOT,
            )
            flash_server_candidate = normalize_candidate(
                dict(flashmoe_server_best.get("candidate", {})),
                flash_server_candidate_path,
            )
        flash_server_backend = flash_server_candidate.get("backend_config", {})
        print("Best Flash-MoE resident-server benchmark")
        print(f"  Run ID:         {flashmoe_server_best.get('run_id', '')}")
        print(f"  Gen tok/s:      {float(flash_server_summary.get('gen_tok_s', 0.0)):.2f}")
        print(f"  Prompt tok/s:   {float(flash_server_summary.get('prompt_tok_s', 0.0)):.2f}")
        print(f"  TTFT:           {float(flash_server_summary.get('ttft_ms', 0.0)):.1f} ms")
        print(f"  Load time:      {float(flash_server_summary.get('load_s', 0.0)):.2f} s")
        print(f"  Min free:       {float(flash_server_summary.get('min_free_gb', 0.0)):.2f} GB")
        print(f"  Swap delta:     {float(flash_server_summary.get('swap_delta_gb', 0.0)):.2f} GB")
        print(f"  Context:        {flash_server_candidate.get('context', '')}")
        print(f"  Slot bank:      {flash_server_backend.get('moe_slot_bank', '')}")
        print(f"  GPU layers:     {flash_server_backend.get('gpu_layers', '')}")
        print(f"  Parallel:       {flash_server_backend.get('parallel', '')}")
        print(f"  Prompt cache:   {flash_server_backend.get('cache_prompt', '')}")
        print(f"  Artifact:       {flashmoe_server_best.get('artifact_path', '')}")
        print(f"  Candidate:      {BEST_FLASHMOE_SERVER_CANDIDATE_PATH if BEST_FLASHMOE_SERVER_CANDIDATE_PATH.exists() else ROOT / 'candidates' / 'flashmoe-server-slot-bank-16.yaml'}")
        print()

    matching_runs = recent_same_core_runs(best_candidate)
    if matching_runs:
        valid_runs = [item for item in matching_runs if item["status"] in {"keep", "discard"}]
        latest = matching_runs[0]
        print("Recent same-core reruns")
        print(f"  Matches found:  {len(matching_runs)} total, {len(valid_runs)} valid")
        if valid_runs:
            gen_scores = [item["gen_tok_s"] for item in valid_runs]
            prompt_scores = [item["prompt_tok_s"] for item in valid_runs]
            ttft_scores = [item["ttft_ms"] for item in valid_runs]
            print(f"  Median gen:     {statistics.median(gen_scores):.2f} tok/s")
            print(f"  Median prompt:  {statistics.median(prompt_scores):.2f} tok/s")
            print(f"  Median TTFT:    {statistics.median(ttft_scores):.1f} ms")
            print(f"  Range gen:      {min(gen_scores):.2f} .. {max(gen_scores):.2f} tok/s")
        else:
            print("  No recent valid reruns found.")
        print(
            f"  Latest rerun:   {latest['run_id']} "
            f"({latest['status']}, {latest['gen_tok_s']:.2f} tok/s)"
        )
        print("  Note: this view ignores memory-reserve overrides, so it shows how much the")
        print("        same core runtime knobs can move around as machine state changes.")
        print()

    print("Tracked candidate status")
    if tracked_candidate is None:
        print(f"  Missing tracked candidate: {TRACKED_CANDIDATE_PATH}")
    elif not tracked_diffs:
        print("  candidate.yaml matches the stable best config.")
    else:
        print("  candidate.yaml has drifted from the stable best config.")
        for diff in tracked_diffs[:10]:
            print(f"  - {diff}")
        if len(tracked_diffs) > 10:
            print(f"  - ... and {len(tracked_diffs) - 10} more differences")
    print()

    print("Useful commands")
    print(f"  Replay best benchmark: uv run train.py --candidate {BEST_CANDIDATE_PATH}")
    print(f"  Refresh stable best:   uv run sync_best.py")
    print(f"  Refresh current-state: uv run refresh_current_state.py")
    print(f"  Launch best server:    {LAUNCHER_PATH}")
    print(f"  Launch current-state:  {CURRENT_STATE_LAUNCHER_PATH}")
    if flashmoe_best is not None:
        print(f"  Run Flash-MoE alt:     {FLASHMOE_LAUNCHER_PATH}")
        print(f"  Serve Flash-MoE alt:   {FLASHMOE_SERVER_LAUNCHER_PATH}")
        print(f"  Server start:          {SERVER_START_PATH}")
        print(f"  Auto server choice:    {AUTO_LAUNCHER_PATH}")
        print(f"  Server status + auto:  {AUTO_SERVER_STATUS_PATH}")
        print(f"  Server stop:           {AUTO_SERVER_STOP_PATH}")
        print(f"  Interactive chat:      python3 {CHAT_CLIENT_PATH} --mode auto")
        print(f"  Chat no-stream:        python3 {CHAT_CLIENT_PATH} --mode auto --no-stream")
        print(f"  Switch chat runtime:   python3 {CHAT_CLIENT_PATH} --mode speed|memory --replace")
        print(f"  Resume chat:           python3 {CHAT_CLIENT_PATH} --mode auto --session NAME")
        print(f"  Show saved chat:       python3 {CHAT_CLIENT_PATH} --show-session NAME")
        print(f"  Delete saved chat:     python3 {CHAT_CLIENT_PATH} --delete-session NAME")
        print(f"  Streaming smoke:       python3 {ROOT / 'streaming_regression_smoke.py'}")
        print(f"  Release preflight:     python3 {ROOT / 'release_readiness_check.py'}")
        release_checklist = ROOT.parent / 'docs' / 'RELEASE_CHECKLIST.md'
        if not release_checklist.exists():
            release_checklist = ROOT.parent / 'RELEASE_CHECKLIST.md'
        print(f"  Release checklist:     {release_checklist}")
        print(f"  Sync Flash-MoE alt:    {ROOT / 'sync_flashmoe_best.py'}")
        print(f"  Sync FM server alt:    {ROOT / 'sync_flashmoe_server_best.py'}")
    print(f"  Ask either runtime:    {ANSWER_WRAPPER_PATH} --mode speed|memory|auto \"your prompt\"")
    print(f"  Stream one prompt:     {ANSWER_WRAPPER_PATH} --mode auto --stream \"your prompt\"")
    print(f"  Switch answer runtime: {ANSWER_WRAPPER_PATH} --mode speed|memory --replace \"your prompt\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
