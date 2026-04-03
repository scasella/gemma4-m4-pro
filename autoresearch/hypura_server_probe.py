"""
Probe a running Hypura server and record warm-request behavior.

Usage:
    uv run hypura_server_probe.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path
from statistics import median
from typing import Any
from urllib import request


ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
OUTPUT_PATH = RESULTS_DIR / "hypura_server_probe_latest.json"
HOST = os.environ.get("HYPURA_HOST", os.environ.get("HOST", "127.0.0.1"))
PORT = int(os.environ.get("HYPURA_PORT", os.environ.get("PORT", "8080")))


def model_name(base_url: str) -> str:
    with request.urlopen(f"{base_url}/api/tags", timeout=30) as response:
        parsed = json.load(response)
    models = parsed.get("models") or []
    for item in models:
        name = item.get("name") or item.get("model")
        if name:
            return str(name)
    raise RuntimeError("No model name found in /api/tags")


def call_chat(base_url: str, current_model: str, prompt: str) -> dict[str, Any]:
    payload = {
        "model": current_model,
        "stream": False,
        "messages": [{"role": "user", "content": prompt}],
        "options": {
            "temperature": 0.0,
            "top_k": 1,
            "top_p": 1.0,
            "num_predict": 8,
            "seed": 1,
        },
    }
    start = time.perf_counter()
    req = request.Request(
        f"{base_url}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=300) as response:
        body = json.loads(response.read().decode("utf-8"))
    elapsed = time.perf_counter() - start

    prompt_count = int(body.get("prompt_eval_count") or 0)
    eval_count = int(body.get("eval_count") or 0)
    prompt_eval_s = float(body.get("prompt_eval_duration") or 0.0) / 1e9
    generation_s = float(body.get("eval_duration") or 0.0) / 1e9
    total_s = float(body.get("total_duration") or 0.0) / 1e9
    load_s = float(body.get("load_duration") or 0.0) / 1e9

    prompt_tok_s = (prompt_count / prompt_eval_s) if prompt_eval_s > 0 else 0.0
    predicted_tok_s = (eval_count / generation_s) if generation_s > 0 else 0.0
    ttft_ms = (
        prompt_eval_s + (generation_s / eval_count if eval_count > 0 and generation_s > 0 else 0.0)
    ) * 1000.0

    return {
        "prompt": prompt,
        "answer": body.get("message", {}).get("content", ""),
        "elapsed_s": round(elapsed, 3),
        "prompt_eval_count": prompt_count,
        "eval_count": eval_count,
        "prompt_ms": round(prompt_eval_s * 1000.0, 3),
        "prompt_per_second": round(prompt_tok_s, 2),
        "predicted_ms": round(generation_s * 1000.0, 3),
        "predicted_per_second": round(predicted_tok_s, 2),
        "ttft_ms": round(ttft_ms, 3),
        "total_s": round(total_s if total_s > 0 else elapsed, 3),
        "load_s": round(load_s, 3),
    }


def pid_for_port(port: int) -> int | None:
    output = subprocess.check_output(
        ["lsof", "-ti", f"TCP:{port}", "-sTCP:LISTEN"],
        text=True,
    ).strip()
    if not output:
        return None
    for line in output.splitlines():
        value = line.strip()
        if value.isdigit():
            return int(value)
    return None


def memory_snapshot(pid: int | None) -> dict[str, Any]:
    if pid is None:
        return {}
    output = subprocess.check_output(
        ["ps", "-o", "rss=,vsz=,%cpu=,etime=,command=", "-p", str(pid)],
        text=True,
    ).strip()
    if not output:
        return {"pid": pid}
    parts = output.split(None, 4)
    rss_kb = int(parts[0])
    vsz_kb = int(parts[1])
    cpu_pct = float(parts[2])
    elapsed = parts[3]
    command = parts[4] if len(parts) > 4 else ""
    return {
        "pid": pid,
        "rss_gb": rss_kb / float(1 << 20),
        "vsz_gb": vsz_kb / float(1 << 20),
        "cpu_pct": cpu_pct,
        "elapsed": elapsed,
        "command": command,
    }


def summarize(items: list[dict[str, Any]]) -> dict[str, float]:
    return {
        "median_elapsed_s": round(median(item["elapsed_s"] for item in items), 3),
        "median_prompt_per_second": round(
            median(item["prompt_per_second"] for item in items),
            2,
        ),
        "median_predicted_per_second": round(
            median(item["predicted_per_second"] for item in items),
            2,
        ),
        "median_prompt_ms": round(median(item["prompt_ms"] for item in items), 3),
        "median_predicted_ms": round(median(item["predicted_ms"] for item in items), 3),
        "median_ttft_ms": round(median(item["ttft_ms"] for item in items), 3),
        "median_total_s": round(median(item["total_s"] for item in items), 3),
    }


def ensure_server_reachable(host: str, port: int) -> None:
    with socket.create_connection((host, port), timeout=2):
        return


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ensure_server_reachable(HOST, PORT)
    base_url = f"http://{HOST}:{PORT}"
    current_model = model_name(base_url)
    nonce = int(time.time())

    similar_prompts = [
        f"Reply with one lowercase word only. Session {nonce}-a. What is the capital of Italy?",
        f"Reply with one digit only. Session {nonce}-b. What is 3+4?",
    ]
    repeat_prompt = (
        f"Reply with one lowercase word only. Session {nonce}-repeat. "
        "What is the capital of France?"
    )

    similar_runs = [call_chat(base_url, current_model, prompt) for prompt in similar_prompts]
    repeated_runs = [call_chat(base_url, current_model, repeat_prompt) for _ in range(2)]

    pid = pid_for_port(PORT)
    payload = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": HOST,
        "port": PORT,
        "model": current_model,
        "server": memory_snapshot(pid),
        "similar_short_prompt_runs": similar_runs,
        "similar_short_prompt_summary": summarize(similar_runs),
        "exact_repeat_runs": repeated_runs,
        "exact_repeat_summary": summarize(repeated_runs),
    }
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
