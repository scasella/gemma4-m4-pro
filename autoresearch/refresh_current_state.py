"""
Revalidate a small neighborhood around the saved best config and record a
current-state winner without changing the historical best.

Usage:
    uv run refresh_current_state.py
    uv run refresh_current_state.py --repeat 2
    uv run refresh_current_state.py --grid backend_config.threads_batch=13,14
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil
import yaml


ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
BEST_PATH = RESULTS_DIR / "best.json"
BEST_CANDIDATE_PATH = RESULTS_DIR / "best_candidate.yaml"
CURRENT_STATE_JSON_PATH = RESULTS_DIR / "current_state_best.json"
CURRENT_STATE_CANDIDATE_PATH = RESULTS_DIR / "current_state_candidate.yaml"
SWEEP_SCRIPT = ROOT / "sweep.py"
DEGRADED_THRESHOLD = 0.75
MEMORY_IMPROVEMENT_GB = 0.5
SWAP_IMPROVEMENT_GB = 0.25


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate",
        default=str(BEST_CANDIDATE_PATH),
        help="Base candidate to revalidate around.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=2,
        help="Repeats per contender in the current-state sweep.",
    )
    parser.add_argument(
        "--grid",
        action="append",
        default=[],
        metavar="PATH=V1,V2",
        help="Override the default contender grid. Repeat for multiple dimensions.",
    )
    parser.add_argument(
        "--label",
        default="current-state",
        help="Sweep label prefix.",
    )
    return parser.parse_args()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a top-level mapping.")
    return data


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
        "min_free_gb": min_free_gb,
    }


def load_existing_current_state() -> dict[str, Any] | None:
    if not CURRENT_STATE_JSON_PATH.exists():
        return None
    return json.loads(CURRENT_STATE_JSON_PATH.read_text(encoding="utf-8"))


def skip_reason_for_unchanged_overload(
    existing_record: dict[str, Any] | None,
    machine_state: dict[str, float],
) -> str | None:
    if not existing_record or not existing_record.get("no_valid_winner"):
        return None

    previous_state = existing_record.get("machine_state")
    if not isinstance(previous_state, dict):
        return None

    previous_available = float(previous_state.get("available_gb", 0.0))
    previous_used = float(previous_state.get("used_gb", 0.0))
    previous_swap = float(previous_state.get("swap_used_gb", 0.0))

    available_improved = machine_state["available_gb"] >= previous_available + MEMORY_IMPROVEMENT_GB
    used_improved = machine_state["used_gb"] <= previous_used - MEMORY_IMPROVEMENT_GB
    swap_improved = machine_state["swap_used_gb"] <= previous_swap - SWAP_IMPROVEMENT_GB

    if available_improved or used_improved or swap_improved:
        return None

    return (
        "Skipped refresh because the last refresh found no valid contender and "
        "the machine has not improved enough since then."
    )


def normalize_candidate(candidate: dict[str, Any], candidate_path: Path) -> dict[str, Any]:
    normalized = json.loads(json.dumps(candidate))
    model_value = normalized.get("model_path")
    if model_value:
        model_path = Path(str(model_value)).expanduser()
        if not model_path.is_absolute():
            model_path = (candidate_path.parent / model_path).resolve()
        normalized["model_path"] = str(model_path)
    return normalized


def parse_scalar(raw_value: str) -> Any:
    return yaml.safe_load(raw_value)


def apply_override(candidate: dict[str, Any], dotted_path: str, value: Any) -> dict[str, Any]:
    updated = json.loads(json.dumps(candidate))
    keys = [segment.strip() for segment in dotted_path.split(".") if segment.strip()]
    if not keys:
        raise ValueError(f"Invalid override path: {dotted_path}")
    target = updated
    for key in keys[:-1]:
        child = target.get(key)
        if child in (None, ""):
            child = {}
            target[key] = child
        if not isinstance(child, dict):
            raise ValueError(f"Override path is not a mapping: {dotted_path}")
        target = child
    target[keys[-1]] = value
    return updated


def format_override(path: str, value: Any) -> str:
    return f"{path}={json.dumps(value)}"


def override_value_map(overrides: list[str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for override in overrides:
        if "=" not in override:
            continue
        path, raw_value = override.split("=", 1)
        parsed[path.strip()] = parse_scalar(raw_value)
    return parsed


def default_grids(candidate: dict[str, Any]) -> list[str]:
    backend = str(candidate.get("backend", ""))
    backend_config = candidate.get("backend_config", {})
    if backend != "hypura":
        return []

    threads_batch = int(backend_config.get("threads_batch", backend_config.get("threads", 10)))
    contenders = sorted({max(1, threads_batch - 1), threads_batch})
    return [f"backend_config.threads_batch={','.join(str(value) for value in contenders)}"]


def extract_summary_path(text: str) -> Path:
    for line in text.splitlines():
        if line.startswith("Sweep summary:"):
            return Path(line.split(":", 1)[1].strip()).expanduser().resolve()
    raise ValueError(f"Missing sweep summary path in output:\n{text}")


def make_relative_model_path(candidate: dict[str, Any], output_path: Path) -> dict[str, Any]:
    updated = json.loads(json.dumps(candidate))
    model_value = updated.get("model_path")
    if not model_value:
        return updated
    model_path = Path(str(model_value)).expanduser().resolve()
    try:
        updated["model_path"] = os.path.relpath(
            str(model_path),
            start=str(output_path.parent.resolve()),
        )
    except Exception:
        updated["model_path"] = str(model_path)
    return updated


def select_grouped_winner(grouped_results: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in grouped_results:
        if int(row.get("valid_runs", 0)) > 0:
            return row
    return None


def main() -> int:
    args = parse_args()
    candidate_path = Path(args.candidate).expanduser().resolve()
    candidate = normalize_candidate(load_yaml(candidate_path), candidate_path)
    min_free_gb = float(candidate.get("min_free_gb", 4.0))
    machine_state = current_machine_state(min_free_gb)
    existing_record = load_existing_current_state()

    skip_reason = skip_reason_for_unchanged_overload(existing_record, machine_state)
    if skip_reason:
        payload = dict(existing_record)
        payload["updated_at"] = utc_now().isoformat()
        payload["refresh_skipped"] = True
        payload["skip_reason"] = skip_reason
        payload["machine_state"] = machine_state
        CURRENT_STATE_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        print(f"Current-state record:   {CURRENT_STATE_JSON_PATH}")
        print("Refresh:                skipped")
        print(f"Reason:                 {skip_reason}")
        print(
            "Machine state:          "
            f"used {machine_state['used_gb']:.2f} GB, "
            f"available {machine_state['available_gb']:.2f} GB, "
            f"swap {machine_state['swap_used_gb']:.2f} GB"
        )
        print(f"Current-state candidate:{payload.get('current_state_candidate_path') or '(none)'}")
        if payload.get("no_valid_winner"):
            print("Winner:                 no valid contender")
            print("State:                  current machine state still violates the benchmark guardrails")
        return 0

    grids = list(args.grid) if args.grid else default_grids(candidate)
    if not grids:
        raise ValueError("No contender grid supplied and no default grid is available for this candidate.")

    command = [
        sys.executable,
        str(SWEEP_SCRIPT),
        "--candidate",
        str(candidate_path),
        "--label",
        args.label,
        "--repeat",
        str(args.repeat),
    ]
    for grid in grids:
        command.extend(["--grid", grid])
    command.extend(["--override", "measured_runs=1"])

    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    combined = completed.stdout if not completed.stderr else f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode != 0:
        raise RuntimeError(combined.strip())

    sweep_summary_path = extract_summary_path(combined)
    sweep_summary = json.loads(sweep_summary_path.read_text(encoding="utf-8"))
    grouped_winner = select_grouped_winner(sweep_summary.get("grouped_results", []))
    historical_best = json.loads(BEST_PATH.read_text(encoding="utf-8")) if BEST_PATH.exists() else None
    historical_gen = (
        float(historical_best.get("summary", {}).get("gen_tok_s", 0.0))
        if historical_best is not None
        else 0.0
    )
    relative_ratio = (
        (float(grouped_winner["median_gen_tok_s"]) / historical_gen)
        if (historical_gen > 0 and grouped_winner is not None)
        else 0.0
    )
    degraded = grouped_winner is None or (historical_gen > 0 and relative_ratio < DEGRADED_THRESHOLD)

    current_state_candidate_path: str | None = None
    if grouped_winner is not None:
        current_candidate = json.loads(json.dumps(candidate))
        for key, value in grouped_winner.get("combo", {}).items():
            current_candidate = apply_override(current_candidate, key, parse_scalar(json.dumps(value)))

        override_map = override_value_map(sweep_summary.get("base_overrides", []))
        for key in (
            "backend_config.memory_reserve_gb",
            "backend_config.keep_resident_headroom_gb",
            "backend_config.preload_headroom_gb",
            "backend_config.gpu_runtime_overhead_gb",
        ):
            if key in override_map:
                current_candidate = apply_override(current_candidate, key, override_map[key])

        current_candidate = make_relative_model_path(current_candidate, CURRENT_STATE_CANDIDATE_PATH)
        CURRENT_STATE_CANDIDATE_PATH.write_text(
            yaml.safe_dump(current_candidate, sort_keys=False),
            encoding="utf-8",
        )
        current_state_candidate_path = str(CURRENT_STATE_CANDIDATE_PATH)
    elif CURRENT_STATE_CANDIDATE_PATH.exists():
        CURRENT_STATE_CANDIDATE_PATH.unlink()

    payload = {
        "updated_at": utc_now().isoformat(),
        "base_candidate_path": str(candidate_path),
        "sweep_summary_path": str(sweep_summary_path),
        "repeat": args.repeat,
        "grid": grids,
        "current_state_candidate_path": current_state_candidate_path,
        "historical_best_gen_tok_s": historical_gen,
        "relative_to_historical": relative_ratio,
        "degraded_vs_historical": degraded,
        "no_valid_winner": grouped_winner is None,
        "winner": grouped_winner,
        "machine_state": machine_state,
        "refresh_skipped": False,
        "skip_reason": None,
    }
    CURRENT_STATE_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Sweep summary:          {sweep_summary_path}")
    print(f"Current-state record:   {CURRENT_STATE_JSON_PATH}")
    print(f"Current-state candidate:{current_state_candidate_path or '(none)'}")
    if grouped_winner is None:
        print("Winner:                 no valid contender")
        print("State:                  current machine state violates the benchmark guardrails")
    else:
        print(f"Winner:                 {grouped_winner['combo_key']}")
        print(f"Median gen tok/s:       {grouped_winner['median_gen_tok_s']:.2f}")
        print(f"Median prompt tok/s:    {grouped_winner['median_prompt_tok_s']:.2f}")
        print(f"Median TTFT:            {grouped_winner['median_ttft_ms']:.1f} ms")
        if historical_gen > 0:
            print(f"Relative to historical: {relative_ratio:.2%}")
        if degraded:
            print("State:                  degraded vs historical best")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
