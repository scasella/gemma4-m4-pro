"""
Single-run benchmark harness for Gemma 4 Mac runtime research.

Usage:
    uv run train.py
    uv run train.py --candidate candidate.yaml
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import socket
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

import psutil
import yaml


ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent
RESULTS_DIR = ROOT / "results"
RUNS_DIR = RESULTS_DIR / "runs"
PROFILE_PATH = RESULTS_DIR / "machine_profile.json"
BEST_PATH = RESULTS_DIR / "best.json"
BEST_CANDIDATE_PATH = RESULTS_DIR / "best_candidate.yaml"
TSV_PATH = ROOT / "results.tsv"
DEFAULT_CANDIDATE = ROOT / "candidate.yaml"
DEFAULT_FLASHMOE_ROOT = WORKSPACE_ROOT / "anemll-flash-llama.cpp-gemma4"
DEFAULT_FLASHMOE_BINARY = DEFAULT_FLASHMOE_ROOT / "build-smoke" / "bin" / "llama-cli"
DEFAULT_FLASHMOE_SIDECAR = RESULTS_DIR / "flashmoe_full_sidecar"

TSV_COLUMNS = [
    "run_id",
    "time",
    "backend",
    "status",
    "score",
    "gen_tok_s",
    "prompt_tok_s",
    "ttft_ms",
    "load_s",
    "min_free_gb",
    "swap_delta_gb",
    "description",
]

SANITY_CHECKS = [
    {
        "name": "sum_digit",
        "prompt": "Answer with one digit only: what is 2+2?",
        "pattern": r"^\s*4\s*$",
    },
    {
        "name": "capital_paris",
        "prompt": "Answer with one lowercase word only: what is the capital of France?",
        "pattern": r"^\s*paris\s*$",
    },
]

THROUGHPUT_PROMPT = (
    "Write a compact explanation of why sparse mixture-of-experts models can feel fast "
    "on unified-memory laptops. Keep the answer dense with plain facts about active "
    "experts, memory movement, CPU work, GPU work, and why prompt processing and "
    "generation speed can diverge."
)


@dataclass
class MemorySample:
    elapsed_s: float
    free_gb: float
    swap_used_gb: float


@dataclass
class ExecutionRecord:
    command: list[str]
    log_path: str
    wall_s: float
    exit_code: int
    stop_reason: str | None
    min_free_gb: float
    swap_delta_gb: float
    memory_samples: list[MemorySample]
    output_excerpt: dict[str, Any]
    full_output: str


@dataclass
class Metrics:
    prompt_tok_s: float
    gen_tok_s: float
    prompt_eval_s: float
    generation_s: float
    wall_s: float
    load_s: float
    ttft_ms: float
    tokens_generated: int


@dataclass
class PhaseRecord:
    phase: str
    status: str
    metrics: Metrics | None
    execution: ExecutionRecord


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate",
        default=str(DEFAULT_CANDIDATE),
        help="Path to the candidate YAML file.",
    )
    parser.add_argument(
        "--override",
        action="append",
        default=[],
        metavar="PATH=VALUE",
        help=(
            "Override one candidate field without editing the file. "
            "Use dotted paths such as backend_config.threads_batch=13. "
            "Values are parsed as YAML scalars."
        ),
    )
    parser.add_argument(
        "--skip-plot",
        action="store_true",
        help="Do not regenerate progress.png after this run.",
    )
    return parser.parse_args()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_float(value: float | None) -> str:
    if value is None or math.isnan(value):
        return ""
    return f"{value:.4f}"


def ensure_results_layout() -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    RUNS_DIR.mkdir(exist_ok=True)
    if not TSV_PATH.exists():
        with TSV_PATH.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=TSV_COLUMNS, delimiter="\t")
            writer.writeheader()


def read_machine_profile() -> dict[str, Any]:
    if not PROFILE_PATH.exists():
        raise FileNotFoundError(
            f"{PROFILE_PATH} is missing. Run `uv run prepare.py` before benchmarking."
        )
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def resolve_model_path(candidate: dict[str, Any], candidate_path: Path) -> str:
    model_value = candidate.get("model_path")
    if not model_value:
        raise ValueError("candidate.yaml must define model_path.")
    model_path = Path(str(model_value)).expanduser()
    if not model_path.is_absolute():
        model_path = (candidate_path.parent / model_path).resolve()
    return str(model_path)


def resolve_path_value(
    candidate: dict[str, Any],
    value: Any,
    default: Path | str | None = None,
) -> str | None:
    raw_value = value if value not in (None, "") else default
    if raw_value in (None, ""):
        return None
    path = Path(str(raw_value)).expanduser()
    if not path.is_absolute():
        base_dir = Path(str(candidate.get("_candidate_dir", ROOT)))
        path = (base_dir / path).resolve()
    return str(path)


def finalize_candidate(candidate: dict[str, Any], candidate_path: Path) -> dict[str, Any]:
    data = dict(candidate)
    data["_resolved_model_path"] = resolve_model_path(data, candidate_path)
    data["_candidate_path"] = str(candidate_path)
    data["_candidate_dir"] = str(candidate_path.parent.resolve())
    return data


def load_candidate(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("candidate.yaml must contain a mapping at the top level.")
    return finalize_candidate(data, path)


def apply_overrides(candidate: dict[str, Any], candidate_path: Path, overrides: list[str]) -> dict[str, Any]:
    updated = {key: value for key, value in candidate.items() if not key.startswith("_")}
    for override in overrides:
        if "=" not in override:
            raise ValueError(f"Invalid override (missing '='): {override}")
        dotted_path, raw_value = override.split("=", 1)
        keys = [segment.strip() for segment in dotted_path.split(".") if segment.strip()]
        if not keys:
            raise ValueError(f"Invalid override path: {override}")
        value = yaml.safe_load(raw_value)
        target: dict[str, Any] = updated
        for key in keys[:-1]:
            child = target.get(key)
            if child in (None, ""):
                child = {}
                target[key] = child
            if not isinstance(child, dict):
                raise ValueError(f"Override path is not a mapping: {dotted_path}")
            target = child
        target[keys[-1]] = value
    return finalize_candidate(updated, candidate_path)


def git_commit(path: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()
    except Exception:
        return None


def read_output(log_path: Path, max_bytes: int = 2_000_000) -> str:
    size = log_path.stat().st_size
    with log_path.open("rb") as handle:
        if size <= max_bytes:
            data = handle.read()
        else:
            head = handle.read(max_bytes // 2)
            handle.seek(max(size - (max_bytes // 2), 0))
            tail = handle.read(max_bytes // 2)
            data = head + b"\n...\n" + tail
    return data.decode("utf-8", errors="replace")


def summarize_output(text: str, head_lines: int = 30, tail_lines: int = 60) -> dict[str, Any]:
    lines = text.splitlines()
    head = "\n".join(lines[:head_lines])
    tail = "\n".join(lines[-tail_lines:])
    return {
        "head": head,
        "tail": tail,
        "line_count": len(lines),
    }


def median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


class BackendAdapter:
    name = ""

    def __init__(self, candidate: dict[str, Any], profile: dict[str, Any]) -> None:
        self.candidate = candidate
        self.profile = profile
        self.context = int(candidate["context"])
        self.model_path = str(candidate.get("_resolved_model_path", profile["model_path"]))
        self.max_tokens = int(candidate["max_tokens"])
        self.correctness_tokens = int(candidate.get("correctness_max_tokens", 16))

    def make_correctness_command(self, prompt: str) -> list[str]:
        raise NotImplementedError

    def make_benchmark_command(self, prompt: str) -> list[str]:
        raise NotImplementedError

    def parse_metrics(self, text: str, requested_tokens: int, wall_s: float) -> Metrics:
        raise NotImplementedError

    def extract_response_text(self, text: str) -> str:
        return text

    def run_correctness_suite(
        self,
        run_id: str,
        min_free_gb: float,
        timeout_s: int,
        sample_interval_s: float,
        max_swap_delta_gb: float,
    ) -> tuple[str, list[PhaseRecord]]:
        return run_standard_correctness_checks(
            adapter=self,
            run_id=run_id,
            min_free_gb=min_free_gb,
            timeout_s=timeout_s,
            sample_interval_s=sample_interval_s,
            max_swap_delta_gb=max_swap_delta_gb,
        )

    def run_candidate(
        self,
        run_id: str,
        min_free_gb: float,
        timeout_s: int,
        sample_interval_s: float,
        max_swap_delta_gb: float,
        warmup_runs: int,
        measured_runs: int,
    ) -> tuple[str, list[PhaseRecord]]:
        phase_records: list[PhaseRecord] = []
        status, correctness_records = self.run_correctness_suite(
            run_id=run_id,
            min_free_gb=min_free_gb,
            timeout_s=timeout_s,
            sample_interval_s=sample_interval_s,
            max_swap_delta_gb=max_swap_delta_gb,
        )
        phase_records.extend(correctness_records)

        if status == "ok":
            for index in range(warmup_runs):
                record = run_phase(
                    adapter=self,
                    run_id=run_id,
                    phase=f"warmup-{index + 1}",
                    prompt=THROUGHPUT_PROMPT,
                    min_free_gb=min_free_gb,
                    timeout_s=timeout_s,
                    sample_interval_s=sample_interval_s,
                    max_swap_delta_gb=max_swap_delta_gb,
                )
                phase_records.append(record)
                if record.status != "ok":
                    return record.status, phase_records

        if status == "ok":
            for index in range(measured_runs):
                record = run_phase(
                    adapter=self,
                    run_id=run_id,
                    phase=f"benchmark-{index + 1}",
                    prompt=THROUGHPUT_PROMPT,
                    min_free_gb=min_free_gb,
                    timeout_s=timeout_s,
                    sample_interval_s=sample_interval_s,
                    max_swap_delta_gb=max_swap_delta_gb,
                )
                phase_records.append(record)
                if record.status != "ok":
                    return record.status, phase_records

        return "ok", phase_records


class LlamaCppAdapter(BackendAdapter):
    name = "llama.cpp"

    def __init__(self, candidate: dict[str, Any], profile: dict[str, Any]) -> None:
        super().__init__(candidate, profile)
        self.binary = profile["llama_cpp"]["path"]
        self.config = candidate.get("backend_config", {})

    def _base_command(self, prompt: str, max_tokens: int, perf: bool) -> list[str]:
        cmd = [
            self.binary,
            "-m",
            self.model_path,
            "-c",
            str(self.context),
            "-n",
            str(max_tokens),
            "-p",
            prompt,
            "--simple-io",
            "--no-display-prompt",
            "-st",
            "--reasoning",
            "off",
            "--no-warmup",
            "--seed",
            "42",
            "--temp",
            "0",
            "--top-k",
            "1",
            "--top-p",
            "1.0",
        ]
        if perf:
            cmd.append("--perf")

        mapping = {
            "threads": "--threads",
            "threads_batch": "--threads-batch",
            "batch_size": "--batch-size",
            "ubatch_size": "--ubatch-size",
            "cache_type_k": "--cache-type-k",
            "cache_type_v": "--cache-type-v",
            "gpu_layers": "--gpu-layers",
            "poll": "--poll",
            "poll_batch": "--poll-batch",
        }
        for key, flag in mapping.items():
            value = self.config.get(key)
            if value not in (None, ""):
                cmd.extend([flag, str(value)])

        flash_attn = self.config.get("flash_attn")
        if flash_attn not in (None, ""):
            if isinstance(flash_attn, bool):
                flash_attn = "on" if flash_attn else "off"
            cmd.extend(["--flash-attn", str(flash_attn)])

        if self.config.get("kv_offload", True) is False:
            cmd.append("--no-kv-offload")
        if self.config.get("mmap", True) is False:
            cmd.append("--no-mmap")
        if self.config.get("mlock", False):
            cmd.append("--mlock")

        cpu_moe_value = self.config.get("cpu_moe_mode", "off")
        if isinstance(cpu_moe_value, bool):
            cpu_moe_mode = "all" if cpu_moe_value else "off"
        else:
            cpu_moe_mode = str(cpu_moe_value)
        if cpu_moe_mode == "all":
            cmd.append("--cpu-moe")
        elif cpu_moe_mode.startswith("first_"):
            cmd.extend(["--n-cpu-moe", cpu_moe_mode.split("_", 1)[1]])
        elif cpu_moe_mode.isdigit():
            cmd.extend(["--n-cpu-moe", cpu_moe_mode])

        for extra_arg in self.config.get("extra_args", []):
            cmd.append(str(extra_arg))
        return cmd

    def make_correctness_command(self, prompt: str) -> list[str]:
        return self._base_command(prompt, self.correctness_tokens, perf=False)

    def make_benchmark_command(self, prompt: str) -> list[str]:
        return self._base_command(prompt, self.max_tokens, perf=True)

    def parse_metrics(self, text: str, requested_tokens: int, wall_s: float) -> Metrics:
        prompt_tps = 0.0
        gen_tps = 0.0
        prompt_eval_s = 0.0
        load_s = 0.0
        tokens_generated = requested_tokens

        bracket = re.search(
            r"\[\s*Prompt:\s*([0-9.]+)\s*t/s\s*\|\s*Generation:\s*([0-9.]+)\s*t/s\s*\]",
            text,
        )
        if bracket:
            prompt_tps = float(bracket.group(1))
            gen_tps = float(bracket.group(2))

        load_match = re.search(r"load time\s*=\s*([0-9.]+)\s*ms", text, flags=re.I)
        if load_match:
            load_s = float(load_match.group(1)) / 1000.0

        prompt_match = re.search(
            r"prompt eval time\s*=\s*([0-9.]+)\s*ms\s*/\s*([0-9]+)\s*tokens?.*?\(\s*([0-9.]+)\s*tokens per second",
            text,
            flags=re.I | re.S,
        )
        if prompt_match:
            prompt_eval_s = float(prompt_match.group(1)) / 1000.0
            prompt_tps = float(prompt_match.group(3))

        eval_match = re.search(
            r"eval time\s*=\s*([0-9.]+)\s*ms\s*/\s*([0-9]+)\s*runs?.*?\(\s*([0-9.]+)\s*tokens per second",
            text,
            flags=re.I | re.S,
        )
        if eval_match:
            tokens_generated = int(eval_match.group(2))
            gen_tps = float(eval_match.group(3))

        generation_s = (tokens_generated / gen_tps) if gen_tps > 0 else 0.0
        if load_s <= 0.0:
            load_s = max(wall_s - prompt_eval_s - generation_s, 0.0)

        ttft_ms = (prompt_eval_s + (1.0 / gen_tps if gen_tps > 0 else 0.0)) * 1000.0
        return Metrics(
            prompt_tok_s=prompt_tps,
            gen_tok_s=gen_tps,
            prompt_eval_s=prompt_eval_s,
            generation_s=generation_s,
            wall_s=wall_s,
            load_s=load_s,
            ttft_ms=ttft_ms,
            tokens_generated=tokens_generated,
        )

    def extract_response_text(self, text: str) -> str:
        before_perf = text.split("[ Prompt:", 1)[0]
        if "\n> " in before_perf:
            segment = before_perf.rsplit("\n> ", 1)[-1]
            if "\n" in segment:
                segment = segment.split("\n", 1)[1]
        else:
            segment = before_perf

        for marker in [
            "llama_memory_breakdown_print:",
            "Exiting...",
            "~llama_context:",
        ]:
            segment = segment.split(marker, 1)[0]
        return segment.strip()


class HypuraAdapter(BackendAdapter):
    name = "hypura"

    def __init__(self, candidate: dict[str, Any], profile: dict[str, Any]) -> None:
        super().__init__(candidate, profile)
        self.binary = profile["hypura"]["path"]
        self.config = candidate.get("backend_config", {})

    def make_correctness_command(self, prompt: str) -> list[str]:
        return [
            self.binary,
            "run",
            self.model_path,
            "--context",
            str(self.context),
            "--prompt",
            prompt,
            "--max-tokens",
            str(self.correctness_tokens),
        ]

    def make_benchmark_command(self, prompt: str) -> list[str]:
        return [
            self.binary,
            "bench",
            self.model_path,
            "--context",
            str(self.context),
            "--prompt",
            prompt,
            "--max-tokens",
            str(self.max_tokens),
        ]

    def parse_metrics(self, text: str, requested_tokens: int, wall_s: float) -> Metrics:
        prompt_eval_s = 0.0
        prompt_tps = 0.0
        gen_tps = 0.0
        tokens_generated = requested_tokens

        prompt_match = re.search(r"Prompt eval:\s*([0-9.]+)s\s*\(([0-9.]+)\s*tok/s\)", text)
        if prompt_match:
            prompt_eval_s = float(prompt_match.group(1))
            prompt_tps = float(prompt_match.group(2))

        gen_match = re.search(r"Generation:\s*([0-9.]+)\s*tok/s\s*\(([0-9]+)\s*tokens\)", text)
        if gen_match:
            gen_tps = float(gen_match.group(1))
            tokens_generated = int(gen_match.group(2))

        wall_match = re.search(r"Wall time:\s*([0-9.]+)s", text)
        if wall_match:
            wall_s = float(wall_match.group(1))

        generation_s = (tokens_generated / gen_tps) if gen_tps > 0 else 0.0
        load_s = max(wall_s - prompt_eval_s - generation_s, 0.0)
        ttft_ms = (prompt_eval_s + (1.0 / gen_tps if gen_tps > 0 else 0.0)) * 1000.0
        return Metrics(
            prompt_tok_s=prompt_tps,
            gen_tok_s=gen_tps,
            prompt_eval_s=prompt_eval_s,
            generation_s=generation_s,
            wall_s=wall_s,
            load_s=load_s,
            ttft_ms=ttft_ms,
            tokens_generated=tokens_generated,
        )

    def extract_response_text(self, text: str) -> str:
        if "Generation complete:" in text:
            text = text.split("Generation complete:", 1)[0]
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines[-3:])

    def _build_server_command(self, host: str, port: int) -> list[str]:
        cmd = [
            self.binary,
            "serve",
            self.model_path,
            "--host",
            host,
            "--port",
            str(port),
            "--context",
            str(self.context),
        ]
        threads = self.config.get("threads")
        if threads not in (None, ""):
            cmd.extend(["--threads", str(threads)])
        batch = self.config.get("batch_size")
        if batch not in (None, ""):
            cmd.extend(["--batch", str(batch)])
        threads_batch = self.config.get("threads_batch")
        if threads_batch not in (None, ""):
            cmd.extend(["--threads-batch", str(threads_batch)])
        ubatch = self.config.get("ubatch_size")
        if ubatch not in (None, ""):
            cmd.extend(["--ubatch", str(ubatch)])
        return cmd

    def _float_config(self, key: str) -> float | None:
        value = self.config.get(key)
        if value in (None, ""):
            return None
        return float(value)

    def _build_server_env(self, min_free_gb: float) -> tuple[dict[str, str], dict[str, float]]:
        env = os.environ.copy()
        memory = psutil.virtual_memory()
        used_gb = (memory.total - memory.available) / float(1 << 30)

        reserve_gb = self._float_config("memory_reserve_gb")
        if reserve_gb is None:
            reserve_gb = used_gb + min_free_gb

        keep_resident_headroom_gb = self._float_config("keep_resident_headroom_gb")
        if keep_resident_headroom_gb is None:
            keep_resident_headroom_gb = reserve_gb

        preload_headroom_gb = self._float_config("preload_headroom_gb")
        if preload_headroom_gb is None:
            preload_headroom_gb = keep_resident_headroom_gb + 2.0

        env["HYPURA_MEMORY_RESERVE_GB"] = f"{reserve_gb:.3f}"
        env["HYPURA_KEEP_RESIDENT_HEADROOM_GB"] = f"{keep_resident_headroom_gb:.3f}"
        env["HYPURA_PRELOAD_HEADROOM_GB"] = f"{preload_headroom_gb:.3f}"

        gpu_runtime_overhead_gb = self._float_config("gpu_runtime_overhead_gb")
        if gpu_runtime_overhead_gb is not None:
            env["HYPURA_GPU_RUNTIME_OVERHEAD_GB"] = f"{gpu_runtime_overhead_gb:.3f}"

        env_summary = {
            "memory_reserve_gb": reserve_gb,
            "keep_resident_headroom_gb": keep_resident_headroom_gb,
            "preload_headroom_gb": preload_headroom_gb,
        }
        if gpu_runtime_overhead_gb is not None:
            env_summary["gpu_runtime_overhead_gb"] = gpu_runtime_overhead_gb

        return env, env_summary

    def _build_chat_payload(self, model_name: str, prompt: str, max_tokens: int) -> dict[str, Any]:
        return {
            "model": model_name,
            "stream": False,
            "messages": [{"role": "user", "content": prompt}],
            "options": {
                "temperature": 0.0,
                "top_k": 1,
                "top_p": 1.0,
                "num_predict": max_tokens,
                "seed": 1,
            },
        }

    def _start_server(
        self,
        run_id: str,
        min_free_gb: float,
        timeout_s: int,
        sample_interval_s: float,
        max_swap_delta_gb: float,
    ) -> tuple[PhaseRecord, subprocess.Popen[str] | None, str, str]:
        port = choose_free_port()
        host = "127.0.0.1"
        model_name = Path(self.model_path).stem
        base_url = f"http://{host}:{port}"
        command = self._build_server_command(host, port)
        env, env_summary = self._build_server_env(min_free_gb)
        log_path = RUNS_DIR / f"{run_id}-server.log"
        start = time.monotonic()
        next_sample_at = start
        low_memory_streak = 0
        stop_reason: str | None = None
        initial_swap_gb = psutil.swap_memory().used / float(1 << 30)
        memory_samples: list[MemorySample] = []

        log_handle = log_path.open("w", encoding="utf-8", errors="replace")
        try:
            process = subprocess.Popen(
                command,
                cwd=WORKSPACE_ROOT,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
        finally:
            log_handle.close()

        ready = False
        startup_timeout_s = min(timeout_s, 300)
        while True:
            now = time.monotonic()
            if now >= next_sample_at:
                free_gb = psutil.virtual_memory().available / float(1 << 30)
                swap_gb = psutil.swap_memory().used / float(1 << 30)
                memory_samples.append(
                    MemorySample(
                        elapsed_s=now - start,
                        free_gb=free_gb,
                        swap_used_gb=swap_gb,
                    )
                )
                low_memory_streak = low_memory_streak + 1 if free_gb < min_free_gb else 0
                if low_memory_streak >= 2:
                    stop_reason = "memory_floor"
                if swap_gb - initial_swap_gb > max_swap_delta_gb:
                    stop_reason = "swap_growth"
                next_sample_at = now + sample_interval_s

            if stop_reason is None and process.poll() is not None:
                stop_reason = "startup_exit"

            if stop_reason is None:
                try:
                    with urllib_request.urlopen(f"{base_url}/api/tags", timeout=2) as response:
                        if response.status == 200:
                            ready = True
                            break
                except urllib_error.URLError:
                    pass

            if stop_reason is None and (now - start) > startup_timeout_s:
                stop_reason = "startup_timeout"

            if stop_reason is not None:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                break

            time.sleep(0.1)

        wall_s = time.monotonic() - start
        final_free_gb = psutil.virtual_memory().available / float(1 << 30)
        final_swap_gb = psutil.swap_memory().used / float(1 << 30)
        memory_samples.append(
            MemorySample(
                elapsed_s=wall_s,
                free_gb=final_free_gb,
                swap_used_gb=final_swap_gb,
            )
        )

        output_text = read_output(log_path)
        output_text = (
            "=== hypura memory reserve ===\n"
            f"{json.dumps(env_summary, indent=2, sort_keys=True)}\n\n"
            f"{output_text}"
        )
        if ready:
            output_text = f"{output_text}\n\nServer ready at {base_url}\n"

        exit_code = 0 if ready else process.returncode
        execution = ExecutionRecord(
            command=command,
            log_path=str(log_path),
            wall_s=wall_s,
            exit_code=0 if exit_code is None else exit_code,
            stop_reason=stop_reason,
            min_free_gb=min(sample.free_gb for sample in memory_samples),
            swap_delta_gb=max(max(sample.swap_used_gb for sample in memory_samples) - initial_swap_gb, 0.0),
            memory_samples=memory_samples,
            output_excerpt=summarize_output(output_text),
            full_output=output_text,
        )

        status = "ok"
        if stop_reason in {"memory_floor", "swap_growth"}:
            status = "reject"
        elif not ready:
            status = "crash"

        record = PhaseRecord(
            phase="server-startup",
            status=status,
            metrics=None,
            execution=execution,
        )
        return record, (process if ready else None), base_url, model_name

    def _stop_server(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _metrics_from_chat_response(self, parsed: dict[str, Any], requested_tokens: int, wall_s: float) -> Metrics:
        prompt_count = int(parsed.get("prompt_eval_count") or 0)
        eval_count = int(parsed.get("eval_count") or requested_tokens or 0)
        prompt_eval_s = float(parsed.get("prompt_eval_duration") or 0) / 1e9
        generation_s = float(parsed.get("eval_duration") or 0) / 1e9
        total_s = float(parsed.get("total_duration") or 0) / 1e9
        load_s = float(parsed.get("load_duration") or 0) / 1e9

        prompt_tok_s = (prompt_count / prompt_eval_s) if prompt_eval_s > 0 else 0.0
        gen_tok_s = (eval_count / generation_s) if generation_s > 0 else 0.0
        if total_s <= 0.0:
            total_s = wall_s

        ttft_ms = (prompt_eval_s + (generation_s / eval_count if eval_count > 0 and generation_s > 0 else 0.0)) * 1000.0
        return Metrics(
            prompt_tok_s=prompt_tok_s,
            gen_tok_s=gen_tok_s,
            prompt_eval_s=prompt_eval_s,
            generation_s=generation_s,
            wall_s=total_s,
            load_s=load_s,
            ttft_ms=ttft_ms,
            tokens_generated=eval_count,
        )

    def _run_server_chat_phase(
        self,
        run_id: str,
        phase: str,
        prompt: str,
        max_tokens: int,
        base_url: str,
        model_name: str,
        process: subprocess.Popen[str],
        min_free_gb: float,
        timeout_s: int,
        sample_interval_s: float,
        max_swap_delta_gb: float,
    ) -> tuple[PhaseRecord, dict[str, Any] | None]:
        phase_slug = re.sub(r"[^a-z0-9]+", "-", phase.lower())
        log_path = RUNS_DIR / f"{run_id}-{phase_slug}.log"
        payload = self._build_chat_payload(model_name=model_name, prompt=prompt, max_tokens=max_tokens)
        request_obj = urllib_request.Request(
            f"{base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        start = time.monotonic()
        next_sample_at = start
        low_memory_streak = 0
        stop_reason: str | None = None
        initial_swap_gb = psutil.swap_memory().used / float(1 << 30)
        memory_samples: list[MemorySample] = []
        response_holder: dict[str, Any] = {}
        error_holder: dict[str, str] = {}

        def do_request() -> None:
            try:
                with urllib_request.urlopen(request_obj, timeout=timeout_s) as response:
                    response_holder["status"] = response.status
                    response_holder["body"] = response.read().decode("utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001
                error_holder["error"] = str(exc)

        worker = threading.Thread(target=do_request, daemon=True)
        worker.start()

        while worker.is_alive():
            now = time.monotonic()
            if now >= next_sample_at:
                free_gb = psutil.virtual_memory().available / float(1 << 30)
                swap_gb = psutil.swap_memory().used / float(1 << 30)
                memory_samples.append(
                    MemorySample(
                        elapsed_s=now - start,
                        free_gb=free_gb,
                        swap_used_gb=swap_gb,
                    )
                )
                low_memory_streak = low_memory_streak + 1 if free_gb < min_free_gb else 0
                if low_memory_streak >= 2:
                    stop_reason = "memory_floor"
                if swap_gb - initial_swap_gb > max_swap_delta_gb:
                    stop_reason = "swap_growth"
                next_sample_at = now + sample_interval_s

            if stop_reason is None and process.poll() is not None:
                stop_reason = "server_exit"

            if stop_reason is None and (now - start) > timeout_s:
                stop_reason = "timeout"

            if stop_reason is not None:
                if process.poll() is None:
                    process.kill()
                break

            time.sleep(0.1)

        worker.join(timeout=5)
        wall_s = time.monotonic() - start
        final_free_gb = psutil.virtual_memory().available / float(1 << 30)
        final_swap_gb = psutil.swap_memory().used / float(1 << 30)
        memory_samples.append(
            MemorySample(
                elapsed_s=wall_s,
                free_gb=final_free_gb,
                swap_used_gb=final_swap_gb,
            )
        )

        parsed: dict[str, Any] | None = None
        if stop_reason is None and error_holder:
            stop_reason = "request_error"

        raw_body = str(response_holder.get("body", ""))
        if stop_reason is None:
            try:
                parsed = json.loads(raw_body)
            except json.JSONDecodeError:
                stop_reason = "bad_json"

        full_output = json.dumps(
            {
                "phase": phase,
                "url": f"{base_url}/api/chat",
                "payload": payload,
                "http_status": response_holder.get("status"),
                "response": parsed if parsed is not None else raw_body,
                "error": error_holder.get("error"),
                "stop_reason": stop_reason,
            },
            indent=2,
            ensure_ascii=True,
        )
        log_path.write_text(full_output + "\n", encoding="utf-8")

        execution = ExecutionRecord(
            command=["POST", f"{base_url}/api/chat"],
            log_path=str(log_path),
            wall_s=wall_s,
            exit_code=0 if stop_reason is None else 1,
            stop_reason=stop_reason,
            min_free_gb=min(sample.free_gb for sample in memory_samples),
            swap_delta_gb=max(max(sample.swap_used_gb for sample in memory_samples) - initial_swap_gb, 0.0),
            memory_samples=memory_samples,
            output_excerpt=summarize_output(full_output),
            full_output=full_output,
        )

        if stop_reason in {"memory_floor", "swap_growth"}:
            return PhaseRecord(phase=phase, status="reject", metrics=None, execution=execution), parsed
        if stop_reason is not None:
            return PhaseRecord(phase=phase, status="crash", metrics=None, execution=execution), parsed

        metrics = self._metrics_from_chat_response(parsed or {}, max_tokens, wall_s)
        if phase.startswith("correctness"):
            return PhaseRecord(phase=phase, status="ok", metrics=None, execution=execution), parsed
        if metrics.gen_tok_s <= 0.0:
            return PhaseRecord(phase=phase, status="crash", metrics=None, execution=execution), parsed
        return PhaseRecord(phase=phase, status="ok", metrics=metrics, execution=execution), parsed

    def run_correctness_suite(
        self,
        run_id: str,
        min_free_gb: float,
        timeout_s: int,
        sample_interval_s: float,
        max_swap_delta_gb: float,
    ) -> tuple[str, list[PhaseRecord]]:
        port = choose_free_port()
        host = "127.0.0.1"
        model_name = Path(self.model_path).stem
        base_url = f"http://{host}:{port}"
        serve_command = [
            self.binary,
            "serve",
            self.model_path,
            "--host",
            host,
            "--port",
            str(port),
            "--context",
            str(self.context),
        ]
        log_path = RUNS_DIR / f"{run_id}-correctness-server.log"
        start = time.monotonic()
        initial_swap_gb = psutil.swap_memory().used / float(1 << 30)
        memory_samples: list[MemorySample] = []
        low_memory_streak = 0
        stop_event = threading.Event()
        violation: dict[str, str | None] = {"reason": None}
        output_payload: list[dict[str, Any]] = []
        process: subprocess.Popen[str] | None = None
        intentional_shutdown = False

        def sample_memory() -> None:
            nonlocal low_memory_streak
            while not stop_event.wait(sample_interval_s):
                free_gb = psutil.virtual_memory().available / float(1 << 30)
                swap_gb = psutil.swap_memory().used / float(1 << 30)
                memory_samples.append(
                    MemorySample(
                        elapsed_s=time.monotonic() - start,
                        free_gb=free_gb,
                        swap_used_gb=swap_gb,
                    )
                )
                low_memory_streak = low_memory_streak + 1 if free_gb < min_free_gb else 0
                if low_memory_streak >= 2 and violation["reason"] is None:
                    violation["reason"] = "memory_floor"
                if swap_gb - initial_swap_gb > max_swap_delta_gb and violation["reason"] is None:
                    violation["reason"] = "swap_growth"
                if violation["reason"] is not None:
                    stop_event.set()
                    return

        with log_path.open("w", encoding="utf-8", errors="replace") as log_handle:
            process = subprocess.Popen(
                serve_command,
                cwd=WORKSPACE_ROOT,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                env=os.environ.copy(),
            )

            memory_thread = threading.Thread(target=sample_memory, daemon=True)
            memory_thread.start()

            try:
                startup_deadline = time.monotonic() + min(timeout_s, 300)
                while time.monotonic() < startup_deadline:
                    if violation["reason"] is not None:
                        break
                    if process.poll() is not None:
                        break
                    try:
                        with urllib_request.urlopen(f"{base_url}/api/tags", timeout=2) as response:
                            if response.status == 200:
                                break
                    except urllib_error.URLError:
                        time.sleep(0.25)
                        continue
                else:
                    violation["reason"] = "startup_timeout"

                if violation["reason"] is None and process.poll() is None:
                    for check in SANITY_CHECKS:
                        payload = {
                            "model": model_name,
                            "stream": False,
                            "messages": [{"role": "user", "content": check["prompt"]}],
                            "options": {
                                "temperature": 0.0,
                                "top_k": 1,
                                "top_p": 1.0,
                                "num_predict": self.correctness_tokens,
                                "seed": 1,
                            },
                        }
                        request = urllib_request.Request(
                            f"{base_url}/api/chat",
                            data=json.dumps(payload).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        try:
                            with urllib_request.urlopen(request, timeout=min(timeout_s, 180)) as response:
                                body = response.read().decode("utf-8", errors="replace")
                        except (urllib_error.URLError, TimeoutError) as exc:
                            output_payload.append(
                                {
                                    "name": check["name"],
                                    "prompt": check["prompt"],
                                    "error": str(exc),
                                }
                            )
                            violation["reason"] = "request_error"
                            break

                        try:
                            parsed = json.loads(body)
                        except json.JSONDecodeError:
                            output_payload.append(
                                {
                                    "name": check["name"],
                                    "prompt": check["prompt"],
                                    "raw": body,
                                }
                            )
                            violation["reason"] = "bad_json"
                            break

                        response_text = parsed.get("message", {}).get("content", "")
                        output_payload.append(
                            {
                                "name": check["name"],
                                "prompt": check["prompt"],
                                "response": response_text,
                                "prompt_eval_count": parsed.get("prompt_eval_count"),
                                "prompt_eval_duration": parsed.get("prompt_eval_duration"),
                                "eval_count": parsed.get("eval_count"),
                                "eval_duration": parsed.get("eval_duration"),
                            }
                        )

                        if violation["reason"] is not None:
                            break
                        if not re.search(check["pattern"], response_text, flags=re.I | re.S):
                            violation["reason"] = "correctness_mismatch"
                            break
            finally:
                stop_event.set()
                if process.poll() is None:
                    intentional_shutdown = True
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                memory_thread.join(timeout=2)

        wall_s = time.monotonic() - start
        final_free_gb = psutil.virtual_memory().available / float(1 << 30)
        final_swap_gb = psutil.swap_memory().used / float(1 << 30)
        memory_samples.append(
            MemorySample(
                elapsed_s=wall_s,
                free_gb=final_free_gb,
                swap_used_gb=final_swap_gb,
            )
        )
        output_text = read_output(log_path)
        combined_output = output_text
        if output_payload:
            combined_output = (
                f"{output_text}\n\n=== correctness responses ===\n"
                f"{json.dumps(output_payload, indent=2)}\n"
            )

        exit_code = process.returncode if process is not None else 1
        if intentional_shutdown and violation["reason"] in (None, "correctness_mismatch"):
            exit_code = 0

        execution = ExecutionRecord(
            command=serve_command,
            log_path=str(log_path),
            wall_s=wall_s,
            exit_code=exit_code,
            stop_reason=violation["reason"],
            min_free_gb=min(sample.free_gb for sample in memory_samples),
            swap_delta_gb=max(max(sample.swap_used_gb for sample in memory_samples) - initial_swap_gb, 0.0),
            memory_samples=memory_samples,
            output_excerpt=summarize_output(combined_output),
            full_output=combined_output,
        )

        status = "ok"
        if violation["reason"] in {"memory_floor", "swap_growth", "correctness_mismatch"}:
            status = "reject"
        elif violation["reason"] is not None:
            status = "crash"
        elif process is not None and process.returncode not in (0, None, -15):
            status = "crash"

        record = PhaseRecord(
            phase="correctness-server",
            status=status,
            metrics=None,
            execution=execution,
        )
        return status, [record]

    def run_candidate(
        self,
        run_id: str,
        min_free_gb: float,
        timeout_s: int,
        sample_interval_s: float,
        max_swap_delta_gb: float,
        warmup_runs: int,
        measured_runs: int,
    ) -> tuple[str, list[PhaseRecord]]:
        phase_records: list[PhaseRecord] = []
        startup_record, process, base_url, model_name = self._start_server(
            run_id=run_id,
            min_free_gb=min_free_gb,
            timeout_s=timeout_s,
            sample_interval_s=sample_interval_s,
            max_swap_delta_gb=max_swap_delta_gb,
        )
        phase_records.append(startup_record)
        if startup_record.status != "ok" or process is None:
            return startup_record.status, phase_records

        try:
            for check in SANITY_CHECKS:
                phase_name = f"correctness-{check['name']}"
                record, parsed = self._run_server_chat_phase(
                    run_id=run_id,
                    phase=phase_name,
                    prompt=check["prompt"],
                    max_tokens=self.correctness_tokens,
                    base_url=base_url,
                    model_name=model_name,
                    process=process,
                    min_free_gb=min_free_gb,
                    timeout_s=timeout_s,
                    sample_interval_s=sample_interval_s,
                    max_swap_delta_gb=max_swap_delta_gb,
                )
                phase_records.append(record)
                if record.status != "ok":
                    return record.status, phase_records

                response_text = str((parsed or {}).get("message", {}).get("content", ""))
                if not re.search(check["pattern"], response_text, flags=re.I | re.S):
                    record.status = "reject"
                    record.execution.stop_reason = "correctness_mismatch"
                    return "reject", phase_records

            for index in range(warmup_runs):
                record, _ = self._run_server_chat_phase(
                    run_id=run_id,
                    phase=f"warmup-{index + 1}",
                    prompt=THROUGHPUT_PROMPT,
                    max_tokens=self.max_tokens,
                    base_url=base_url,
                    model_name=model_name,
                    process=process,
                    min_free_gb=min_free_gb,
                    timeout_s=timeout_s,
                    sample_interval_s=sample_interval_s,
                    max_swap_delta_gb=max_swap_delta_gb,
                )
                phase_records.append(record)
                if record.status != "ok":
                    return record.status, phase_records

            for index in range(measured_runs):
                record, _ = self._run_server_chat_phase(
                    run_id=run_id,
                    phase=f"benchmark-{index + 1}",
                    prompt=THROUGHPUT_PROMPT,
                    max_tokens=self.max_tokens,
                    base_url=base_url,
                    model_name=model_name,
                    process=process,
                    min_free_gb=min_free_gb,
                    timeout_s=timeout_s,
                    sample_interval_s=sample_interval_s,
                    max_swap_delta_gb=max_swap_delta_gb,
                )
                phase_records.append(record)
                if record.status != "ok":
                    return record.status, phase_records
        finally:
            self._stop_server(process)

        return "ok", phase_records


class FlashMoeAdapter(LlamaCppAdapter):
    name = "flashmoe"

    def __init__(self, candidate: dict[str, Any], profile: dict[str, Any]) -> None:
        BackendAdapter.__init__(self, candidate, profile)
        self.config = candidate.get("backend_config", {})

        profile_flashmoe = profile.get("flashmoe", {})
        profile_binary = None
        if isinstance(profile_flashmoe, dict):
            profile_binary = profile_flashmoe.get("path")

        self.flashmoe_root = str(
            resolve_path_value(
                candidate,
                self.config.get("flashmoe_root"),
                DEFAULT_FLASHMOE_ROOT,
            )
        )
        self.binary = str(
            resolve_path_value(
                candidate,
                self.config.get("binary_path"),
                profile_binary or DEFAULT_FLASHMOE_BINARY,
            )
        )
        self.sidecar_dir = resolve_path_value(
            candidate,
            self.config.get("sidecar_dir"),
            DEFAULT_FLASHMOE_SIDECAR,
        )
        self.preflight = self._run_preflight()

    def _sidecar_tool_path(self) -> Path:
        return Path(self.flashmoe_root) / "tools" / "flashmoe-sidecar" / "flashmoe_sidecar.py"

    def _run_preflight(self) -> dict[str, Any]:
        issues: list[str] = []
        binary_path = Path(self.binary)
        if not binary_path.exists():
            issues.append(f"binary missing: {binary_path}")
        elif not os.access(binary_path, os.X_OK):
            issues.append(f"binary not executable: {binary_path}")

        moe_mode = str(self.config.get("moe_mode", "slot-bank"))
        sidecar_dir = Path(self.sidecar_dir) if self.sidecar_dir else None
        manifest_path = sidecar_dir / "manifest.json" if sidecar_dir else None
        if moe_mode != "off":
            if sidecar_dir is None:
                issues.append("sidecar_dir is required when moe_mode is enabled")
            elif not manifest_path or not manifest_path.exists():
                issues.append(f"sidecar manifest missing: {manifest_path}")

        coverage: dict[str, Any] | None = None
        if not issues and moe_mode == "slot-bank" and sidecar_dir is not None:
            tool_path = self._sidecar_tool_path()
            if not tool_path.exists():
                issues.append(f"sidecar inspect tool missing: {tool_path}")
            else:
                command = [
                    sys.executable,
                    str(tool_path),
                    "inspect",
                    "--model",
                    self.model_path,
                    "--families",
                    "routed",
                    "--sidecar",
                    str(sidecar_dir),
                    "--json",
                ]
                try:
                    completed = subprocess.run(
                        command,
                        cwd=Path(self.flashmoe_root),
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    coverage = json.loads(completed.stdout)
                    sidecar_info = coverage.get("sidecar") or {}
                    missing_items = list(sidecar_info.get("missing_from_sidecar") or [])
                    if missing_items:
                        first_missing = ", ".join(missing_items[:3])
                        issues.append(
                            "slot-bank sidecar coverage incomplete"
                            f" (missing={len(missing_items)}, first_missing={first_missing or 'unknown'})"
                        )
                except Exception as exc:  # noqa: BLE001
                    issues.append(f"sidecar preflight failed: {exc}")

        return {
            "ok": not issues,
            "issues": issues,
            "binary": self.binary,
            "flashmoe_root": self.flashmoe_root,
            "sidecar_dir": self.sidecar_dir,
            "coverage": coverage,
        }

    def _preflight_record(self, run_id: str) -> PhaseRecord:
        log_path = RUNS_DIR / f"{run_id}-preflight.log"
        full_output = json.dumps(self.preflight, indent=2, sort_keys=True)
        log_path.write_text(full_output + "\n", encoding="utf-8")
        free_gb = psutil.virtual_memory().available / float(1 << 30)
        swap_gb = psutil.swap_memory().used / float(1 << 30)
        sample = MemorySample(elapsed_s=0.0, free_gb=free_gb, swap_used_gb=swap_gb)
        execution = ExecutionRecord(
            command=[self.binary],
            log_path=str(log_path),
            wall_s=0.0,
            exit_code=1,
            stop_reason="flashmoe_preflight",
            min_free_gb=free_gb,
            swap_delta_gb=0.0,
            memory_samples=[sample],
            output_excerpt=summarize_output(full_output),
            full_output=full_output,
        )
        return PhaseRecord(
            phase="preflight",
            status="crash",
            metrics=None,
            execution=execution,
        )

    def _base_command(self, prompt: str, max_tokens: int, perf: bool) -> list[str]:
        cmd = [
            self.binary,
            "--color",
            "off",
            "--simple-io",
            "-m",
            self.model_path,
            "-c",
            str(self.context),
            "-n",
            str(max_tokens),
            "-p",
            prompt,
            "-cnv",
            "-st",
            "-fit",
            "on",
            "--seed",
            "42",
            "--temp",
            "0",
            "--top-k",
            "1",
            "--top-p",
            "1.0",
        ]
        if perf:
            cmd.append("--perf")

        mapping = {
            "threads": "--threads",
            "threads_batch": "--threads-batch",
            "batch_size": "--batch-size",
            "ubatch_size": "--ubatch-size",
            "gpu_layers": "--gpu-layers",
            "poll": "--poll",
            "poll_batch": "--poll-batch",
        }
        for key, flag in mapping.items():
            value = self.config.get(key)
            if value not in (None, ""):
                cmd.extend([flag, str(value)])

        moe_mode = str(self.config.get("moe_mode", "slot-bank"))
        if moe_mode not in ("", "off"):
            cmd.extend(["--moe-mode", moe_mode])
        if self.sidecar_dir:
            cmd.extend(["--moe-sidecar", self.sidecar_dir])
        slot_bank = self.config.get("moe_slot_bank")
        if slot_bank not in (None, ""):
            cmd.extend(["--moe-slot-bank", str(slot_bank)])

        for extra_arg in self.config.get("extra_args", []):
            cmd.append(str(extra_arg))
        return cmd

    def run_candidate(
        self,
        run_id: str,
        min_free_gb: float,
        timeout_s: int,
        sample_interval_s: float,
        max_swap_delta_gb: float,
        warmup_runs: int,
        measured_runs: int,
    ) -> tuple[str, list[PhaseRecord]]:
        if not self.preflight["ok"]:
            return "crash", [self._preflight_record(run_id)]
        return super().run_candidate(
            run_id=run_id,
            min_free_gb=min_free_gb,
            timeout_s=timeout_s,
            sample_interval_s=sample_interval_s,
            max_swap_delta_gb=max_swap_delta_gb,
            warmup_runs=warmup_runs,
            measured_runs=measured_runs,
        )


class FlashMoeServerAdapter(FlashMoeAdapter):
    name = "flashmoe_server"

    def __init__(self, candidate: dict[str, Any], profile: dict[str, Any]) -> None:
        super().__init__(candidate, profile)
        self.server_binary = str(
            resolve_path_value(
                candidate,
                self.config.get("server_binary_path"),
                Path(self.binary).resolve().parent / "llama-server",
            )
        )
        self._startup_wall_s = 0.0
        server_path = Path(self.server_binary)
        if not server_path.exists():
            self.preflight["ok"] = False
            self.preflight["issues"].append(f"server binary missing: {server_path}")
        elif not os.access(server_path, os.X_OK):
            self.preflight["ok"] = False
            self.preflight["issues"].append(f"server binary not executable: {server_path}")

    def _build_server_command(self, host: str, port: int) -> list[str]:
        cmd = [
            self.server_binary,
            "--log-colors",
            "off",
            "-m",
            self.model_path,
            "-c",
            str(self.context),
            "-fit",
            "on",
            "--seed",
            "42",
            "--temp",
            "0",
            "--top-k",
            "1",
            "--top-p",
            "1.0",
            "--host",
            host,
            "--port",
            str(port),
            "--no-webui",
        ]

        mapping = {
            "threads": "--threads",
            "threads_batch": "--threads-batch",
            "batch_size": "--batch-size",
            "ubatch_size": "--ubatch-size",
            "gpu_layers": "--gpu-layers",
            "poll": "--poll",
            "poll_batch": "--poll-batch",
            "parallel": "--parallel",
        }
        for key, flag in mapping.items():
            value = self.config.get(key)
            if value not in (None, ""):
                cmd.extend([flag, str(value)])

        moe_mode = str(self.config.get("moe_mode", "slot-bank"))
        if moe_mode not in ("", "off"):
            cmd.extend(["--moe-mode", moe_mode])
        if self.sidecar_dir:
            cmd.extend(["--moe-sidecar", self.sidecar_dir])
        slot_bank = self.config.get("moe_slot_bank")
        if slot_bank not in (None, ""):
            cmd.extend(["--moe-slot-bank", str(slot_bank)])

        cache_prompt = self.config.get("cache_prompt", False)
        cmd.append("--cache-prompt" if cache_prompt else "--no-cache-prompt")

        for extra_arg in self.config.get("extra_args", []):
            cmd.append(str(extra_arg))
        return cmd

    def _start_server(
        self,
        run_id: str,
        min_free_gb: float,
        timeout_s: int,
        sample_interval_s: float,
        max_swap_delta_gb: float,
    ) -> tuple[PhaseRecord, subprocess.Popen[str] | None, str, str]:
        port = choose_free_port()
        host = "127.0.0.1"
        model_name = Path(self.model_path).name
        base_url = f"http://{host}:{port}"
        command = self._build_server_command(host, port)
        log_path = RUNS_DIR / f"{run_id}-server.log"
        start = time.monotonic()
        next_sample_at = start
        low_memory_streak = 0
        stop_reason: str | None = None
        initial_swap_gb = psutil.swap_memory().used / float(1 << 30)
        memory_samples: list[MemorySample] = []

        log_handle = log_path.open("w", encoding="utf-8", errors="replace")
        try:
            env = os.environ.copy()
            flashmoe_bin_dir = str(Path(self.server_binary).resolve().parent)
            env["DYLD_LIBRARY_PATH"] = (
                flashmoe_bin_dir
                if not env.get("DYLD_LIBRARY_PATH")
                else flashmoe_bin_dir + ":" + env["DYLD_LIBRARY_PATH"]
            )
            process = subprocess.Popen(
                command,
                cwd=WORKSPACE_ROOT,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
        finally:
            log_handle.close()

        ready = False
        startup_timeout_s = min(timeout_s, 300)
        while True:
            now = time.monotonic()
            if now >= next_sample_at:
                free_gb = psutil.virtual_memory().available / float(1 << 30)
                swap_gb = psutil.swap_memory().used / float(1 << 30)
                memory_samples.append(
                    MemorySample(
                        elapsed_s=now - start,
                        free_gb=free_gb,
                        swap_used_gb=swap_gb,
                    )
                )
                low_memory_streak = low_memory_streak + 1 if free_gb < min_free_gb else 0
                if low_memory_streak >= 2:
                    stop_reason = "memory_floor"
                if swap_gb - initial_swap_gb > max_swap_delta_gb:
                    stop_reason = "swap_growth"
                next_sample_at = now + sample_interval_s

            if stop_reason is None and process.poll() is not None:
                stop_reason = "startup_exit"

            if stop_reason is None:
                try:
                    with urllib_request.urlopen(f"{base_url}/health", timeout=2) as response:
                        if response.status == 200:
                            ready = True
                            break
                except urllib_error.URLError:
                    pass

            if stop_reason is None and (now - start) > startup_timeout_s:
                stop_reason = "startup_timeout"

            if stop_reason is not None:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                break

            time.sleep(0.1)

        wall_s = time.monotonic() - start
        self._startup_wall_s = wall_s if ready else 0.0
        final_free_gb = psutil.virtual_memory().available / float(1 << 30)
        final_swap_gb = psutil.swap_memory().used / float(1 << 30)
        memory_samples.append(
            MemorySample(
                elapsed_s=wall_s,
                free_gb=final_free_gb,
                swap_used_gb=final_swap_gb,
            )
        )

        output_text = read_output(log_path)
        if ready:
            output_text = f"{output_text}\n\nServer ready at {base_url}\n"

        exit_code = 0 if ready else process.returncode
        execution = ExecutionRecord(
            command=command,
            log_path=str(log_path),
            wall_s=wall_s,
            exit_code=0 if exit_code is None else exit_code,
            stop_reason=stop_reason,
            min_free_gb=min(sample.free_gb for sample in memory_samples),
            swap_delta_gb=max(max(sample.swap_used_gb for sample in memory_samples) - initial_swap_gb, 0.0),
            memory_samples=memory_samples,
            output_excerpt=summarize_output(output_text),
            full_output=output_text,
        )

        status = "ok"
        if stop_reason in {"memory_floor", "swap_growth"}:
            status = "reject"
        elif not ready:
            status = "crash"

        record = PhaseRecord(
            phase="server-startup",
            status=status,
            metrics=None,
            execution=execution,
        )
        return record, (process if ready else None), base_url, model_name

    def _stop_server(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _metrics_from_completion(self, parsed: dict[str, Any], requested_tokens: int, wall_s: float) -> Metrics:
        timings = parsed.get("timings") or {}
        usage = parsed.get("usage") or {}
        prompt_count = int(timings.get("prompt_n") or usage.get("prompt_tokens") or 0)
        eval_count = int(timings.get("predicted_n") or requested_tokens or 0)
        prompt_eval_s = float(timings.get("prompt_ms") or 0.0) / 1000.0
        generation_s = float(timings.get("predicted_ms") or 0.0) / 1000.0
        prompt_tok_s = float(timings.get("prompt_per_second") or 0.0)
        gen_tok_s = float(timings.get("predicted_per_second") or 0.0)
        ttft_ms = (prompt_eval_s + (generation_s / eval_count if eval_count > 0 and generation_s > 0 else 0.0)) * 1000.0
        return Metrics(
            prompt_tok_s=prompt_tok_s,
            gen_tok_s=gen_tok_s,
            prompt_eval_s=prompt_eval_s,
            generation_s=generation_s,
            wall_s=wall_s,
            load_s=self._startup_wall_s,
            ttft_ms=ttft_ms,
            tokens_generated=eval_count if eval_count > 0 else prompt_count,
        )

    def _build_chat_payload(self, model_name: str, prompt: str, max_tokens: int) -> dict[str, Any]:
        return {
            "model": model_name,
            "stream": False,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "top_k": 1,
            "top_p": 1.0,
            "max_tokens": max_tokens,
            "seed": 1,
        }

    def _run_server_chat_phase(
        self,
        run_id: str,
        phase: str,
        prompt: str,
        max_tokens: int,
        base_url: str,
        model_name: str,
        process: subprocess.Popen[str],
        min_free_gb: float,
        timeout_s: int,
        sample_interval_s: float,
        max_swap_delta_gb: float,
    ) -> tuple[PhaseRecord, dict[str, Any] | None]:
        phase_slug = re.sub(r"[^a-z0-9]+", "-", phase.lower())
        log_path = RUNS_DIR / f"{run_id}-{phase_slug}.log"
        payload = self._build_chat_payload(model_name=model_name, prompt=prompt, max_tokens=max_tokens)
        request_obj = urllib_request.Request(
            f"{base_url}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        start = time.monotonic()
        next_sample_at = start
        low_memory_streak = 0
        stop_reason: str | None = None
        initial_swap_gb = psutil.swap_memory().used / float(1 << 30)
        memory_samples: list[MemorySample] = []
        response_holder: dict[str, Any] = {}
        error_holder: dict[str, str] = {}

        def do_request() -> None:
            try:
                with urllib_request.urlopen(request_obj, timeout=timeout_s) as response:
                    response_holder["status"] = response.status
                    response_holder["body"] = response.read().decode("utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001
                error_holder["error"] = str(exc)

        worker = threading.Thread(target=do_request, daemon=True)
        worker.start()

        while worker.is_alive():
            now = time.monotonic()
            if now >= next_sample_at:
                free_gb = psutil.virtual_memory().available / float(1 << 30)
                swap_gb = psutil.swap_memory().used / float(1 << 30)
                memory_samples.append(
                    MemorySample(
                        elapsed_s=now - start,
                        free_gb=free_gb,
                        swap_used_gb=swap_gb,
                    )
                )
                low_memory_streak = low_memory_streak + 1 if free_gb < min_free_gb else 0
                if low_memory_streak >= 2:
                    stop_reason = "memory_floor"
                if swap_gb - initial_swap_gb > max_swap_delta_gb:
                    stop_reason = "swap_growth"
                next_sample_at = now + sample_interval_s

            if stop_reason is None and process.poll() is not None:
                stop_reason = "server_exit"

            if stop_reason is None and (now - start) > timeout_s:
                stop_reason = "timeout"

            if stop_reason is not None:
                if process.poll() is None:
                    process.kill()
                break

            time.sleep(0.1)

        worker.join(timeout=5)
        wall_s = time.monotonic() - start
        final_free_gb = psutil.virtual_memory().available / float(1 << 30)
        final_swap_gb = psutil.swap_memory().used / float(1 << 30)
        memory_samples.append(
            MemorySample(
                elapsed_s=wall_s,
                free_gb=final_free_gb,
                swap_used_gb=final_swap_gb,
            )
        )

        parsed: dict[str, Any] | None = None
        if stop_reason is None and error_holder:
            stop_reason = "request_error"

        raw_body = str(response_holder.get("body", ""))
        if stop_reason is None:
            try:
                parsed = json.loads(raw_body)
            except json.JSONDecodeError:
                stop_reason = "bad_json"

        full_output = json.dumps(
            {
                "phase": phase,
                "url": f"{base_url}/v1/chat/completions",
                "payload": payload,
                "http_status": response_holder.get("status"),
                "response": parsed if parsed is not None else raw_body,
                "error": error_holder.get("error"),
                "stop_reason": stop_reason,
            },
            indent=2,
            ensure_ascii=True,
        )
        log_path.write_text(full_output + "\n", encoding="utf-8")

        execution = ExecutionRecord(
            command=["POST", f"{base_url}/v1/chat/completions"],
            log_path=str(log_path),
            wall_s=wall_s,
            exit_code=0 if stop_reason is None else 1,
            stop_reason=stop_reason,
            min_free_gb=min(sample.free_gb for sample in memory_samples),
            swap_delta_gb=max(max(sample.swap_used_gb for sample in memory_samples) - initial_swap_gb, 0.0),
            memory_samples=memory_samples,
            output_excerpt=summarize_output(full_output),
            full_output=full_output,
        )

        if stop_reason in {"memory_floor", "swap_growth"}:
            return PhaseRecord(phase=phase, status="reject", metrics=None, execution=execution), parsed
        if stop_reason is not None:
            return PhaseRecord(phase=phase, status="crash", metrics=None, execution=execution), parsed

        metrics = self._metrics_from_completion(parsed or {}, max_tokens, wall_s)
        if phase.startswith("correctness"):
            return PhaseRecord(phase=phase, status="ok", metrics=None, execution=execution), parsed
        if metrics.gen_tok_s <= 0.0:
            return PhaseRecord(phase=phase, status="crash", metrics=None, execution=execution), parsed
        return PhaseRecord(phase=phase, status="ok", metrics=metrics, execution=execution), parsed

    def run_candidate(
        self,
        run_id: str,
        min_free_gb: float,
        timeout_s: int,
        sample_interval_s: float,
        max_swap_delta_gb: float,
        warmup_runs: int,
        measured_runs: int,
    ) -> tuple[str, list[PhaseRecord]]:
        if not self.preflight["ok"]:
            return "crash", [self._preflight_record(run_id)]

        phase_records: list[PhaseRecord] = []
        startup_record, process, base_url, model_name = self._start_server(
            run_id=run_id,
            min_free_gb=min_free_gb,
            timeout_s=timeout_s,
            sample_interval_s=sample_interval_s,
            max_swap_delta_gb=max_swap_delta_gb,
        )
        phase_records.append(startup_record)
        if startup_record.status != "ok" or process is None:
            return startup_record.status, phase_records

        try:
            for check in SANITY_CHECKS:
                phase_name = f"correctness-{check['name']}"
                record, parsed = self._run_server_chat_phase(
                    run_id=run_id,
                    phase=phase_name,
                    prompt=check["prompt"],
                    max_tokens=self.correctness_tokens,
                    base_url=base_url,
                    model_name=model_name,
                    process=process,
                    min_free_gb=min_free_gb,
                    timeout_s=timeout_s,
                    sample_interval_s=sample_interval_s,
                    max_swap_delta_gb=max_swap_delta_gb,
                )
                phase_records.append(record)
                if record.status != "ok":
                    return record.status, phase_records

                response_text = str(((parsed or {}).get("choices") or [{}])[0].get("message", {}).get("content", ""))
                if not re.search(check["pattern"], response_text, flags=re.I | re.S):
                    record.status = "reject"
                    record.execution.stop_reason = "correctness_mismatch"
                    return "reject", phase_records

            for index in range(warmup_runs):
                record, _ = self._run_server_chat_phase(
                    run_id=run_id,
                    phase=f"warmup-{index + 1}",
                    prompt=THROUGHPUT_PROMPT,
                    max_tokens=self.max_tokens,
                    base_url=base_url,
                    model_name=model_name,
                    process=process,
                    min_free_gb=min_free_gb,
                    timeout_s=timeout_s,
                    sample_interval_s=sample_interval_s,
                    max_swap_delta_gb=max_swap_delta_gb,
                )
                phase_records.append(record)
                if record.status != "ok":
                    return record.status, phase_records

            for index in range(measured_runs):
                record, _ = self._run_server_chat_phase(
                    run_id=run_id,
                    phase=f"benchmark-{index + 1}",
                    prompt=THROUGHPUT_PROMPT,
                    max_tokens=self.max_tokens,
                    base_url=base_url,
                    model_name=model_name,
                    process=process,
                    min_free_gb=min_free_gb,
                    timeout_s=timeout_s,
                    sample_interval_s=sample_interval_s,
                    max_swap_delta_gb=max_swap_delta_gb,
                )
                phase_records.append(record)
                if record.status != "ok":
                    return record.status, phase_records
        finally:
            self._stop_server(process)

        return "ok", phase_records


BACKENDS = {
    "llama_cpp": LlamaCppAdapter,
    "hypura": HypuraAdapter,
    "flashmoe": FlashMoeAdapter,
    "flashmoe_server": FlashMoeServerAdapter,
}


def make_run_id(candidate: dict[str, Any]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", candidate.get("description", candidate["backend"]).lower())
    slug = slug.strip("-")[:32] or candidate["backend"]
    return f"{utc_now().strftime('%Y%m%dT%H%M%SZ')}-{slug}"


def monitor_process(
    command: list[str],
    log_path: Path,
    min_free_gb: float,
    timeout_s: int,
    sample_interval_s: float,
    max_swap_delta_gb: float,
) -> ExecutionRecord:
    start = time.monotonic()
    stop_reason: str | None = None
    low_memory_streak = 0
    memory_samples: list[MemorySample] = []
    initial_swap_gb = psutil.swap_memory().used / float(1 << 30)
    next_sample_at = start

    with log_path.open("w", encoding="utf-8", errors="replace") as log_handle:
        process = subprocess.Popen(
            command,
            cwd=WORKSPACE_ROOT,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            env=os.environ.copy(),
        )

        while True:
            now = time.monotonic()
            if now >= next_sample_at:
                free_gb = psutil.virtual_memory().available / float(1 << 30)
                swap_gb = psutil.swap_memory().used / float(1 << 30)
                memory_samples.append(
                    MemorySample(
                        elapsed_s=now - start,
                        free_gb=free_gb,
                        swap_used_gb=swap_gb,
                    )
                )
                low_memory_streak = low_memory_streak + 1 if free_gb < min_free_gb else 0
                if low_memory_streak >= 2:
                    stop_reason = "memory_floor"
                if swap_gb - initial_swap_gb > max_swap_delta_gb:
                    stop_reason = "swap_growth"
                next_sample_at = now + sample_interval_s

            if stop_reason is None and (now - start) > timeout_s:
                stop_reason = "timeout"

            if stop_reason is not None:
                process.kill()
                break

            if process.poll() is not None:
                break

            time.sleep(0.1)

        exit_code = process.wait()

    wall_s = time.monotonic() - start
    final_free_gb = psutil.virtual_memory().available / float(1 << 30)
    final_swap_gb = psutil.swap_memory().used / float(1 << 30)
    memory_samples.append(
        MemorySample(
            elapsed_s=wall_s,
            free_gb=final_free_gb,
            swap_used_gb=final_swap_gb,
        )
    )

    min_free_seen = min(sample.free_gb for sample in memory_samples)
    swap_delta_gb = max(sample.swap_used_gb for sample in memory_samples) - initial_swap_gb
    output_text = read_output(log_path)
    return ExecutionRecord(
        command=command,
        log_path=str(log_path),
        wall_s=wall_s,
        exit_code=exit_code,
        stop_reason=stop_reason,
        min_free_gb=min_free_seen,
        swap_delta_gb=max(swap_delta_gb, 0.0),
        memory_samples=memory_samples,
        output_excerpt=summarize_output(output_text),
        full_output=output_text,
    )


def run_phase(
    adapter: BackendAdapter,
    run_id: str,
    phase: str,
    prompt: str,
    min_free_gb: float,
    timeout_s: int,
    sample_interval_s: float,
    max_swap_delta_gb: float,
) -> PhaseRecord:
    if phase.startswith("correctness"):
        command = adapter.make_correctness_command(prompt)
        requested_tokens = adapter.correctness_tokens
    else:
        command = adapter.make_benchmark_command(prompt)
        requested_tokens = adapter.max_tokens

    phase_slug = re.sub(r"[^a-z0-9]+", "-", phase.lower())
    log_path = RUNS_DIR / f"{run_id}-{phase_slug}.log"
    execution = monitor_process(
        command=command,
        log_path=log_path,
        min_free_gb=min_free_gb,
        timeout_s=timeout_s,
        sample_interval_s=sample_interval_s,
        max_swap_delta_gb=max_swap_delta_gb,
    )

    if execution.stop_reason in {"memory_floor", "swap_growth"}:
        return PhaseRecord(phase=phase, status="reject", metrics=None, execution=execution)
    if execution.stop_reason == "timeout":
        return PhaseRecord(phase=phase, status="crash", metrics=None, execution=execution)
    if execution.exit_code != 0:
        return PhaseRecord(phase=phase, status="crash", metrics=None, execution=execution)

    metrics = adapter.parse_metrics(execution.full_output, requested_tokens, execution.wall_s)
    if not phase.startswith("correctness") and metrics.gen_tok_s <= 0.0:
        return PhaseRecord(phase=phase, status="crash", metrics=None, execution=execution)
    return PhaseRecord(phase=phase, status="ok", metrics=metrics, execution=execution)


def run_standard_correctness_checks(
    adapter: BackendAdapter,
    run_id: str,
    min_free_gb: float,
    timeout_s: int,
    sample_interval_s: float,
    max_swap_delta_gb: float,
) -> tuple[str, list[PhaseRecord]]:
    records: list[PhaseRecord] = []
    for check in SANITY_CHECKS:
        phase_name = f"correctness-{check['name']}"
        record = run_phase(
            adapter=adapter,
            run_id=run_id,
            phase=phase_name,
            prompt=check["prompt"],
            min_free_gb=min_free_gb,
            timeout_s=timeout_s,
            sample_interval_s=sample_interval_s,
            max_swap_delta_gb=max_swap_delta_gb,
        )
        records.append(record)
        if record.status != "ok":
            return record.status, records
        response_text = adapter.extract_response_text(record.execution.full_output)
        if not re.search(check["pattern"], response_text, flags=re.I | re.S):
            record.status = "reject"
            return "reject", records
    return "ok", records


def choose_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def best_record() -> dict[str, Any] | None:
    if not BEST_PATH.exists():
        return None
    return json.loads(BEST_PATH.read_text(encoding="utf-8"))


def append_results_row(row: dict[str, str]) -> None:
    with TSV_PATH.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TSV_COLUMNS, delimiter="\t")
        writer.writerow(row)


def complexity_score(candidate: dict[str, Any]) -> int:
    score = 0
    for key, value in candidate.get("backend_config", {}).items():
        if value in (None, "", False):
            continue
        if key == "cpu_moe_mode" and value == "off":
            continue
        if key == "gpu_layers" and str(value) == "auto":
            continue
        score += 1
    return score


def is_promotable_candidate(candidate: dict[str, Any]) -> bool:
    return int(candidate.get("measured_runs", 3)) >= 3


def constraint_profile(candidate: dict[str, Any]) -> dict[str, float]:
    return {
        "min_free_gb": float(candidate.get("min_free_gb", 4.0)),
        "max_swap_delta_gb": float(candidate.get("max_swap_delta_gb", 0.25)),
    }


def compare_constraint_profiles(new: dict[str, float], current: dict[str, float]) -> int:
    epsilon = 1e-6

    if new["min_free_gb"] > current["min_free_gb"] + epsilon:
        return 1
    if new["min_free_gb"] < current["min_free_gb"] - epsilon:
        return -1

    if new["max_swap_delta_gb"] < current["max_swap_delta_gb"] - epsilon:
        return 1
    if new["max_swap_delta_gb"] > current["max_swap_delta_gb"] + epsilon:
        return -1

    return 0


def is_better_result(
    new_summary: dict[str, float],
    new_candidate: dict[str, Any],
    current_best: dict[str, Any] | None,
) -> bool:
    if current_best is None:
        return True

    new_promotable = is_promotable_candidate(new_candidate)
    current_measured_runs = int(current_best.get("measured_runs", 0))
    if new_promotable and current_measured_runs < 3:
        return True
    if not new_promotable and current_measured_runs >= 3:
        return False

    new_constraints = constraint_profile(new_candidate)
    current_constraints = current_best.get(
        "constraints",
        {"min_free_gb": 0.0, "max_swap_delta_gb": float("inf")},
    )
    constraint_cmp = compare_constraint_profiles(new_constraints, current_constraints)
    if constraint_cmp > 0:
        return True
    if constraint_cmp < 0:
        return False

    current_summary = current_best.get("summary", {})
    epsilon = 1e-6

    current_score = float(current_summary.get("score", 0.0))
    if new_summary["score"] > current_score + epsilon:
        return True
    if new_summary["score"] < current_score - epsilon:
        return False

    current_ttft = float(current_summary.get("ttft_ms", float("inf")))
    if new_summary["ttft_ms"] < current_ttft - epsilon:
        return True
    if new_summary["ttft_ms"] > current_ttft + epsilon:
        return False

    current_prompt = float(current_summary.get("prompt_tok_s", 0.0))
    if new_summary["prompt_tok_s"] > current_prompt + epsilon:
        return True
    if new_summary["prompt_tok_s"] < current_prompt - epsilon:
        return False

    current_load = float(current_summary.get("load_s", float("inf")))
    if new_summary["load_s"] < current_load - epsilon:
        return True
    if new_summary["load_s"] > current_load + epsilon:
        return False

    return complexity_score(new_candidate) < int(current_best.get("complexity", 1_000_000))


def render_progress_chart() -> None:
    command = [sys.executable, str(ROOT / "progress.py")]
    subprocess.run(command, cwd=ROOT, check=False)


def refresh_flashmoe_best() -> None:
    command = [sys.executable, str(ROOT / "sync_flashmoe_best.py")]
    subprocess.run(command, cwd=ROOT, check=False)


def refresh_flashmoe_server_best() -> None:
    command = [sys.executable, str(ROOT / "sync_flashmoe_server_best.py")]
    subprocess.run(command, cwd=ROOT, check=False)


def main() -> int:
    args = parse_args()
    ensure_results_layout()

    profile = read_machine_profile()
    candidate_path = Path(args.candidate).expanduser().resolve()
    candidate = load_candidate(candidate_path)
    if args.override:
        candidate = apply_overrides(candidate, candidate_path, list(args.override))
    backend_key = str(candidate["backend"])
    adapter_cls = BACKENDS.get(backend_key)
    if adapter_cls is None:
        raise ValueError(f"Unsupported backend: {backend_key}")

    adapter = adapter_cls(candidate, profile)
    min_free_gb = float(candidate.get("min_free_gb", 4.0))
    timeout_s = int(candidate.get("timeout_s", 900))
    sample_interval_s = float(candidate.get("sample_interval_s", 1.0))
    max_swap_delta_gb = float(candidate.get("max_swap_delta_gb", 0.25))
    warmup_runs = int(candidate.get("warmup_runs", 1))
    measured_runs = int(candidate.get("measured_runs", 3))

    run_id = make_run_id(candidate)
    started_at = utc_now()

    status, phase_records = adapter.run_candidate(
        run_id=run_id,
        min_free_gb=min_free_gb,
        timeout_s=timeout_s,
        sample_interval_s=sample_interval_s,
        max_swap_delta_gb=max_swap_delta_gb,
        warmup_runs=warmup_runs,
        measured_runs=measured_runs,
    )
    benchmark_records = [record for record in phase_records if record.phase.startswith("benchmark")]

    score = 0.0
    metrics_summary: dict[str, float] = {
        "score": 0.0,
        "gen_tok_s": 0.0,
        "prompt_tok_s": 0.0,
        "ttft_ms": 0.0,
        "load_s": 0.0,
    }
    if status == "ok" and benchmark_records:
        gen_scores = [record.metrics.gen_tok_s for record in benchmark_records if record.metrics]
        prompt_scores = [record.metrics.prompt_tok_s for record in benchmark_records if record.metrics]
        ttft_scores = [record.metrics.ttft_ms for record in benchmark_records if record.metrics]
        load_scores = [record.metrics.load_s for record in benchmark_records if record.metrics]
        score = median(gen_scores)
        metrics_summary = {
            "score": score,
            "gen_tok_s": score,
            "prompt_tok_s": median(prompt_scores),
            "ttft_ms": median(ttft_scores),
            "load_s": median(load_scores),
        }

        if is_promotable_candidate(candidate):
            existing_best = best_record()
            if is_better_result(metrics_summary, candidate, existing_best):
                status = "keep"
            else:
                status = "discard"
        else:
            status = "discard"

    min_free_gb_seen = min(record.execution.min_free_gb for record in phase_records)
    swap_delta_gb = max(record.execution.swap_delta_gb for record in phase_records)

    artifact = {
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "finished_at": utc_now().isoformat(),
        "candidate_path": str(candidate_path),
        "overrides": list(args.override),
        "candidate": {key: value for key, value in candidate.items() if not key.startswith("_")},
        "backend": adapter.name,
        "status": status,
        "summary": {
            **metrics_summary,
            "min_free_gb": min_free_gb_seen,
            "swap_delta_gb": swap_delta_gb,
        },
        "constraints": {
            "min_free_gb": min_free_gb,
            "max_swap_delta_gb": max_swap_delta_gb,
            "timeout_s": timeout_s,
            "sample_interval_s": sample_interval_s,
        },
        "phase_records": [
            {
                "phase": record.phase,
                "status": record.status,
                "metrics": asdict(record.metrics) if record.metrics else None,
                "execution": {
                    "command": record.execution.command,
                    "log_path": record.execution.log_path,
                    "wall_s": record.execution.wall_s,
                    "exit_code": record.execution.exit_code,
                    "stop_reason": record.execution.stop_reason,
                    "min_free_gb": record.execution.min_free_gb,
                    "swap_delta_gb": record.execution.swap_delta_gb,
                    "output_excerpt": record.execution.output_excerpt,
                    "memory_samples": [asdict(sample) for sample in record.execution.memory_samples],
                },
            }
            for record in phase_records
        ],
        "commits": {
            "workspace": git_commit(WORKSPACE_ROOT),
            "hypura": git_commit(WORKSPACE_ROOT / "hypura-main"),
            "flashmoe": git_commit(WORKSPACE_ROOT / "anemll-flash-llama.cpp-gemma4"),
        },
    }
    artifact_path = RUNS_DIR / f"{run_id}.json"
    artifact_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")

    if status == "keep":
        exported_candidate = {key: value for key, value in candidate.items() if not key.startswith("_")}
        exported_candidate["model_path"] = str(
            Path(exported_candidate["model_path"]).expanduser()
            if Path(exported_candidate["model_path"]).expanduser().is_absolute()
            else (candidate_path.parent / exported_candidate["model_path"]).resolve()
        )
        BEST_PATH.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "summary": metrics_summary,
                    "constraints": constraint_profile(candidate),
                    "measured_runs": measured_runs,
                    "backend": adapter.name,
                    "artifact_path": str(artifact_path),
                    "candidate_path": str(candidate_path),
                    "candidate_export_path": str(BEST_CANDIDATE_PATH),
                    "complexity": complexity_score(candidate),
                    "updated_at": utc_now().isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        BEST_CANDIDATE_PATH.write_text(
            yaml.safe_dump(
                exported_candidate,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    if status in {"keep", "discard"} and adapter.name == "flashmoe" and measured_runs >= 3:
        refresh_flashmoe_best()
    if status in {"keep", "discard"} and adapter.name == "flashmoe_server" and measured_runs >= 3:
        refresh_flashmoe_server_best()

    append_results_row(
        {
            "run_id": run_id,
            "time": started_at.isoformat(),
            "backend": adapter.name,
            "status": status,
            "score": format_float(metrics_summary["score"]),
            "gen_tok_s": format_float(metrics_summary["gen_tok_s"]),
            "prompt_tok_s": format_float(metrics_summary["prompt_tok_s"]),
            "ttft_ms": format_float(metrics_summary["ttft_ms"]),
            "load_s": format_float(metrics_summary["load_s"]),
            "min_free_gb": format_float(min_free_gb_seen),
            "swap_delta_gb": format_float(swap_delta_gb),
            "description": str(candidate.get("description", "")),
        }
    )

    if not args.skip_plot:
        render_progress_chart()

    print(f"Run ID:     {run_id}")
    print(f"Backend:    {adapter.name}")
    print(f"Status:     {status}")
    print(f"Gen tok/s:  {metrics_summary['gen_tok_s']:.2f}")
    print(f"Prompt tok/s: {metrics_summary['prompt_tok_s']:.2f}")
    print(f"Load time:  {metrics_summary['load_s']:.2f}s")
    print(f"TTFT:       {metrics_summary['ttft_ms']:.1f} ms")
    print(f"Min free:   {min_free_gb_seen:.2f} GB")
    print(f"Swap delta: {swap_delta_gb:.2f} GB")
    print(f"Artifact:   {artifact_path}")
    return 0 if status in {"keep", "discard"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
