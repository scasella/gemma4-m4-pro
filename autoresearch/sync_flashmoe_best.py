"""
Rebuild the exported stable Flash-MoE alternate config from recorded Flash-MoE runs.

Usage:
    uv run sync_flashmoe_best.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
RUNS_DIR = RESULTS_DIR / "runs"
BEST_FLASHMOE_PATH = RESULTS_DIR / "best_flashmoe.json"
BEST_FLASHMOE_CANDIDATE_PATH = RESULTS_DIR / "best_flashmoe_candidate.yaml"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_record_path(value: str | Path, base: Path) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = (base / path).resolve()
    return path


def resolve_model_path(candidate: dict[str, Any], candidate_path: Path) -> str:
    model_value = str(candidate["model_path"])
    model_path = Path(model_value).expanduser()
    if not model_path.is_absolute():
        model_path = (candidate_path.parent / model_path).resolve()
    return str(model_path)


def best_flashmoe_run(require_measured_runs: int = 3) -> tuple[dict[str, Any], Path] | None:
    best: tuple[dict[str, Any], Path] | None = None

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
        if artifact.get("backend") != "flashmoe":
            continue
        if artifact.get("status") not in {"keep", "discard"}:
            continue
        candidate = artifact.get("candidate", {})
        if int(candidate.get("measured_runs", 0)) < require_measured_runs:
            continue
        if best is None or key_for(artifact) > key_for(best[0]):
            best = (artifact, artifact_path)
    return best


def main() -> int:
    best = best_flashmoe_run()
    if best is None:
        raise FileNotFoundError("No valid Flash-MoE run with at least three measured passes was found.")

    artifact, artifact_path = best
    candidate = dict(artifact["candidate"])
    candidate_path = resolve_record_path(artifact["candidate_path"], ROOT)
    candidate["model_path"] = resolve_model_path(candidate, candidate_path)

    BEST_FLASHMOE_CANDIDATE_PATH.write_text(
        yaml.safe_dump(candidate, sort_keys=False),
        encoding="utf-8",
    )

    record = {
        "run_id": artifact["run_id"],
        "backend": artifact["backend"],
        "artifact_path": str(artifact_path),
        "candidate_path": str(candidate_path),
        "candidate_export_path": str(BEST_FLASHMOE_CANDIDATE_PATH),
        "summary": artifact["summary"],
        "measured_runs": int(candidate.get("measured_runs", 0)),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    BEST_FLASHMOE_PATH.write_text(json.dumps(record, indent=2), encoding="utf-8")

    print(f"Best Flash-MoE artifact:  {artifact_path}")
    print(f"Best Flash-MoE record:    {BEST_FLASHMOE_PATH}")
    print(f"Best Flash-MoE candidate: {BEST_FLASHMOE_CANDIDATE_PATH}")
    print(f"Model path:               {candidate['model_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
