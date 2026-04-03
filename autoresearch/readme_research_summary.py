#!/usr/bin/env python3
"""
Build the compact research summary artifact that backs the root README charts.

Usage:
    python3 readme_research_summary.py
    python3 readme_research_summary.py --check
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
RESULTS_TSV = ROOT / "results.tsv"
MACHINE_PROFILE_PATH = RESULTS_DIR / "machine_profile.json"
BEST_PATH = RESULTS_DIR / "best.json"
BEST_FLASHMOE_PATH = RESULTS_DIR / "best_flashmoe.json"
BEST_FLASHMOE_SERVER_PATH = RESULTS_DIR / "best_flashmoe_server.json"
HYPURA_SERVER_PROBE_PATH = RESULTS_DIR / "hypura_server_probe_latest.json"
FLASHMOE_SERVER_PROBE_PATH = RESULTS_DIR / "flashmoe_server_probe_latest.json"
OUTPUT_PATH = RESULTS_DIR / "readme_research_summary.json"

CURATED_RUN_IDS = {
    "20260402T210451Z-hypura-strict-reserve-probe",
    "20260402T212311Z-hypura-4096-combo-prompt-stable",
    "20260402T234400Z-flashmoe-slot-bank-16-cpu-dense-",
    "20260403T004039Z-flashmoe-server-slot-bank-16-cpu",
}

NUMERIC_FIELDS = {
    "score",
    "gen_tok_s",
    "prompt_tok_s",
    "ttft_ms",
    "load_s",
    "min_free_gb",
    "swap_delta_gb",
}

HYPURA_PROGRESS_SPECS = [
    {
        "run_id": "20260402T200315Z-llama-cpp-smoke-verification",
        "label": "Usable llama.cpp anchor",
        "phase": "baseline",
        "change": "Established the first working baseline for this exact laptop and model.",
        "track": "speed",
    },
    {
        "run_id": "20260402T203711Z-hypura-server-bench-smoke",
        "label": "First usable Hypura server path",
        "phase": "backend_switch",
        "change": "Moved the fast path onto the Hypura resident server route.",
        "track": "speed",
    },
    {
        "run_id": "20260402T210451Z-hypura-strict-reserve-probe",
        "label": "Memory reserve respected",
        "phase": "memory_reserve",
        "change": "Stopped Hypura from planning as if the whole machine were free.",
        "track": "speed",
    },
    {
        "run_id": "20260402T210949Z-hypura-context-4096-stable",
        "label": "Context 4096 adopted",
        "phase": "context",
        "change": "Raised context without giving up the free-memory floor.",
        "track": "speed",
    },
    {
        "run_id": "20260402T212109Z-hypura-4096-threads-batch-14",
        "label": "Prompt-side worker tuning",
        "phase": "threads_batch",
        "change": "Settled on the stronger prompt-side worker count.",
        "track": "speed",
    },
    {
        "run_id": "20260402T212130Z-hypura-4096-ubatch-256",
        "label": "Micro-batch tuning",
        "phase": "ubatch",
        "change": "Kept the faster batch shape while staying within the safety limits.",
        "track": "speed",
    },
    {
        "run_id": "20260402T212311Z-hypura-4096-combo-prompt-stable",
        "label": "Validated Hypura winner",
        "phase": "validated_winner",
        "change": "Locked the adopted three-pass winner.",
        "track": "speed",
        "adopted": True,
        "validated": True,
    },
    {
        "run_id": "20260402T214323Z-hypura-4096-tb13-probe",
        "label": "Exploratory peak",
        "phase": "exploratory_peak",
        "change": "A one-pass probe beat the adopted winner, but it did not survive follow-up stability checks.",
        "track": "speed",
        "exploratory_only": True,
    },
]

FLASHMOE_PROGRESS_SPECS = [
    {
        "run_id": "20260402T233718Z-flashmoe-slot-bank-16-smoke",
        "label": "First usable Flash-MoE smoke run",
        "phase": "smoke",
        "change": "Proved the Gemma 4 Flash-MoE route could answer correctly on this machine.",
        "track": "memory",
    },
    {
        "run_id": "20260402T234127Z-flashmoe-bank-ngl-moe-slot-bank-",
        "label": "Bank and GPU sweep winner",
        "phase": "bank_gpu_sweep",
        "change": "The best one-pass sweep favored slot-bank 16 with the dense and shared path left on the CPU.",
        "track": "memory",
        "exploratory_only": True,
    },
    {
        "run_id": "20260402T234400Z-flashmoe-slot-bank-16-cpu-dense-",
        "label": "Validated Flash-MoE CLI fallback",
        "phase": "validated_cli",
        "change": "Locked the three-pass one-shot alternate.",
        "track": "memory",
        "adopted": True,
        "validated": True,
    },
    {
        "run_id": "20260403T001746Z-flashmoe-batch-batch-size-2-ubat",
        "label": "Exploratory CLI peak",
        "phase": "cli_peak",
        "change": "Small batch tuning briefly nudged the CLI path higher, but it stayed an exploratory point.",
        "track": "memory",
        "exploratory_only": True,
    },
    {
        "run_id": "20260403T004039Z-flashmoe-server-slot-bank-16-cpu",
        "label": "Resident-server architecture shift",
        "phase": "resident_server",
        "change": "Keeping Flash-MoE hot removed most of the one-shot startup penalty and became the practical low-memory path.",
        "track": "memory",
        "adopted": True,
        "validated": True,
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=str(OUTPUT_PATH),
        help="Where to write the derived summary JSON.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if the output file is missing or stale instead of rewriting it.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_rows() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    with RESULTS_TSV.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    parsed_rows: list[dict[str, Any]] = []
    by_run_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        parsed = dict(row)
        for key in NUMERIC_FIELDS:
            value = parsed.get(key, "")
            parsed[key] = float(value) if value not in {"", None} else None
        parsed_rows.append(parsed)
        by_run_id[parsed["run_id"]] = parsed
    return parsed_rows, by_run_id


def metric_round(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def ratio(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 2)


def pct_gain(newer: float, older: float) -> float:
    return round(((newer - older) / older) * 100.0, 2)


def absolute_delta(newer: float, older: float) -> float:
    return round(newer - older, 2)


def evidence_path(run_id: str) -> str:
    if run_id in CURATED_RUN_IDS:
        return f"results/runs/{run_id}.json"
    return "results.tsv"


def milestone_from_row(
    rows_by_run_id: dict[str, dict[str, Any]],
    spec: dict[str, Any],
) -> dict[str, Any]:
    row = rows_by_run_id.get(spec["run_id"])
    if row is None:
        raise FileNotFoundError(f"run_id missing from results.tsv: {spec['run_id']}")
    return {
        "run_id": row["run_id"],
        "backend": row["backend"],
        "label": spec["label"],
        "phase": spec["phase"],
        "change": spec["change"],
        "description": row["description"],
        "status": row["status"],
        "track": spec.get("track"),
        "adopted": bool(spec.get("adopted", False)),
        "validated": bool(spec.get("validated", False)),
        "exploratory_only": bool(spec.get("exploratory_only", False)),
        "evidence": evidence_path(row["run_id"]),
        "metrics": {
            "gen_tok_s": metric_round(row["gen_tok_s"]),
            "prompt_tok_s": metric_round(row["prompt_tok_s"]),
            "ttft_ms": metric_round(row["ttft_ms"]),
            "load_s": metric_round(row["load_s"]),
            "min_free_gb": metric_round(row["min_free_gb"]),
            "swap_delta_gb": metric_round(row["swap_delta_gb"]),
        },
    }


def with_step_deltas(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for item in items:
        current = dict(item)
        current_metrics = dict(current["metrics"])
        if previous is None:
            current["delta_vs_previous_pct"] = None
        else:
            current["delta_vs_previous_pct"] = pct_gain(
                current_metrics["gen_tok_s"],
                previous["metrics"]["gen_tok_s"],
            )
        current["metrics"] = current_metrics
        enriched.append(current)
        previous = current
    return enriched


def probe_summary(path: Path) -> dict[str, Any]:
    probe = load_json(path)
    return {
        "updated_at": probe["updated_at"],
        "resident_rss_gb": metric_round(probe["server"]["rss_gb"]),
        "similar_short_prompts": {
            "median_elapsed_s": metric_round(probe["similar_short_prompt_summary"]["median_elapsed_s"]),
            "median_prompt_tok_s": metric_round(probe["similar_short_prompt_summary"]["median_prompt_per_second"]),
            "median_gen_tok_s": metric_round(probe["similar_short_prompt_summary"]["median_predicted_per_second"]),
        },
        "exact_repeat": {
            "median_elapsed_s": metric_round(probe["exact_repeat_summary"]["median_elapsed_s"]),
            "median_prompt_tok_s": metric_round(probe["exact_repeat_summary"]["median_prompt_per_second"]),
            "median_gen_tok_s": metric_round(probe["exact_repeat_summary"]["median_predicted_per_second"]),
        },
    }


def parse_iso_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build_payload() -> dict[str, Any]:
    rows, rows_by_run_id = load_rows()
    machine_profile = load_json(MACHINE_PROFILE_PATH)
    best = load_json(BEST_PATH)
    best_flashmoe = load_json(BEST_FLASHMOE_PATH)
    best_flashmoe_server = load_json(BEST_FLASHMOE_SERVER_PATH)

    hypura_progress = with_step_deltas(
        [milestone_from_row(rows_by_run_id, spec) for spec in HYPURA_PROGRESS_SPECS]
    )
    flashmoe_progress = with_step_deltas(
        [milestone_from_row(rows_by_run_id, spec) for spec in FLASHMOE_PROGRESS_SPECS]
    )

    llama_baseline = rows_by_run_id["20260402T200315Z-llama-cpp-smoke-verification"]
    hypura_winner = rows_by_run_id[best["run_id"]]
    flashmoe_cli = rows_by_run_id[best_flashmoe["run_id"]]
    flashmoe_server = rows_by_run_id[best_flashmoe_server["run_id"]]
    hypura_peak = rows_by_run_id["20260402T214323Z-hypura-4096-tb13-probe"]

    hypura_probe = probe_summary(HYPURA_SERVER_PROBE_PATH)
    flashmoe_probe = probe_summary(FLASHMOE_SERVER_PROBE_PATH)
    source_updated_at = max(
        [
            parse_iso_timestamp(rows[-1]["time"]),
            parse_iso_timestamp(machine_profile["generated_at"]),
            parse_iso_timestamp(best["updated_at"]),
            parse_iso_timestamp(best_flashmoe["updated_at"]),
            parse_iso_timestamp(best_flashmoe_server["updated_at"]),
            parse_iso_timestamp(load_json(HYPURA_SERVER_PROBE_PATH)["updated_at"]),
            parse_iso_timestamp(load_json(FLASHMOE_SERVER_PROBE_PATH)["updated_at"]),
        ]
    ).isoformat()

    return {
        "generated_at": source_updated_at,
        "description": "Compact research summary backing the root README progress charts and comparison claims.",
        "machine": {
            "model_path": Path(machine_profile["model_path"]).name,
            "total_memory_gb": metric_round(machine_profile["total_memory_gb"], 2),
            "performance_cores": machine_profile["performance_cores"],
            "efficiency_cores": machine_profile["efficiency_cores"],
        },
        "overall_frontier": {
            "speed_track": [
                hypura_progress[0],
                hypura_progress[1],
                hypura_progress[2],
                hypura_progress[6],
                hypura_progress[7],
            ],
            "memory_track": [
                flashmoe_progress[0],
                flashmoe_progress[2],
                flashmoe_progress[4],
            ],
        },
        "hypura_progress": hypura_progress,
        "flashmoe_progress": flashmoe_progress,
        "comparison_proof": {
            "hypura_vs_llama_cpp_baseline": {
                "lhs": "Validated Hypura winner",
                "rhs": "Usable llama.cpp baseline",
                "generation_speed_ratio": ratio(hypura_winner["gen_tok_s"], llama_baseline["gen_tok_s"]),
                "generation_speed_gain_pct": pct_gain(hypura_winner["gen_tok_s"], llama_baseline["gen_tok_s"]),
                "load_time_faster_x": ratio(llama_baseline["load_s"], hypura_winner["load_s"]),
                "free_memory_delta_gb": absolute_delta(hypura_winner["min_free_gb"], llama_baseline["min_free_gb"]),
                "swap_growth_reduction_gb": absolute_delta(llama_baseline["swap_delta_gb"], hypura_winner["swap_delta_gb"]),
            },
            "hypura_vs_flashmoe_cli": {
                "lhs": "Validated Hypura winner",
                "rhs": "Validated Flash-MoE CLI fallback",
                "generation_speed_ratio": ratio(hypura_winner["gen_tok_s"], flashmoe_cli["gen_tok_s"]),
                "prompt_speed_ratio": ratio(hypura_winner["prompt_tok_s"], flashmoe_cli["prompt_tok_s"]),
                "load_time_faster_x": ratio(flashmoe_cli["load_s"], hypura_winner["load_s"]),
                "free_memory_delta_gb": absolute_delta(hypura_winner["min_free_gb"], flashmoe_cli["min_free_gb"]),
            },
            "flashmoe_server_vs_flashmoe_cli": {
                "lhs": "Validated Flash-MoE resident server",
                "rhs": "Validated Flash-MoE CLI fallback",
                "generation_speed_ratio": ratio(flashmoe_server["gen_tok_s"], flashmoe_cli["gen_tok_s"]),
                "prompt_speed_ratio": ratio(flashmoe_server["prompt_tok_s"], flashmoe_cli["prompt_tok_s"]),
                "load_time_faster_x": ratio(flashmoe_cli["load_s"], flashmoe_server["load_s"]),
                "free_memory_delta_gb": absolute_delta(flashmoe_server["min_free_gb"], flashmoe_cli["min_free_gb"]),
            },
            "hypura_resident_vs_flashmoe_resident": {
                "lhs": "Hypura resident server",
                "rhs": "Flash-MoE resident server",
                "generation_speed_ratio": ratio(hypura_winner["gen_tok_s"], flashmoe_server["gen_tok_s"]),
                "prompt_speed_ratio": ratio(hypura_winner["prompt_tok_s"], flashmoe_server["prompt_tok_s"]),
                "resident_memory_ratio": ratio(hypura_probe["resident_rss_gb"], flashmoe_probe["resident_rss_gb"]),
                "similar_prompt_wall_time_flashmoe_advantage_x": ratio(
                    hypura_probe["similar_short_prompts"]["median_elapsed_s"],
                    flashmoe_probe["similar_short_prompts"]["median_elapsed_s"],
                ),
                "exact_repeat_wall_time_hypura_advantage_x": ratio(
                    flashmoe_probe["exact_repeat"]["median_elapsed_s"],
                    hypura_probe["exact_repeat"]["median_elapsed_s"],
                ),
            },
        },
        "resident_probe_comparison": {
            "hypura": hypura_probe,
            "flashmoe_server": flashmoe_probe,
        },
        "notes": {
            "validated_winners": {
                "hypura": {
                    "run_id": best["run_id"],
                    "measured_runs": best["measured_runs"],
                    "gen_tok_s": metric_round(hypura_winner["gen_tok_s"]),
                },
                "flashmoe_cli": {
                    "run_id": best_flashmoe["run_id"],
                    "measured_runs": best_flashmoe["measured_runs"],
                    "gen_tok_s": metric_round(flashmoe_cli["gen_tok_s"]),
                },
                "flashmoe_server": {
                    "run_id": best_flashmoe_server["run_id"],
                    "measured_runs": best_flashmoe_server["measured_runs"],
                    "gen_tok_s": metric_round(flashmoe_server["gen_tok_s"]),
                },
            },
            "exploratory_peak": {
                "run_id": hypura_peak["run_id"],
                "gen_tok_s": metric_round(hypura_peak["gen_tok_s"]),
                "delta_vs_validated_hypura_pct": pct_gain(hypura_peak["gen_tok_s"], hypura_winner["gen_tok_s"]),
                "reason_not_adopted": "The higher one-pass Hypura probe never became the official winner because the follow-up repeat and current-state passes were inconsistent.",
            },
            "supporting_files": [
                "results.tsv",
                "results/best.json",
                "results/best_flashmoe.json",
                "results/best_flashmoe_server.json",
                "results/hypura_server_probe_latest.json",
                "results/flashmoe_server_probe_latest.json",
                "results/runtime_comparison.md",
            ],
            "row_count": len(rows),
        },
    }


def render_payload() -> str:
    return json.dumps(build_payload(), indent=2) + "\n"


def main() -> int:
    args = parse_args()
    output_path = Path(args.output).expanduser().resolve()
    expected = render_payload()

    if args.check:
        if not output_path.exists():
            print(f"[fail] README research summary is missing: {output_path}")
            return 1
        current = output_path.read_text(encoding="utf-8")
        if current != expected:
            print(f"[fail] README research summary is stale: {output_path}")
            print("       Run `python3 autoresearch/readme_research_summary.py` to refresh it.")
            return 1
        print(f"[pass] README research summary is current: {output_path}")
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(expected, encoding="utf-8")
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
