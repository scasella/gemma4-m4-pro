"""
Write a simple side-by-side runtime summary for the best overall path and best Flash-MoE alternate.

Usage:
    uv run runtime_comparison.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
RUNS_DIR = RESULTS_DIR / "runs"
BEST_PATH = RESULTS_DIR / "best.json"
BEST_CANDIDATE_PATH = RESULTS_DIR / "best_candidate.yaml"
OUTPUT_MD = RESULTS_DIR / "runtime_comparison.md"
OUTPUT_JSON = RESULTS_DIR / "runtime_comparison.json"
FLASHMOE_SERVER_PROBE_PATH = RESULTS_DIR / "flashmoe_server_probe_latest.json"
HYPURA_SERVER_PROBE_PATH = RESULTS_DIR / "hypura_server_probe_latest.json"


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


def backend_line(candidate: dict[str, Any], key: str) -> Any:
    return (candidate.get("backend_config") or {}).get(key, "")


def best_summary_value(summary: dict[str, Any], key: str) -> float:
    return float(summary.get(key, 0.0))


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


def make_payload() -> dict[str, Any]:
    best = load_json(BEST_PATH)
    best_artifact = load_json(resolve_record_path(best["artifact_path"], ROOT))
    best_candidate_path = resolve_record_path(
        best_artifact.get("candidate_path", BEST_CANDIDATE_PATH),
        ROOT,
    )
    best_candidate = normalize_candidate(
        dict(best_artifact.get("candidate", load_yaml(BEST_CANDIDATE_PATH))),
        best_candidate_path,
    )

    flashmoe_best = best_backend_run("flashmoe")
    if flashmoe_best is None:
        raise FileNotFoundError("No valid Flash-MoE run with at least three measured passes was found.")
    flashmoe_server_best = best_backend_run("flashmoe_server")

    flash_candidate_path = resolve_record_path(
        flashmoe_best.get("candidate_path", ROOT / "candidates" / "flashmoe-slot-bank-16.yaml"),
        ROOT,
    )
    flash_candidate = normalize_candidate(dict(flashmoe_best.get("candidate", {})), flash_candidate_path)

    payload = {
        "overall_best": {
            "run_id": best["run_id"],
            "backend": best["backend"],
            "artifact_path": best["artifact_path"],
            "summary": best_artifact["summary"],
            "candidate": best_candidate,
        },
        "flashmoe_alternate": {
            "run_id": flashmoe_best["run_id"],
            "backend": flashmoe_best["backend"],
            "artifact_path": flashmoe_best["artifact_path"],
            "summary": flashmoe_best["summary"],
            "candidate": flash_candidate,
        },
    }
    if flashmoe_server_best is not None:
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
        payload["flashmoe_server_alternate"] = {
            "run_id": flashmoe_server_best["run_id"],
            "backend": flashmoe_server_best["backend"],
            "artifact_path": flashmoe_server_best["artifact_path"],
            "summary": flashmoe_server_best["summary"],
            "candidate": flash_server_candidate,
        }
    if FLASHMOE_SERVER_PROBE_PATH.exists():
        payload["flashmoe_server_probe"] = load_json(FLASHMOE_SERVER_PROBE_PATH)
    if HYPURA_SERVER_PROBE_PATH.exists():
        payload["hypura_server_probe"] = load_json(HYPURA_SERVER_PROBE_PATH)
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    overall = payload["overall_best"]
    flash = payload["flashmoe_alternate"]
    o_sum = overall["summary"]
    f_sum = flash["summary"]
    o_cand = overall["candidate"]
    f_cand = flash["candidate"]
    flash_server_alt = payload.get("flashmoe_server_alternate")
    flash_server = payload.get("flashmoe_server_probe")
    hypura_server = payload.get("hypura_server_probe")

    lines = [
        "# Gemma 4 Runtime Comparison",
        "",
        "## Bottom line",
        "",
        f"- Fastest overall: `{overall['backend']}` at about `{best_summary_value(o_sum, 'gen_tok_s'):.2f}` tokens/second",
        f"- Lower-pressure alternate: `flashmoe` at about `{best_summary_value(f_sum, 'gen_tok_s'):.2f}` tokens/second in one-shot mode",
        "",
        "## Side by side",
        "",
        "| Runtime | Usage style | Generation speed | Prompt speed | First answer | Load time | Lowest free memory | Swap growth |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| {overall['backend']} | persistent server | {best_summary_value(o_sum, 'gen_tok_s'):.2f} tok/s | "
            f"{best_summary_value(o_sum, 'prompt_tok_s'):.2f} tok/s | {best_summary_value(o_sum, 'ttft_ms'):.1f} ms | "
            f"{best_summary_value(o_sum, 'load_s'):.2f} s | {best_summary_value(o_sum, 'min_free_gb'):.2f} GB | "
            f"{best_summary_value(o_sum, 'swap_delta_gb'):.2f} GB |"
        ),
        (
            f"| flashmoe | one-shot CLI | {best_summary_value(f_sum, 'gen_tok_s'):.2f} tok/s | "
            f"{best_summary_value(f_sum, 'prompt_tok_s'):.2f} tok/s | {best_summary_value(f_sum, 'ttft_ms'):.1f} ms | "
            f"{best_summary_value(f_sum, 'load_s'):.2f} s | {best_summary_value(f_sum, 'min_free_gb'):.2f} GB | "
            f"{best_summary_value(f_sum, 'swap_delta_gb'):.2f} GB |"
        ),
    ]
    if flash_server_alt:
        fs_sum = flash_server_alt["summary"]
        lines.append(
            f"| flashmoe_server | resident server benchmark | {best_summary_value(fs_sum, 'gen_tok_s'):.2f} tok/s | "
            f"{best_summary_value(fs_sum, 'prompt_tok_s'):.2f} tok/s | {best_summary_value(fs_sum, 'ttft_ms'):.1f} ms | "
            f"{best_summary_value(fs_sum, 'load_s'):.2f} s | {best_summary_value(fs_sum, 'min_free_gb'):.2f} GB | "
            f"{best_summary_value(fs_sum, 'swap_delta_gb'):.2f} GB |"
        )
    lines.extend([
        "",
        "## Best overall config",
        "",
        f"- Backend: `{overall['backend']}`",
        f"- Run: `{overall['run_id']}`",
        f"- Context: `{o_cand.get('context', '')}`",
        f"- Threads: `{backend_line(o_cand, 'threads')}`",
        f"- Prompt threads: `{backend_line(o_cand, 'threads_batch')}`",
        f"- Batch: `{backend_line(o_cand, 'batch_size')}`",
        f"- Micro-batch: `{backend_line(o_cand, 'ubatch_size')}`",
        f"- Artifact: `{overall['artifact_path']}`",
        "",
        "## Best Flash-MoE alternate",
        "",
        "- Backend: `flashmoe`",
        f"- Run: `{flash['run_id']}`",
        f"- Context: `{f_cand.get('context', '')}`",
        f"- Slot bank: `{backend_line(f_cand, 'moe_slot_bank')}`",
        f"- GPU layers: `{backend_line(f_cand, 'gpu_layers')}`",
        f"- Threads: `{backend_line(f_cand, 'threads')}`",
        f"- Prompt threads: `{backend_line(f_cand, 'threads_batch')}`",
        f"- Batch: `{backend_line(f_cand, 'batch_size')}`",
        f"- Micro-batch: `{backend_line(f_cand, 'ubatch_size')}`",
        f"- Artifact: `{flash['artifact_path']}`",
        "",
    ])

    if flash_server_alt:
        fs_cand = flash_server_alt["candidate"]
        lines.extend(
            [
                "## Best Flash-MoE resident-server benchmark",
                "",
                f"- Backend: `{flash_server_alt['backend']}`",
                f"- Run: `{flash_server_alt['run_id']}`",
                f"- Context: `{fs_cand.get('context', '')}`",
                f"- Slot bank: `{backend_line(fs_cand, 'moe_slot_bank')}`",
                f"- GPU layers: `{backend_line(fs_cand, 'gpu_layers')}`",
                f"- Threads: `{backend_line(fs_cand, 'threads')}`",
                f"- Prompt threads: `{backend_line(fs_cand, 'threads_batch')}`",
                f"- Batch: `{backend_line(fs_cand, 'batch_size')}`",
                f"- Micro-batch: `{backend_line(fs_cand, 'ubatch_size')}`",
                f"- Parallel slots: `{backend_line(fs_cand, 'parallel')}`",
                f"- Prompt cache: `{backend_line(fs_cand, 'cache_prompt')}`",
                f"- Artifact: `{flash_server_alt['artifact_path']}`",
                "",
            ]
        )

    if flash_server:
        similar = flash_server.get("similar_short_prompt_summary", {})
        repeat = flash_server.get("exact_repeat_summary", {})
        server = flash_server.get("server", {})
        lines.extend(
            [
                "## Flash-MoE as a resident server",
                "",
                f"- Probe: `{FLASHMOE_SERVER_PROBE_PATH}`",
                f"- Resident memory: about `{float(server.get('rss_gb', 0.0)):.2f} GB` RSS",
                (
                    "- Warm similar short prompts: "
                    f"`{float(similar.get('median_elapsed_s', 0.0)):.3f} s` wall time, "
                    f"`{float(similar.get('median_prompt_per_second', 0.0)):.2f}` prompt tok/s, "
                    f"`{float(similar.get('median_predicted_per_second', 0.0)):.2f}` generation tok/s"
                ),
                (
                    "- Exact repeated prompt: "
                    f"`{float(repeat.get('median_elapsed_s', 0.0)):.3f} s` wall time, "
                    f"`{float(repeat.get('median_prompt_per_second', 0.0)):.2f}` prompt tok/s, "
                    f"`{float(repeat.get('median_predicted_per_second', 0.0)):.2f}` generation tok/s"
                ),
                "- Interpretation: Flash-MoE is still slow as a fresh one-shot process, but as a resident server it stays much lighter in memory and can answer short warm prompts reasonably quickly.",
                "",
            ]
        )

    if hypura_server:
        similar = hypura_server.get("similar_short_prompt_summary", {})
        repeat = hypura_server.get("exact_repeat_summary", {})
        server = hypura_server.get("server", {})
        lines.extend(
            [
                "## Hypura as a resident server",
                "",
                f"- Probe: `{HYPURA_SERVER_PROBE_PATH}`",
                f"- Resident memory: about `{float(server.get('rss_gb', 0.0)):.2f} GB` RSS",
                (
                    "- Warm similar short prompts: "
                    f"`{float(similar.get('median_elapsed_s', 0.0)):.3f} s` wall time, "
                    f"`{float(similar.get('median_prompt_per_second', 0.0)):.2f}` prompt tok/s, "
                    f"`{float(similar.get('median_predicted_per_second', 0.0)):.2f}` generation tok/s"
                ),
                (
                    "- Exact repeated prompt: "
                    f"`{float(repeat.get('median_elapsed_s', 0.0)):.3f} s` wall time, "
                    f"`{float(repeat.get('median_prompt_per_second', 0.0)):.2f}` prompt tok/s, "
                    f"`{float(repeat.get('median_predicted_per_second', 0.0)):.2f}` generation tok/s"
                ),
                "- Interpretation: Hypura is still the throughput leader and the faster path on exact repeated prompts, but it keeps much more memory resident than Flash-MoE.",
                "",
            ]
        )

    lines.extend(
        [
            "## Practical reading",
            "",
            "- Hypura is still the clear choice if you want the highest raw speed and you can spare the extra resident memory.",
            "- Flash-MoE resident server is the lighter always-on option. On this fresh probe it used much less memory and came back sooner on the two short non-identical prompts, but its steady-state generation speed stayed far lower.",
            "",
            "## When to use which",
            "",
            f"- Use `{overall['backend']}` when speed matters most and you are comfortable keeping more memory tied up.",
            "- Use `flashmoe` one-shot when you want a roomier fallback and do not care about startup cost.",
            "- Use `flashmoe_server` when you want a resident lower-memory alternate without leaning on exact-prompt cache wins.",
            "- Use the Flash-MoE shell server path when you want the lower-memory alternate and plan to ask more than one question in the same session.",
            "",
        ]
    )
    return "\n".join(lines)


def refresh_outputs() -> dict[str, Any]:
    payload = make_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")
    return payload


def main() -> int:
    refresh_outputs()
    print(f"Wrote {OUTPUT_MD}")
    print(f"Wrote {OUTPUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
