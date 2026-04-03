"""
Run a small override sweep against the benchmark harness and summarize the results.

Usage:
    uv run sweep.py \
      --label threads-batch-probe \
      --grid backend_config.threads_batch=13,14,15 \
      --override measured_runs=1
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil
import yaml


ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
SWEEPS_DIR = RESULTS_DIR / "sweeps"
TRAIN_SCRIPT = ROOT / "train.py"
DEFAULT_CANDIDATE = ROOT / "candidate.yaml"


@dataclass
class SweepResult:
    run_index: int
    repeat_index: int
    run_id: str
    status: str
    score: float
    gen_tok_s: float
    prompt_tok_s: float
    ttft_ms: float
    load_s: float
    min_free_gb: float
    swap_delta_gb: float
    artifact_path: str
    overrides: list[str]
    combo: dict[str, Any]
    stdout: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate",
        default=str(DEFAULT_CANDIDATE),
        help="Base candidate YAML file.",
    )
    parser.add_argument(
        "--label",
        required=True,
        help="Short label for the sweep artifact names.",
    )
    parser.add_argument(
        "--grid",
        action="append",
        default=[],
        metavar="PATH=V1,V2,V3",
        help="Grid dimension to sweep. Repeat for multiple dimensions.",
    )
    parser.add_argument(
        "--override",
        action="append",
        default=[],
        metavar="PATH=VALUE",
        help="Override applied to every run in the sweep.",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Regenerate progress.png once after the sweep completes.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="How many times to repeat each grid point. Repeats are interleaved round-robin.",
    )
    return parser.parse_args()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_scalar(raw_value: str) -> Any:
    return yaml.safe_load(raw_value)


def parse_grid_spec(spec: str) -> tuple[str, list[Any]]:
    if "=" not in spec:
        raise ValueError(f"Invalid grid spec (missing '='): {spec}")
    path, raw_values = spec.split("=", 1)
    path = path.strip()
    if not path:
        raise ValueError(f"Invalid grid path: {spec}")
    values = [segment.strip() for segment in raw_values.split(",") if segment.strip()]
    if not values:
        raise ValueError(f"Grid spec must include at least one value: {spec}")
    return path, [parse_scalar(value) for value in values]


def format_override(path: str, value: Any) -> str:
    return f"{path}={json.dumps(value)}"


def load_candidate(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a top-level mapping.")
    return data


def apply_override_map(candidate: dict[str, Any], overrides: list[str]) -> dict[str, Any]:
    updated = json.loads(json.dumps(candidate))
    for override in overrides:
        if "=" not in override:
            raise ValueError(f"Invalid override (missing '='): {override}")
        dotted_path, raw_value = override.split("=", 1)
        keys = [segment.strip() for segment in dotted_path.split(".") if segment.strip()]
        if not keys:
            raise ValueError(f"Invalid override path: {override}")
        value = parse_scalar(raw_value)
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


def memory_override_fields() -> set[str]:
    return {
        "backend_config.memory_reserve_gb",
        "backend_config.keep_resident_headroom_gb",
        "backend_config.preload_headroom_gb",
        "backend_config.gpu_runtime_overhead_gb",
    }


def should_freeze_hypura_memory(candidate: dict[str, Any], base_overrides: list[str], grid_specs: list[str]) -> bool:
    backend = str(candidate.get("backend", ""))
    if backend != "hypura":
        return False

    touched_paths = set()
    for item in list(base_overrides) + list(grid_specs):
        if "=" not in item:
            continue
        path = item.split("=", 1)[0].strip()
        touched_paths.add(path)
    return not bool(touched_paths & memory_override_fields())


def compute_frozen_memory_overrides(candidate: dict[str, Any]) -> list[str]:
    min_free_gb = float(candidate.get("min_free_gb", 4.0))
    memory = psutil.virtual_memory()
    used_gb = (memory.total - memory.available) / float(1 << 30)
    reserve_gb = used_gb + min_free_gb
    keep_resident_gb = reserve_gb
    preload_gb = keep_resident_gb + 2.0
    return [
        format_override("backend_config.memory_reserve_gb", round(reserve_gb, 3)),
        format_override("backend_config.keep_resident_headroom_gb", round(keep_resident_gb, 3)),
        format_override("backend_config.preload_headroom_gb", round(preload_gb, 3)),
    ]


def build_description(label: str, base_description: str, combo: dict[str, Any]) -> str:
    parts = [f"{key.split('.')[-1]}={value}" for key, value in combo.items()]
    suffix = ", ".join(parts)
    return f"{label}: {suffix} :: {base_description}"


def extract_value(text: str, label: str) -> str:
    prefix = f"{label}:"
    for line in text.splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()
    raise ValueError(f"Missing '{label}:' in train.py output")


def run_one(
    candidate_path: Path,
    run_index: int,
    repeat_index: int,
    base_overrides: list[str],
    combo: dict[str, Any],
    description: str,
) -> SweepResult:
    overrides = list(base_overrides)
    overrides.append(format_override("description", description))
    for key, value in combo.items():
        overrides.append(format_override(key, value))

    command = [sys.executable, str(TRAIN_SCRIPT), "--candidate", str(candidate_path), "--skip-plot"]
    for override in overrides:
        command.extend(["--override", override])

    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    combined = stdout if not stderr else f"{stdout}\n{stderr}".strip()
    try:
        artifact_path = extract_value(combined, "Artifact")
    except ValueError as exc:
        raise RuntimeError(f"train.py did not produce an artifact for overrides {overrides}:\n{combined}") from exc

    artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
    summary = artifact["summary"]
    return SweepResult(
        run_index=run_index,
        repeat_index=repeat_index,
        run_id=artifact["run_id"],
        status=artifact["status"],
        score=float(summary["score"]),
        gen_tok_s=float(summary["gen_tok_s"]),
        prompt_tok_s=float(summary["prompt_tok_s"]),
        ttft_ms=float(summary["ttft_ms"]),
        load_s=float(summary["load_s"]),
        min_free_gb=float(summary["min_free_gb"]),
        swap_delta_gb=float(summary["swap_delta_gb"]),
        artifact_path=artifact_path,
        overrides=overrides,
        combo=combo,
        stdout=combined,
    )


def render_progress_chart() -> None:
    subprocess.run([sys.executable, str(ROOT / "progress.py")], cwd=ROOT, check=False)


def combo_key(combo: dict[str, Any]) -> str:
    return ", ".join(f"{key.split('.')[-1]}={value}" for key, value in combo.items())


def median_or_zero(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def group_results(results: list[SweepResult]) -> list[dict[str, Any]]:
    grouped: dict[str, list[SweepResult]] = {}
    for result in results:
        grouped.setdefault(combo_key(result.combo), []).append(result)

    summary_rows: list[dict[str, Any]] = []
    for key, group in grouped.items():
        valid = [item for item in group if item.status in {"keep", "discard"}]
        summary_rows.append(
            {
                "combo_key": key,
                "combo": group[0].combo,
                "runs": len(group),
                "valid_runs": len(valid),
                "statuses": [item.status for item in group],
                "median_gen_tok_s": median_or_zero([item.gen_tok_s for item in valid]),
                "median_prompt_tok_s": median_or_zero([item.prompt_tok_s for item in valid]),
                "median_ttft_ms": median_or_zero([item.ttft_ms for item in valid]),
                "median_load_s": median_or_zero([item.load_s for item in valid]),
                "min_free_gb": min(item.min_free_gb for item in group),
                "max_swap_delta_gb": max(item.swap_delta_gb for item in group),
                "run_ids": [item.run_id for item in group],
                "artifact_paths": [item.artifact_path for item in group],
                "overrides": [item.overrides for item in group],
            }
        )

    summary_rows.sort(
        key=lambda row: (
            0 if row["valid_runs"] > 0 else 1,
            -row["median_gen_tok_s"],
            -row["median_prompt_tok_s"],
            row["median_ttft_ms"],
        )
    )
    return summary_rows


def main() -> int:
    args = parse_args()
    if not args.grid:
        raise ValueError("At least one --grid spec is required.")
    if args.repeat < 1:
        raise ValueError("--repeat must be at least 1.")

    candidate_path = Path(args.candidate).expanduser().resolve()
    candidate = load_candidate(candidate_path)
    base_overrides = list(args.override)
    candidate_with_base = apply_override_map(candidate, base_overrides)
    if should_freeze_hypura_memory(candidate_with_base, base_overrides, args.grid):
        base_overrides.extend(compute_frozen_memory_overrides(candidate_with_base))
        candidate_with_base = apply_override_map(candidate, base_overrides)

    base_description = str(candidate_with_base.get("description", candidate_with_base.get("backend", "sweep")))

    grid_dimensions = [parse_grid_spec(spec) for spec in args.grid]
    dimension_names = [name for name, _ in grid_dimensions]
    value_matrix = [values for _, values in grid_dimensions]
    combinations = [
        dict(zip(dimension_names, values, strict=True))
        for values in itertools.product(*value_matrix)
    ]

    SWEEPS_DIR.mkdir(parents=True, exist_ok=True)
    started_at = utc_now()
    slug = "".join(char if char.isalnum() else "-" for char in args.label.lower()).strip("-") or "sweep"
    sweep_id = f"{started_at.strftime('%Y%m%dT%H%M%SZ')}-{slug}"

    total_runs = len(combinations) * args.repeat
    scheduled_runs = [
        (repeat_index, combo)
        for repeat_index in range(1, args.repeat + 1)
        for combo in combinations
    ]

    results: list[SweepResult] = []
    for run_index, (repeat_index, combo) in enumerate(scheduled_runs, start=1):
        description = build_description(
            args.label,
            base_description,
            {**combo, "repeat": repeat_index} if args.repeat > 1 else combo,
        )
        result = run_one(
            candidate_path=candidate_path,
            run_index=run_index,
            repeat_index=repeat_index,
            base_overrides=list(base_overrides),
            combo=combo,
            description=description,
        )
        results.append(result)
        print(
            f"[{run_index}/{total_runs}] {result.run_id} "
            f"status={result.status} gen={result.gen_tok_s:.2f} "
            f"prompt={result.prompt_tok_s:.2f} ttft={result.ttft_ms:.1f}ms "
            f"combo={combo_key(combo)} repeat={repeat_index}/{args.repeat}",
            flush=True,
        )

    if args.plot:
        render_progress_chart()

    summary_rows = [
        {
            "run_index": result.run_index,
            "repeat_index": result.repeat_index,
            "run_id": result.run_id,
            "status": result.status,
            "score": result.score,
            "gen_tok_s": result.gen_tok_s,
            "prompt_tok_s": result.prompt_tok_s,
            "ttft_ms": result.ttft_ms,
            "load_s": result.load_s,
            "min_free_gb": result.min_free_gb,
            "swap_delta_gb": result.swap_delta_gb,
            "artifact_path": result.artifact_path,
            "combo": result.combo,
            "overrides": result.overrides,
        }
        for result in results
    ]

    summary_rows.sort(
        key=lambda row: (
            0 if row["status"] in {"keep", "discard"} else 1,
            -row["gen_tok_s"],
            -row["prompt_tok_s"],
            row["ttft_ms"],
        )
    )

    summary_payload = {
        "sweep_id": sweep_id,
        "started_at": started_at.isoformat(),
        "finished_at": utc_now().isoformat(),
        "candidate_path": str(candidate_path),
        "base_overrides": list(base_overrides),
        "grid": args.grid,
        "repeat": args.repeat,
        "results": summary_rows,
        "grouped_results": group_results(results),
    }

    json_path = SWEEPS_DIR / f"{sweep_id}.json"
    tsv_path = SWEEPS_DIR / f"{sweep_id}.tsv"
    json_path.write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")

    lines = [
        "\t".join(
            [
                "run_index",
                "repeat_index",
                "run_id",
                "status",
                "gen_tok_s",
                "prompt_tok_s",
                "ttft_ms",
                "load_s",
                "min_free_gb",
                "swap_delta_gb",
                "artifact_path",
                "overrides",
            ]
        )
    ]
    for row in summary_rows:
        lines.append(
            "\t".join(
                [
                    str(row["run_index"]),
                    str(row["repeat_index"]),
                    str(row["run_id"]),
                    str(row["status"]),
                    f"{row['gen_tok_s']:.4f}",
                    f"{row['prompt_tok_s']:.4f}",
                    f"{row['ttft_ms']:.4f}",
                    f"{row['load_s']:.4f}",
                    f"{row['min_free_gb']:.4f}",
                    f"{row['swap_delta_gb']:.4f}",
                    str(row["artifact_path"]),
                    json.dumps(row["overrides"]),
                ]
            )
        )
    tsv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    grouped_tsv_path = SWEEPS_DIR / f"{sweep_id}.grouped.tsv"
    grouped_lines = [
        "\t".join(
            [
                "combo_key",
                "valid_runs",
                "runs",
                "median_gen_tok_s",
                "median_prompt_tok_s",
                "median_ttft_ms",
                "median_load_s",
                "min_free_gb",
                "max_swap_delta_gb",
                "statuses",
                "run_ids",
            ]
        )
    ]
    for row in summary_payload["grouped_results"]:
        grouped_lines.append(
            "\t".join(
                [
                    str(row["combo_key"]),
                    str(row["valid_runs"]),
                    str(row["runs"]),
                    f"{row['median_gen_tok_s']:.4f}",
                    f"{row['median_prompt_tok_s']:.4f}",
                    f"{row['median_ttft_ms']:.4f}",
                    f"{row['median_load_s']:.4f}",
                    f"{row['min_free_gb']:.4f}",
                    f"{row['max_swap_delta_gb']:.4f}",
                    json.dumps(row["statuses"]),
                    json.dumps(row["run_ids"]),
                ]
            )
        )
    grouped_tsv_path.write_text("\n".join(grouped_lines) + "\n", encoding="utf-8")

    print()
    print(f"Sweep summary: {json_path}")
    print(f"Sweep table:   {tsv_path}")
    print(f"Grouped table: {grouped_tsv_path}")
    print("Top grouped results:")
    for row in summary_payload["grouped_results"][:5]:
        print(
            f"  {row['combo_key']}: valid={row['valid_runs']}/{row['runs']} "
            f"median_gen={row['median_gen_tok_s']:.2f} "
            f"median_prompt={row['median_prompt_tok_s']:.2f} "
            f"median_ttft={row['median_ttft_ms']:.1f}ms"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
