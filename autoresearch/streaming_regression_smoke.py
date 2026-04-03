#!/usr/bin/env python3
from __future__ import annotations

import os
import pty
import selectors
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ANSWER_WRAPPER = ROOT / "gemma4_answer.sh"
CHAT_CLIENT = ROOT / "gemma4_chat.py"
FLASHMOE_ASK = ROOT / "flashmoe_gemma4_ask.sh"


FAKE_SERVER_SOURCE = r"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

kind = sys.argv[1]
port = int(sys.argv[2])


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if kind == "hypura" and self.path == "/api/tags":
            body = json.dumps({"models": [{"name": "fake-hypura-stream"}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if kind == "flashmoe" and self.path == "/health":
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        stream = bool(payload.get("stream"))

        if kind == "hypura" and self.path == "/api/chat":
            if not stream:
                time.sleep(1.0)
                body = json.dumps({"message": {"content": "alpha beta gamma"}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            for idx, chunk in enumerate(["alpha ", "beta ", "gamma"]):
                line = json.dumps({"message": {"content": chunk}, "done": idx == 2}).encode("utf-8") + b"\n"
                self.wfile.write(line)
                self.wfile.flush()
                time.sleep(0.5)
            return

        if kind == "flashmoe" and self.path == "/v1/chat/completions":
            if not stream:
                time.sleep(1.0)
                body = json.dumps({"choices": [{"message": {"content": "delta epsilon zeta"}}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in ["delta ", "epsilon ", "zeta"]:
                line = ("data: " + json.dumps({"choices": [{"delta": {"content": chunk}}]}) + "\n\n").encode("utf-8")
                self.wfile.write(line)
                self.wfile.flush()
                time.sleep(0.5)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        pass


HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_port(port: int, timeout_s: float = 5.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError(f"Timed out waiting for port {port} to become ready.")


def start_fake_server(kind: str, port: int) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        [sys.executable, "-c", FAKE_SERVER_SOURCE, kind, str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    wait_for_port(port)
    return process


def stop_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def read_available(process: subprocess.Popen[str]) -> str:
    if process.stdout is None:
        return ""
    chunks: list[str] = []
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while True:
            ready = selector.select(timeout=0)
            if not ready:
                break
            chunk = os.read(process.stdout.fileno(), 4096).decode("utf-8", errors="replace")
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        selector.close()
    return "".join(chunks)


def start_chat_pty(args: list[str], env: dict[str, str]) -> tuple[subprocess.Popen[str], int]:
    master_fd, slave_fd = pty.openpty()
    process = subprocess.Popen(
        args,
        cwd=str(ROOT),
        env=env,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
        text=False,
    )
    os.close(slave_fd)
    os.set_blocking(master_fd, False)
    return process, master_fd


def read_available_fd(fd: int) -> str:
    chunks: list[str] = []
    while True:
        try:
            chunk = os.read(fd, 4096)
        except BlockingIOError:
            break
        if not chunk:
            break
        chunks.append(chunk.decode("utf-8", errors="replace"))
    return "".join(chunks)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def wait_for_partial_stream(
    process: subprocess.Popen[bytes],
    *,
    expected_fragment: str,
    forbidden_fragment: str,
    timeout_s: float = 3.0,
) -> str:
    deadline = time.time() + timeout_s
    seen = ""
    while time.time() < deadline:
        seen += read_available(process)
        if expected_fragment in seen and forbidden_fragment not in seen:
            return seen
        if forbidden_fragment in seen or process.poll() is not None:
            break
        time.sleep(0.05)
    return seen


def run_streaming_answer_smoke(hypura_port: int) -> None:
    process = subprocess.Popen(
        [
            str(ANSWER_WRAPPER),
            "--mode",
            "speed",
            "--stream",
            "say three words",
        ],
        cwd=str(ROOT),
        env={
            **os.environ,
            "AUTO_START_SERVER": "0",
            "HYPURA_PORT": str(hypura_port),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    try:
        partial = wait_for_partial_stream(
            process,
            expected_fragment="alpha ",
            forbidden_fragment="beta",
        )
        assert_true("alpha " in partial and "beta" not in partial, "Expected partial Hypura stream before completion.")
        stdout, stderr = process.communicate(timeout=5)
        combined = partial + stdout.decode("utf-8", errors="replace")
        assert_true("alpha beta gamma" in combined, "Expected full Hypura streamed answer.")
        assert_true(process.returncode == 0, f"Hypura streaming wrapper failed: {stderr.decode('utf-8', errors='replace')}")
    finally:
        stop_process(process)


def run_flashmoe_streaming_smoke(flashmoe_port: int) -> None:
    process = subprocess.Popen(
        [str(FLASHMOE_ASK), "say three words"],
        cwd=str(ROOT),
        env={
            **os.environ,
            "FLASHMOE_PORT": str(flashmoe_port),
            "FLASHMOE_ASK_MODE": "server",
            "STREAM": "1",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    try:
        partial = wait_for_partial_stream(
            process,
            expected_fragment="delta ",
            forbidden_fragment="epsilon",
        )
        assert_true("delta " in partial and "epsilon" not in partial, "Expected partial Flash-MoE stream before completion.")
        stdout, stderr = process.communicate(timeout=5)
        combined = partial + stdout.decode("utf-8", errors="replace")
        assert_true("delta epsilon zeta" in combined, "Expected full Flash-MoE streamed answer.")
        assert_true(process.returncode == 0, f"Flash-MoE streaming wrapper failed: {stderr.decode('utf-8', errors='replace')}")
    finally:
        stop_process(process)


def run_buffered_chat_smoke(hypura_port: int) -> None:
    process, master_fd = start_chat_pty(
        [
            sys.executable,
            str(CHAT_CLIENT),
            "--mode",
            "speed",
            "--session",
            "stream-buffered-smoke",
            "--no-stream",
        ],
        {
            **os.environ,
            "HYPURA_PORT": str(hypura_port),
            "PYTHONUNBUFFERED": "1",
        },
    )
    try:
        time.sleep(0.6)
        banner = read_available_fd(master_fd)
        assert_true("Connected to Hypura" in banner, "Expected chat client to connect to fake Hypura.")
        os.write(master_fd, b"say three words again\n")
        time.sleep(0.35)
        early = read_available_fd(master_fd)
        assert_true("alpha beta gamma" not in early, "Buffered chat should not print the full answer early.")
        time.sleep(1.0)
        later = read_available_fd(master_fd)
        assert_true("hypura> alpha beta gamma" in later, "Buffered chat should print one complete answer block.")
        os.write(master_fd, b"/exit\n")
        process.wait(timeout=5)
        assert_true(process.returncode == 0, "Buffered chat smoke should exit cleanly.")
    finally:
        stop_process(process)
        os.close(master_fd)
        try:
            session_path = ROOT / "results" / "chat_sessions" / "stream-buffered-smoke.json"
            session_path.unlink()
        except FileNotFoundError:
            pass


def run_cleanup_smoke(hypura_process: subprocess.Popen[str], flashmoe_process: subprocess.Popen[str], hypura_port: int, flashmoe_port: int) -> None:
    state_file = "/tmp/gemma-streaming-smoke-auto-state.json"
    try:
        os.remove(state_file)
    except FileNotFoundError:
        pass
    process, master_fd = start_chat_pty(
        [
            sys.executable,
            str(CHAT_CLIENT),
            "--mode",
            "speed",
            "--session",
            "stream-cleanup-smoke",
            "--no-stream",
        ],
        {
            **os.environ,
            "AUTO_STATE_FILE": state_file,
            "HYPURA_PORT": str(hypura_port),
            "FLASHMOE_PORT": str(flashmoe_port),
            "PYTHONUNBUFFERED": "1",
        },
    )
    try:
        time.sleep(0.6)
        _ = read_available_fd(master_fd)
        os.write(master_fd, b"/cleanup\n")
        output = ""
        deadline = time.time() + 12
        while time.time() < deadline:
            output += read_available_fd(master_fd)
            if "Stopped Flash-MoE server" in output:
                break
            time.sleep(0.1)
        assert_true("Stopped Flash-MoE server" in output, f"Expected cleanup to stop the other runtime. Output was:\n{output}")
        assert_true(hypura_process.poll() is None, "Hypura should still be alive after cleanup.")
        deadline = time.time() + 3
        while time.time() < deadline and flashmoe_process.poll() is None:
            time.sleep(0.05)
        assert_true(flashmoe_process.poll() is not None, "Flash-MoE process should be terminated by cleanup.")
    finally:
        stop_process(process)
        os.close(master_fd)
        try:
            os.remove(state_file)
        except FileNotFoundError:
            pass
        try:
            session_path = ROOT / "results" / "chat_sessions" / "stream-cleanup-smoke.json"
            session_path.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    hypura_port = free_port()
    flashmoe_port = free_port()

    hypura_process = start_fake_server("hypura", hypura_port)
    flashmoe_process = start_fake_server("flashmoe", flashmoe_port)
    try:
        run_streaming_answer_smoke(hypura_port)
        run_flashmoe_streaming_smoke(flashmoe_port)
        run_buffered_chat_smoke(hypura_port)
        run_cleanup_smoke(hypura_process, flashmoe_process, hypura_port, flashmoe_port)
    finally:
        stop_process(hypura_process)
        stop_process(flashmoe_process)

    print("Streaming regression smoke passed.")
    print("  - Hypura front-door streaming produced partial output before completion")
    print("  - Flash-MoE server streaming produced partial output before completion")
    print("  - Chat buffered mode stayed buffered when streaming was off")
    print("  - Chat cleanup stopped the non-active runtime and kept the current one alive")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
