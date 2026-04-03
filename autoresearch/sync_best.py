"""
Rebuild the exported stable best config from the recorded best artifact.

Usage:
    uv run sync_best.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
BEST_PATH = RESULTS_DIR / "best.json"
BEST_CANDIDATE_PATH = RESULTS_DIR / "best_candidate.yaml"


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


def main() -> int:
    if not BEST_PATH.exists():
        raise FileNotFoundError(f"{BEST_PATH} does not exist.")

    best = load_json(BEST_PATH)
    artifact_path = resolve_record_path(best["artifact_path"], ROOT)
    if not artifact_path.exists():
        raise FileNotFoundError(f"Best artifact does not exist: {artifact_path}")

    artifact = load_json(artifact_path)
    candidate = dict(artifact["candidate"])
    candidate_path = resolve_record_path(artifact["candidate_path"], ROOT)
    candidate["model_path"] = resolve_model_path(candidate, candidate_path)

    BEST_CANDIDATE_PATH.write_text(
        yaml.safe_dump(candidate, sort_keys=False),
        encoding="utf-8",
    )

    best["candidate_export_path"] = str(BEST_CANDIDATE_PATH)
    best["measured_runs"] = int(candidate.get("measured_runs", best.get("measured_runs", 0)))
    BEST_PATH.write_text(json.dumps(best, indent=2), encoding="utf-8")

    print(f"Best artifact:  {artifact_path}")
    print(f"Best record:    {BEST_PATH}")
    print(f"Best candidate: {BEST_CANDIDATE_PATH}")
    print(f"Model path:     {candidate['model_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
