"""
Restore the tracked candidate file from the saved stable best config.

Usage:
    uv run restore_best.py
    uv run restore_best.py --output candidate.yaml
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parent
BEST_CANDIDATE_PATH = ROOT / "results" / "best_candidate.yaml"
DEFAULT_OUTPUT_PATH = ROOT / "candidate.yaml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Where to write the restored candidate config.",
    )
    return parser.parse_args()


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a top-level mapping.")
    return data


def normalize_mapping(data: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(data))


def normalize_for_compare(candidate: dict[str, Any], candidate_path: Path) -> dict[str, Any]:
    normalized = normalize_mapping(candidate)
    model_value = normalized.get("model_path")
    if model_value:
        model_path = Path(str(model_value)).expanduser()
        if not model_path.is_absolute():
            model_path = (candidate_path.parent / model_path).resolve()
        normalized["model_path"] = str(model_path)
    return normalized


def maybe_relativize_model_path(candidate: dict[str, Any], output_path: Path) -> dict[str, Any]:
    restored = normalize_mapping(candidate)
    model_value = restored.get("model_path")
    if not model_value:
        return restored

    model_path = Path(str(model_value)).expanduser().resolve()
    try:
        restored["model_path"] = os.path.relpath(
            str(model_path),
            start=str(output_path.parent.resolve()),
        )
    except Exception:
        restored["model_path"] = str(model_path)
    return restored


def main() -> int:
    args = parse_args()
    output_path = Path(args.output).expanduser().resolve()

    if not BEST_CANDIDATE_PATH.exists():
        raise FileNotFoundError(
            f"{BEST_CANDIDATE_PATH} does not exist. Run `uv run sync_best.py` first."
        )

    best_candidate = load_yaml(BEST_CANDIDATE_PATH)
    restored = maybe_relativize_model_path(best_candidate, output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_text = yaml.safe_dump(restored, sort_keys=False)

    if output_path.exists():
        existing = load_yaml(output_path)
        if normalize_for_compare(existing, output_path) == normalize_for_compare(
            restored,
            output_path,
        ):
            print(f"Candidate already matches the stable best: {output_path}")
            return 0

    output_path.write_text(output_text, encoding="utf-8")
    print(f"Stable best source: {BEST_CANDIDATE_PATH}")
    print(f"Restored candidate: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
