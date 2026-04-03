"""
Probe a running Flash-MoE server and record warm-request behavior.

Usage:
    uv run flashmoe_server_probe.py
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
OUTPUT_PATH = RESULTS_DIR / "flashmoe_server_probe_latest.json"
HOST = os.environ.get("FLASHMOE_HOST", "127.0.0.1")
PORT = int(os.environ.get("FLASHMOE_PORT", "8097"))


def call_chat(url: str, prompt: str) -> dict[str, Any]:
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "top_k": 1,
        "top_p": 1,
        "max_tokens": 8,
    }
    start = time.perf_counter()
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=300) as response:
        body = json.loads(response.read().decode("utf-8"))
    elapsed = time.perf_counter() - start
    timings = body.get("timings", {})
    usage = body.get("usage", {})
    return {
        "prompt": prompt,
        "answer": body["choices"][0]["message"]["content"],
        "elapsed_s": round(elapsed, 3),
        "cached_tokens": int(usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)),
        "prompt_ms": float(timings.get("prompt_ms", 0.0)),
        "prompt_per_second": float(timings.get("prompt_per_second", 0.0)),
        "predicted_ms": float(timings.get("predicted_ms", 0.0)),
        "predicted_per_second": float(timings.get("predicted_per_second", 0.0)),
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
        "median_cached_tokens": float(median(item["cached_tokens"] for item in items)),
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
    }


def ensure_server_reachable(host: str, port: int) -> None:
    with socket.create_connection((host, port), timeout=2):
        return


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ensure_server_reachable(HOST, PORT)
    url = f"http://{HOST}:{PORT}/v1/chat/completions"
    nonce = int(time.time())

    similar_prompts = [
        f"Reply with one lowercase word only. Session {nonce}-a. What is the capital of Italy?",
        f"Reply with one digit only. Session {nonce}-b. What is 3+4?",
    ]
    repeat_prompt = (
        f"Reply with one lowercase word only. Session {nonce}-repeat. "
        "What is the capital of France?"
    )

    similar_runs = [call_chat(url, prompt) for prompt in similar_prompts]
    repeated_runs = [call_chat(url, repeat_prompt) for _ in range(2)]

    pid = pid_for_port(PORT)
    payload = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": HOST,
        "port": PORT,
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
