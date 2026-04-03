#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SIZE_BUDGET_MB = 100.0
PRIVATE_MARKERS = [
    "/Users/scasella/Downloads/gemma",
    "/Users/scasella/Downloads/gemma-release-repo",
]
TEXT_SUFFIXES = {
    ".md", ".py", ".sh", ".yaml", ".yml", ".json", ".toml", ".lock", ".txt",
    ".rs", ".c", ".cc", ".cpp", ".h", ".hpp", ".cmake", ".in", ".cfg",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit this repo for lean public-release shape.")
    parser.add_argument("--json", action="store_true", help="Print structured JSON instead of human-readable output.")
    return parser.parse_args()


def load_curated_manifest() -> dict:
    manifest_path = ROOT / "autoresearch" / "results" / "curated_results_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"missing curated results manifest: {manifest_path.relative_to(ROOT)}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def load_layout_manifest() -> dict:
    manifest_path = ROOT / "lean_repo_layout_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"missing lean layout manifest: {manifest_path.relative_to(ROOT)}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def repo_files() -> list[Path]:
    return [path for path in ROOT.rglob("*") if path.is_file() and ".git" not in path.parts]


def total_size_mb(paths: list[Path]) -> float:
    return sum(path.stat().st_size for path in paths) / (1024 * 1024)


def largest_files(paths: list[Path], limit: int = 5) -> list[dict[str, object]]:
    ranked = sorted(paths, key=lambda path: path.stat().st_size, reverse=True)[:limit]
    return [
        {
            "path": str(path.relative_to(ROOT)),
            "size_mb": round(path.stat().st_size / (1024 * 1024), 2),
        }
        for path in ranked
    ]


def direct_entries(path: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    dirs: set[str] = set()
    if not path.exists():
        return files, dirs
    for entry in path.iterdir():
        if entry.name == ".git":
            continue
        if entry.is_file():
            files.add(entry.name)
        elif entry.is_dir():
            dirs.add(entry.name)
    return files, dirs


def compare_entries(actual: set[str], expected: set[str], optional: set[str] | None = None) -> tuple[list[str], list[str]]:
    optional = optional or set()
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected - optional)
    return missing, unexpected


def find_layout_issues(layout_manifest: dict) -> dict[str, list[str]]:
    issues: dict[str, list[str]] = {}

    root_files, root_dirs = direct_entries(ROOT)
    missing, unexpected = compare_entries(
        root_files,
        set(layout_manifest.get("root_files", [])),
        set(layout_manifest.get("root_optional_files", [])),
    )
    issues["missing_root_files"] = missing
    issues["unexpected_root_files"] = unexpected
    missing, unexpected = compare_entries(root_dirs, set(layout_manifest.get("root_dirs", [])))
    issues["missing_root_dirs"] = missing
    issues["unexpected_root_dirs"] = unexpected

    docs_root = ROOT / "docs"
    docs_files, docs_dirs = direct_entries(docs_root)
    missing, unexpected = compare_entries(docs_files, set(layout_manifest.get("docs_files", [])))
    issues["missing_docs_files"] = missing
    issues["unexpected_docs_files"] = unexpected
    missing, unexpected = compare_entries(docs_dirs, set(layout_manifest.get("docs_dirs", [])))
    issues["missing_docs_dirs"] = missing
    issues["unexpected_docs_dirs"] = unexpected

    license_template_root = docs_root / "license-templates"
    license_template_files, license_template_dirs = direct_entries(license_template_root)
    missing, unexpected = compare_entries(license_template_files, set(layout_manifest.get("docs_license_template_files", [])))
    issues["missing_docs_license_template_files"] = missing
    issues["unexpected_docs_license_template_files"] = unexpected
    _, unexpected = compare_entries(license_template_dirs, set())
    issues["unexpected_docs_license_template_dirs"] = unexpected

    github_root = ROOT / ".github"
    github_files, github_dirs = direct_entries(github_root)
    _, unexpected = compare_entries(github_files, set())
    issues["unexpected_github_files"] = unexpected
    missing, unexpected = compare_entries(github_dirs, set(layout_manifest.get("github_dirs", [])))
    issues["missing_github_dirs"] = missing
    issues["unexpected_github_dirs"] = unexpected

    workflows_root = github_root / "workflows"
    workflow_files, workflow_dirs = direct_entries(workflows_root)
    missing, unexpected = compare_entries(workflow_files, set(layout_manifest.get("github_workflow_files", [])))
    issues["missing_github_workflow_files"] = missing
    issues["unexpected_github_workflow_files"] = unexpected
    _, unexpected = compare_entries(workflow_dirs, set())
    issues["unexpected_github_workflow_dirs"] = unexpected

    auto_root = ROOT / "autoresearch"
    auto_files, auto_dirs = direct_entries(auto_root)
    missing, unexpected = compare_entries(auto_files, set(layout_manifest.get("autoresearch_files", [])))
    issues["missing_autoresearch_files"] = missing
    issues["unexpected_autoresearch_files"] = unexpected
    missing, unexpected = compare_entries(auto_dirs, set(layout_manifest.get("autoresearch_dirs", [])))
    issues["missing_autoresearch_dirs"] = missing
    issues["unexpected_autoresearch_dirs"] = unexpected

    results_root = auto_root / "results"
    result_files, result_dirs = direct_entries(results_root)
    missing, unexpected = compare_entries(result_files, set(layout_manifest.get("results_files", [])))
    issues["missing_results_files"] = missing
    issues["unexpected_results_files"] = unexpected
    missing, unexpected = compare_entries(result_dirs, set(layout_manifest.get("results_dirs", [])))
    issues["missing_results_dirs"] = missing
    issues["unexpected_results_dirs"] = unexpected

    models_root = ROOT / "models"
    model_files, model_dirs = direct_entries(models_root)
    missing, unexpected = compare_entries(model_files, set(layout_manifest.get("models_files", [])))
    issues["missing_models_files"] = missing
    issues["unexpected_models_files"] = unexpected
    _, unexpected = compare_entries(model_dirs, set())
    issues["unexpected_models_dirs"] = unexpected

    hypura_root = ROOT / "hypura-main"
    hypura_files, hypura_dirs = direct_entries(hypura_root)
    missing, unexpected = compare_entries(hypura_files, set(layout_manifest.get("hypura_files", [])))
    issues["missing_hypura_files"] = missing
    issues["unexpected_hypura_files"] = unexpected
    missing, unexpected = compare_entries(hypura_dirs, set(layout_manifest.get("hypura_dirs", [])))
    issues["missing_hypura_dirs"] = missing
    issues["unexpected_hypura_dirs"] = unexpected

    return {key: value for key, value in issues.items() if value}


def find_disallowed_paths() -> list[str]:
    found: list[str] = []
    checks = [
        ROOT / "anemll-flash-llama.cpp-gemma4",
        ROOT / "llama-baseline-build",
        ROOT / "hypura-main" / "target",
        ROOT / "autoresearch" / ".venv",
        ROOT / "autoresearch" / "__pycache__",
        ROOT / "autoresearch" / "results" / "chat_sessions",
        ROOT / "autoresearch" / "results" / "auto_server_state.json",
        ROOT / "autoresearch" / "results" / "flashmoe_full_sidecar",
        ROOT / "autoresearch" / "results" / "flashmoe_smoke",
    ]
    for path in checks:
        if path.exists():
            found.append(str(path.relative_to(ROOT)))
    for path in (ROOT / "models").glob("*.gguf"):
        found.append(str(path.relative_to(ROOT)))
    for path in ROOT.rglob("__pycache__"):
        if ".git" not in path.parts:
            found.append(str(path.relative_to(ROOT)))
    return sorted(set(found))


def find_run_set_issues(expected_run_files: set[str]) -> tuple[list[str], list[str]]:
    runs_dir = ROOT / "autoresearch" / "results" / "runs"
    actual = {path.name for path in runs_dir.glob("*.json")}
    missing = sorted(expected_run_files - actual)
    unexpected = sorted(actual - expected_run_files)
    return missing, unexpected


def find_private_path_leaks(paths: list[Path]) -> list[str]:
    leaks: list[str] = []
    for path in paths:
        if path.name == "lean_repo_audit.py":
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(marker in text for marker in PRIVATE_MARKERS):
            leaks.append(str(path.relative_to(ROOT)))
    return leaks


def main() -> int:
    args = parse_args()
    errors: list[str] = []
    try:
        curated_manifest = load_curated_manifest()
    except Exception as exc:
        curated_manifest = None
        errors.append(str(exc))
    try:
        layout_manifest = load_layout_manifest()
    except Exception as exc:
        layout_manifest = None
        errors.append(str(exc))

    if errors:
        payload = {
            "ready": False,
            "errors": errors,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print("Lean repo audit")
            print()
            for error in errors:
                print(f"[todo] {error}")
            print()
            print("Lean audit: not yet")
        return 1

    expected_run_files = set(curated_manifest.get("run_artifacts", []))
    summary_files = set(curated_manifest.get("summary_files", []))
    files = repo_files()
    total_mb = round(total_size_mb(files), 2)
    disallowed_paths = find_disallowed_paths()
    missing_runs, unexpected_runs = find_run_set_issues(expected_run_files)
    private_leaks = find_private_path_leaks(files)
    largest = largest_files(files)
    curated_manifest_present = bool(expected_run_files) and bool(summary_files)

    layout_issues = find_layout_issues(layout_manifest)
    layout_manifest_present = bool(layout_manifest.get("root_files")) and bool(layout_manifest.get("root_dirs"))

    size_ok = total_mb <= SIZE_BUDGET_MB
    disallowed_ok = not disallowed_paths
    runs_ok = curated_manifest_present and not missing_runs and not unexpected_runs
    private_ok = not private_leaks
    layout_ok = layout_manifest_present and not layout_issues
    ready = all([size_ok, disallowed_ok, runs_ok, private_ok, curated_manifest_present, layout_manifest_present, layout_ok])

    payload = {
        "ready": ready,
        "repo_size_mb": total_mb,
        "size_budget_mb": SIZE_BUDGET_MB,
        "manifest_summary_files_count": len(summary_files),
        "manifest_run_artifacts_count": len(expected_run_files),
        "checks": {
            "size_within_budget": size_ok,
            "no_disallowed_paths": disallowed_ok,
            "curated_manifest_present": curated_manifest_present,
            "curated_run_set_matches": runs_ok,
            "layout_manifest_present": layout_manifest_present,
            "public_layout_matches": layout_ok,
            "no_private_path_leaks": private_ok,
        },
        "curated_manifest_path": "autoresearch/results/curated_results_manifest.json",
        "layout_manifest_path": "lean_repo_layout_manifest.json",
        "disallowed_paths": disallowed_paths,
        "missing_curated_runs": missing_runs,
        "unexpected_run_files": unexpected_runs,
        "layout_issues": layout_issues,
        "private_path_leaks": private_leaks,
        "largest_files": largest,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0 if ready else 1

    print("Lean repo audit")
    print()
    if size_ok:
        print(f"[pass] repo size is within budget: {total_mb:.2f} MB <= {SIZE_BUDGET_MB:.2f} MB")
    else:
        print(f"[todo] repo size is over budget: {total_mb:.2f} MB > {SIZE_BUDGET_MB:.2f} MB")
    if disallowed_ok:
        print("[pass] no disallowed local-only paths or bundled externals are present")
    else:
        print(f"[todo] disallowed paths are present: {' '.join(disallowed_paths)}")
    if curated_manifest_present:
        print(f"[pass] curated results manifest is present ({len(summary_files)} summary files, {len(expected_run_files)} run artifacts)")
    else:
        print("[todo] curated results manifest is missing or empty")
    if runs_ok:
        print(f"[pass] curated run set matches expected {len(expected_run_files)} files")
    else:
        details = []
        if not curated_manifest_present:
            details.append("manifest missing or empty")
        if missing_runs:
            details.append("missing=" + ",".join(missing_runs))
        if unexpected_runs:
            details.append("unexpected=" + ",".join(unexpected_runs))
        print(f"[todo] curated run set drifted: {'; '.join(details)}")
    if layout_manifest_present:
        print("[pass] lean layout manifest is present")
    else:
        print("[todo] lean layout manifest is missing or empty")
    if layout_ok:
        print("[pass] public file layout matches the expected lean toolkit shape")
    else:
        details = [f"{key}=" + ",".join(values) for key, values in sorted(layout_issues.items())]
        print(f"[todo] public file layout drifted: {'; '.join(details)}")
    if private_ok:
        print("[pass] no private workspace paths were found")
    else:
        print(f"[todo] private workspace paths were found in: {', '.join(private_leaks)}")
    print()
    print("Largest files:")
    for item in largest:
        print(f"- {item['path']}: {item['size_mb']:.2f} MB")
    print()
    if ready:
        print("Lean audit: pass")
        return 0
    print("Lean audit: not yet")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
