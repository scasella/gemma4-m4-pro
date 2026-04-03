#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
GEMMA_ROOT = ROOT.parent


def run_step(name: str, cmd: list[str], *, cwd: Path | None = None) -> None:
    started = time.time()
    result = subprocess.run(
        cmd,
        cwd=str(cwd or ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    duration = time.time() - started
    if result.returncode != 0:
        print(f"[fail] {name} ({duration:.2f}s)")
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip())
        raise SystemExit(1)
    print(f"[pass] {name} ({duration:.2f}s)")


def existing(paths: list[Path]) -> list[Path]:
    return [path for path in paths if path.exists()]


def main() -> int:
    print("Release readiness check")
    print()

    python_files = [
        ROOT / "prepare.py",
        ROOT / "train.py",
        ROOT / "show_best.py",
        ROOT / "gemma4_chat.py",
        ROOT / "refresh_current_state.py",
        ROOT / "runtime_comparison.py",
        ROOT / "streaming_regression_smoke.py",
    ]
    run_step(
        "python syntax",
        [sys.executable, "-m", "py_compile", *[str(path) for path in python_files]],
    )

    shell_files = existing([
        ROOT / "gemma4_answer.sh",
        ROOT / "gemma4_server_start.sh",
        ROOT / "gemma4_server_status.sh",
        ROOT / "gemma4_server_stop.sh",
        ROOT / "flashmoe_gemma4_ask.sh",
        ROOT / "flashmoe_gemma4_serve.sh",
        GEMMA_ROOT / "publish_status.sh",
        GEMMA_ROOT / "prepare_public_push.sh",
        GEMMA_ROOT / "make_publish_ready.sh",
        GEMMA_ROOT / "install_ci_badge.sh",
        GEMMA_ROOT / "finish_public_release.sh",
        GEMMA_ROOT / "rehearse_publish_flow.sh",
        GEMMA_ROOT / "rehearsal_temp_status.sh",
        GEMMA_ROOT / "install_license.sh",
        GEMMA_ROOT / "hypura-main" / "scripts" / "ask-gemma4-m4pro.sh",
        GEMMA_ROOT / "hypura-main" / "scripts" / "serve-gemma4-m4pro.sh",
    ])
    for path in shell_files:
        run_step(
            f"shell syntax: {path.relative_to(GEMMA_ROOT)}",
            ["bash", "-n", str(path)],
        )

    run_step(
        "streaming regression smoke",
        [sys.executable, str(ROOT / "streaming_regression_smoke.py")],
    )

    run_step(
        "show_best summary",
        ["uv", "run", "show_best.py"],
    )

    print()
    print("Release readiness check passed.")
    print("  - public Python entrypoints parse")
    print("  - public shell entrypoints parse")
    print("  - streaming and cleanup regression smoke passed")
    print("  - top-level status summary still renders")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
