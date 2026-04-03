"""
One-time setup for the Gemma 4 Mac runtime research loop.

Usage:
    uv run prepare.py
    uv run prepare.py --skip-hypura-build
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent
RESULTS_DIR = ROOT / "results"
RUNS_DIR = RESULTS_DIR / "runs"
PROFILE_PATH = RESULTS_DIR / "machine_profile.json"
TSV_PATH = ROOT / "results.tsv"
DEFAULT_MODEL_PATH = WORKSPACE_ROOT / "models" / "gemma-4-26B-A4B-it-Q4_K_M.gguf"
LLAMA_PATH = WORKSPACE_ROOT / "llama-baseline-build" / "bin" / "llama-cli"
HYPURA_MANIFEST = WORKSPACE_ROOT / "hypura-main" / "Cargo.toml"
HYPURA_RELEASE = WORKSPACE_ROOT / "hypura-main" / "target" / "release" / "hypura"

TSV_HEADER = (
    "run_id\ttime\tbackend\tstatus\tscore\tgen_tok_s\tprompt_tok_s\t"
    "ttft_ms\tload_s\tmin_free_gb\tswap_delta_gb\tdescription\n"
)


@dataclass
class BackendProfile:
    name: str
    command: list[str]
    path: str


@dataclass
class MachineProfile:
    generated_at: str
    hostname: str
    platform: str
    os_version: str
    machine: str
    total_memory_bytes: int
    total_memory_gb: float
    performance_cores: int
    efficiency_cores: int
    model_path: str
    llama_cpp: BackendProfile | None
    hypura: BackendProfile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        default=str(DEFAULT_MODEL_PATH),
        help="Path to the Gemma GGUF file to optimize.",
    )
    parser.add_argument(
        "--skip-hypura-build",
        action="store_true",
        help="Do not build Hypura in release mode if the binary is missing.",
    )
    return parser.parse_args()


def shell(cmd: list[str], cwd: Path | None = None) -> str:
    completed = subprocess.run(
        cmd,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def sysctl_int(name: str, default: int = 0) -> int:
    try:
        return int(shell(["sysctl", "-n", name]))
    except Exception:
        return default


def ensure_hypura_binary(skip_build: bool) -> Path:
    if HYPURA_RELEASE.exists():
        return HYPURA_RELEASE

    if skip_build:
        raise FileNotFoundError(
            f"Hypura release binary not found at {HYPURA_RELEASE}. "
            "Run without --skip-hypura-build to build it."
        )

    if not HYPURA_MANIFEST.exists():
        raise FileNotFoundError(f"Hypura manifest not found at {HYPURA_MANIFEST}")

    print("Building Hypura release binary...")
    subprocess.run(
        ["cargo", "build", "--manifest-path", str(HYPURA_MANIFEST), "--release"],
        cwd=WORKSPACE_ROOT,
        check=True,
    )
    if not HYPURA_RELEASE.exists():
        raise FileNotFoundError(f"Hypura release binary still missing at {HYPURA_RELEASE}")
    return HYPURA_RELEASE


def verify_backend(command: list[str]) -> None:
    subprocess.run(
        command + ["--help"],
        cwd=WORKSPACE_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )


def build_profile(model_path: Path, hypura_path: Path, llama_path: Path | None) -> MachineProfile:
    total_memory_bytes = sysctl_int("hw.memsize")
    performance_cores = sysctl_int("hw.perflevel0.physicalcpu")
    efficiency_cores = sysctl_int("hw.perflevel1.physicalcpu")

    return MachineProfile(
        generated_at=datetime.now(timezone.utc).isoformat(),
        hostname=platform.node(),
        platform=platform.platform(),
        os_version=platform.mac_ver()[0] or platform.release(),
        machine=platform.machine(),
        total_memory_bytes=total_memory_bytes,
        total_memory_gb=total_memory_bytes / float(1 << 30),
        performance_cores=performance_cores,
        efficiency_cores=efficiency_cores,
        model_path=str(model_path),
        llama_cpp=(
            BackendProfile(
                name="llama.cpp",
                command=[str(llama_path)],
                path=str(llama_path),
            )
            if llama_path is not None
            else None
        ),
        hypura=BackendProfile(
            name="hypura",
            command=[str(hypura_path)],
            path=str(hypura_path),
        ),
    )


def ensure_layout() -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    RUNS_DIR.mkdir(exist_ok=True)
    if not TSV_PATH.exists():
        TSV_PATH.write_text(TSV_HEADER, encoding="utf-8")


def main() -> int:
    args = parse_args()
    model_path = Path(args.model).expanduser().resolve()

    if not model_path.exists():
        print(f"Model file not found: {model_path}", file=sys.stderr)
        return 1

    llama_path: Path | None = LLAMA_PATH if LLAMA_PATH.exists() else None

    try:
        hypura_path = ensure_hypura_binary(args.skip_hypura_build)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        if llama_path is not None:
            verify_backend([str(llama_path)])
        verify_backend([str(hypura_path)])
    except subprocess.CalledProcessError as exc:
        print(f"Backend verification failed: {exc}", file=sys.stderr)
        return 1

    ensure_layout()
    profile = build_profile(model_path, hypura_path, llama_path)
    PROFILE_PATH.write_text(json.dumps(asdict(profile), indent=2), encoding="utf-8")

    print("Gemma runtime research loop is ready.")
    print(f"Model:   {profile.model_path}")
    print(f"Memory:  {profile.total_memory_gb:.1f} GB unified")
    print(
        f"Cores:   {profile.performance_cores} performance + "
        f"{profile.efficiency_cores} efficiency"
    )
    if profile.llama_cpp is not None:
        print(f"Llama:   {profile.llama_cpp.path}")
    else:
        print("Llama:   optional baseline not present in this checkout")
    print(f"Hypura:  {profile.hypura.path}")
    print(f"Profile: {PROFILE_PATH}")
    print(f"Results: {TSV_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
