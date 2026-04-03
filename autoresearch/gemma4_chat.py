#!/usr/bin/env python3
"""
Interactive chat client for the local Gemma 4 runtimes.

Usage:
    python3 gemma4_chat.py --mode auto
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib import request


ROOT = Path(__file__).resolve().parent
AUTO_LAUNCHER = ROOT / "serve_gemma4_auto.sh"
SERVER_STARTER = ROOT / "gemma4_server_start.sh"
SERVER_STOPPER = ROOT / "gemma4_server_stop.sh"
SESSIONS_DIR = ROOT / "results" / "chat_sessions"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive chat for the local Gemma 4 runtimes.")
    parser.add_argument("--mode", choices=["auto", "speed", "memory"], default="auto")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Stop the other live runtime first before starting the chosen one.",
    )
    parser.add_argument("--session", default="", help="Session name to create or resume.")
    parser.add_argument("--list-sessions", action="store_true", help="List saved chat sessions and exit.")
    parser.add_argument("--show-session", default="", help="Show one saved session and exit.")
    parser.add_argument("--delete-session", default="", help="Delete one saved session and exit.")
    parser.add_argument("--system", default="", help="Optional system prompt for the whole chat session.")
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-k", type=int, default=1)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--no-stream", action="store_true", help="Disable token streaming in the interactive chat.")
    parser.add_argument("--auto-start-timeout", type=int, default=int(os.environ.get("AUTO_START_TIMEOUT_S", "180")))
    parser.add_argument("--auto-start-log", default=os.environ.get("AUTO_START_LOG", ""))
    return parser.parse_args()


def slugify_session(name: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", name.strip()).strip("-").lower()
    return value or f"session-{time.strftime('%Y%m%dT%H%M%S', time.localtime())}"


def session_path(session_name: str) -> Path:
    return SESSIONS_DIR / f"{slugify_session(session_name)}.json"


def list_sessions() -> int:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    sessions = sorted(SESSIONS_DIR.glob("*.json"))
    if not sessions:
        print("No saved chat sessions.")
        return 0
    print("Saved chat sessions")
    for path in sessions:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            print(f"  {path.stem}: unreadable")
            continue
        updated = payload.get("updated_at", "")
        turns = int(payload.get("turns", 0))
        runtime = payload.get("runtime_name", payload.get("runtime", ""))
        print(f"  {path.stem}: {turns} turns, runtime {runtime}, updated {updated}")
    return 0


def session_summaries(current_name: str = "") -> list[str]:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    sessions = sorted(SESSIONS_DIR.glob("*.json"))
    summaries: list[str] = []
    for path in sessions:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            label = f"{path.stem}: unreadable"
            summaries.append(f"* {label}" if path.stem == current_name else f"  {label}")
            continue
        updated = payload.get("updated_at", "")
        turns = int(payload.get("turns", 0))
        runtime = payload.get("runtime_name", payload.get("runtime", ""))
        label = f"{path.stem}: {turns} turns, runtime {runtime}, updated {updated}"
        summaries.append(f"* {label}" if path.stem == current_name else f"  {label}")
    return summaries


def load_session(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def show_session(session_name: str) -> int:
    path = session_path(session_name)
    if not path.exists():
        print(f"No saved session named {path.stem}.")
        return 1
    payload = load_session(path)
    print("Saved chat session")
    print(f"  Name:         {path.stem}")
    print(f"  Runtime:      {payload.get('runtime_name', payload.get('runtime', ''))}")
    print(f"  Host:         {payload.get('host', '')}")
    print(f"  Port:         {payload.get('port', '')}")
    print(f"  Turns:        {payload.get('turns', 0)}")
    print(f"  Created:      {payload.get('created_at', '')}")
    print(f"  Updated:      {payload.get('updated_at', '')}")
    if payload.get("reason"):
        print(f"  Reason:       {payload.get('reason')}")
    if payload.get("system_prompt"):
        print(f"  System:       {payload.get('system_prompt')}")
    print(f"  File:         {path}")
    print()
    print("Transcript")
    messages = payload.get("messages", [])
    if not isinstance(messages, list) or not messages:
        print("  (empty)")
        return 0
    for item in messages:
        role = str(item.get("role", "unknown"))
        content = str(item.get("content", ""))
        print(f"  {role}: {content}")
    return 0


def delete_session(session_name: str) -> int:
    path = session_path(session_name)
    if not path.exists():
        print(f"No saved session named {path.stem}.")
        return 1
    path.unlink()
    print(f"Deleted saved session {path.stem}.")
    return 0


def save_session(
    path: Path,
    *,
    session_name: str,
    requested_mode: str,
    runtime: str,
    runtime_name: str,
    host: str,
    port: int,
    reason: str,
    system_prompt: str,
    messages: list[dict[str, str]],
) -> None:
    created_at = ""
    if path.exists():
        try:
            created_at = json.loads(path.read_text(encoding="utf-8")).get("created_at", "")
        except Exception:
            created_at = ""
    if not created_at:
        created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload = {
        "session_name": session_name,
        "requested_mode": requested_mode,
        "runtime": runtime,
        "runtime_name": runtime_name,
        "host": host,
        "port": port,
        "reason": reason,
        "system_prompt": system_prompt,
        "messages": messages,
        "turns": sum(1 for item in messages if item.get("role") == "user"),
        "created_at": created_at,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def has_meaningful_content(messages: list[dict[str, str]]) -> bool:
    return any(item.get("role") in {"user", "assistant"} and str(item.get("content", "")).strip() for item in messages)


def persist_session_if_needed(
    path: Path,
    *,
    session_name: str,
    requested_mode: str,
    runtime: str,
    runtime_name: str,
    host: str,
    port: int,
    reason: str,
    system_prompt: str,
    messages: list[dict[str, str]],
) -> None:
    if not has_meaningful_content(messages):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    save_session(
        path,
        session_name=session_name,
        requested_mode=requested_mode,
        runtime=runtime,
        runtime_name=runtime_name,
        host=host,
        port=port,
        reason=reason,
        system_prompt=system_prompt,
        messages=messages,
    )


def print_chat_help() -> None:
    print("Commands:")
    print("  /status           show runtime, endpoint, turn count, active session, and the current auto recommendation")
    print("  /switch MODE      switch this chat to speed, memory, or auto without leaving the session")
    print("                    add --replace to stop the other runtime during the handoff")
    print("  /cleanup          stop the other runtime and keep this chat on the current one")
    print("  /stream on|off    enable or disable token streaming for new replies")
    print("  /clear            clear the current conversation history")
    print("  /sessions         list the saved chat sessions")
    print("  /saveas NAME      save the current conversation under a new session name and continue there")
    print("  /rename NAME      rename the current saved session and continue there")
    print("  /delete NAME      delete another saved session by name")
    print("  /exit or /quit    leave the chat but keep the server running")
    print("  /help             show this help")


def request_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def streaming_request(url: str, payload: dict[str, Any], timeout: int):
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return request.urlopen(req, timeout=timeout)


def server_ready(runtime: str, host: str, port: int) -> bool:
    if runtime == "speed":
        url = f"http://{host}:{port}/api/tags"
    else:
        url = f"http://{host}:{port}/health"
    try:
        with request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def decision_json(mode: str, env: dict[str, str]) -> dict[str, Any]:
    output = subprocess.check_output(
        [str(AUTO_LAUNCHER)],
        env={**os.environ, **env, "MODE": mode, "PRINT_DECISION_JSON": "1"},
        text=True,
    )
    return json.loads(output.strip().splitlines()[-1])


def live_runtime_status(env: dict[str, str]) -> list[str]:
    statuses: list[str] = []
    hypura_host = env["HYPURA_HOST"]
    hypura_port = int(env["HYPURA_PORT"])
    flashmoe_host = env["FLASHMOE_HOST"]
    flashmoe_port = int(env["FLASHMOE_PORT"])
    if server_ready("speed", hypura_host, hypura_port):
        statuses.append(f"Hypura {hypura_host}:{hypura_port}")
    if server_ready("memory", flashmoe_host, flashmoe_port):
        statuses.append(f"Flash-MoE {flashmoe_host}:{flashmoe_port}")
    return statuses

def start_runtime(mode: str, timeout_s: int, log_path: str, env: dict[str, str]) -> None:
    command = [str(SERVER_STARTER), "--mode", mode]
    if env.get("REPLACE_LIVE_RUNTIME") == "1":
        command.append("--replace")
    run_env = {
        **os.environ,
        **env,
        "AUTO_START_TIMEOUT_S": str(timeout_s),
        "AUTO_START_LOG": log_path,
        "PRINT_STATUS_AFTER": "0",
    }
    result = subprocess.run(
        command,
        env=run_env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return
    detail = "\n".join(
        part for part in (result.stdout.strip(), result.stderr.strip()) if part
    )
    raise RuntimeError(detail or f"Failed to start runtime for mode {mode}.")


def connect_runtime(
    mode: str,
    *,
    timeout_s: int,
    log_path: str,
    env: dict[str, str],
    replace_live_runtime: bool,
) -> tuple[dict[str, Any], str, str, int, str, str | None]:
    decision = decision_json(mode, env)
    runtime = str(decision["chosen_runtime"])
    host = str(decision["target_host"])
    port = int(decision["target_port"])
    runtime_name = str(decision["target_name"])

    if replace_live_runtime or not server_ready(runtime, host, port):
        print(
            f"Preparing {runtime_name} on {host}:{port}.",
            file=sys.stderr,
        )
        start_env = {**env, "REPLACE_LIVE_RUNTIME": "1" if replace_live_runtime else "0"}
        start_runtime(mode, timeout_s, log_path, start_env)
        decision = decision_json(mode, env)
        runtime = str(decision["chosen_runtime"])
        host = str(decision["target_host"])
        port = int(decision["target_port"])
        runtime_name = str(decision["target_name"])

    hypura_model = hypura_model_name(host, port) if runtime == "speed" else None
    return decision, runtime, host, port, runtime_name, hypura_model


def cleanup_other_runtime(current_runtime: str, env: dict[str, str]) -> tuple[bool, str]:
    other_runtime = "flashmoe" if current_runtime == "speed" else "hypura"
    run_env = {
        **os.environ,
        **env,
    }
    if "AUTO_STATE_FILE" in os.environ:
        run_env["AUTO_STATE_FILE"] = os.environ["AUTO_STATE_FILE"]
    result = subprocess.run(
        [str(SERVER_STOPPER), "--runtime", other_runtime],
        env=run_env,
        capture_output=True,
        text=True,
        check=False,
    )
    detail = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part).strip()
    return result.returncode == 0, detail


def hypura_model_name(host: str, port: int) -> str:
    with request.urlopen(f"http://{host}:{port}/api/tags", timeout=30) as response:
        parsed = json.load(response)
    models = parsed.get("models") or []
    for item in models:
        name = item.get("name") or item.get("model")
        if name:
            return str(name)
    raise RuntimeError("No model name found from Hypura /api/tags")


def chat_once(
    runtime: str,
    host: str,
    port: int,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    temperature: float,
    top_k: int,
    top_p: float,
    seed: int,
    hypura_model: str | None,
) -> str:
    if runtime == "speed":
        payload = {
            "model": hypura_model,
            "stream": False,
            "messages": messages,
            "options": {
                "temperature": temperature,
                "top_k": top_k,
                "top_p": top_p,
                "num_predict": max_tokens,
                "seed": seed,
            },
        }
        parsed = request_json(f"http://{host}:{port}/api/chat", payload, timeout=600)
        text = parsed.get("message", {}).get("content", "")
    else:
        payload = {
            "messages": messages,
            "temperature": temperature,
            "top_k": top_k,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "seed": seed,
        }
        parsed = request_json(f"http://{host}:{port}/v1/chat/completions", payload, timeout=600)
        text = ((parsed.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    if not text:
        raise RuntimeError("Model returned an empty response.")
    return text.rstrip()


def stream_chat_once(
    runtime: str,
    host: str,
    port: int,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    temperature: float,
    top_k: int,
    top_p: float,
    seed: int,
    hypura_model: str | None,
) -> str:
    if runtime == "speed":
        payload = {
            "model": hypura_model,
            "stream": True,
            "messages": messages,
            "options": {
                "temperature": temperature,
                "top_k": top_k,
                "top_p": top_p,
                "num_predict": max_tokens,
                "seed": seed,
            },
        }
        url = f"http://{host}:{port}/api/chat"
        chunks: list[str] = []
        with streaming_request(url, payload, timeout=600) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = str((parsed.get("message") or {}).get("content") or "")
                if text:
                    sys.stdout.write(text)
                    sys.stdout.flush()
                    chunks.append(text)
                if parsed.get("done"):
                    break
        answer = "".join(chunks).rstrip()
    else:
        payload = {
            "messages": messages,
            "temperature": temperature,
            "top_k": top_k,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "seed": seed,
            "stream": True,
        }
        url = f"http://{host}:{port}/v1/chat/completions"
        chunks = []
        with streaming_request(url, payload, timeout=600) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[len("data:"):].strip()
                if not line or line == "[DONE]":
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                choice = ((parsed.get("choices") or [{}])[0] if isinstance(parsed, dict) else {})
                delta = choice.get("delta") or {}
                message = choice.get("message") or {}
                text = str(delta.get("content") or message.get("content") or "")
                if text:
                    sys.stdout.write(text)
                    sys.stdout.flush()
                    chunks.append(text)
        answer = "".join(chunks).rstrip()
    if not answer:
        raise RuntimeError("Model returned an empty response.")
    return answer


def main() -> int:
    args = parse_args()
    if args.list_sessions:
        return list_sessions()
    if args.show_session:
        return show_session(args.show_session)
    if args.delete_session:
        return delete_session(args.delete_session)

    env = {
        "HYPURA_HOST": os.environ.get("HYPURA_HOST", os.environ.get("HOST", "127.0.0.1")),
        "HYPURA_PORT": os.environ.get("HYPURA_PORT", os.environ.get("PORT", "8080")),
        "FLASHMOE_HOST": os.environ.get("FLASHMOE_HOST", os.environ.get("HOST", "127.0.0.1")),
        "FLASHMOE_PORT": os.environ.get("FLASHMOE_PORT", os.environ.get("PORT", "8097")),
        "AUTO_AVAILABLE_GB_OVERRIDE": os.environ.get("AUTO_AVAILABLE_GB_OVERRIDE", ""),
        "AUTO_SWAP_USED_GB_OVERRIDE": os.environ.get("AUTO_SWAP_USED_GB_OVERRIDE", ""),
        "REPLACE_LIVE_RUNTIME": "1" if args.replace else "0",
    }

    current_mode = args.mode
    stream_responses = (not args.no_stream) and sys.stdout.isatty()
    decision, runtime, host, port, runtime_name, hypura_model = connect_runtime(
        args.mode,
        timeout_s=args.auto_start_timeout,
        log_path=args.auto_start_log,
        env=env,
        replace_live_runtime=args.replace,
    )

    session_name = args.session or f"{time.strftime('%Y%m%d-%H%M%S', time.localtime())}-{runtime}"
    session_file = session_path(session_name)
    history: list[dict[str, str]] = []
    system_prompt = args.system
    resumed = False
    if session_file.exists():
        payload = load_session(session_file)
        stored_messages = payload.get("messages", [])
        if isinstance(stored_messages, list):
            history = [
                {"role": str(item.get("role", "")), "content": str(item.get("content", ""))}
                for item in stored_messages
                if item.get("role") and item.get("content") is not None
            ]
        if not system_prompt:
            system_prompt = str(payload.get("system_prompt", ""))
        resumed = True
    elif system_prompt:
        history.append({"role": "system", "content": system_prompt})

    persist_session_if_needed(
        session_file,
        session_name=session_name,
        requested_mode=current_mode,
        runtime=runtime,
        runtime_name=runtime_name,
        host=host,
        port=port,
        reason=str(decision["reason"]),
        system_prompt=system_prompt,
        messages=history,
    )

    print(f"Connected to {runtime_name} on {host}:{port}.")
    print(f"Session: {session_file.stem}")
    if resumed:
        print(f"Resumed with {sum(1 for item in history if item['role'] == 'user')} prior turns.")
    print("Commands: /help, /status, /switch MODE [--replace], /cleanup, /stream on|off, /clear, /sessions, /saveas NAME, /rename NAME, /delete NAME, /exit")

    while True:
        try:
            user_text = input("you> ").strip()
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print()
            break

        if not user_text:
            continue
        if user_text in {"/exit", "/quit"}:
            break
        if user_text == "/help":
            print_chat_help()
            continue
        if user_text == "/clear":
            history = [{"role": "system", "content": system_prompt}] if system_prompt else []
            persist_session_if_needed(
                session_file,
                session_name=session_name,
                requested_mode=current_mode,
                runtime=runtime,
                runtime_name=runtime_name,
                host=host,
                port=port,
                reason=str(decision["reason"]),
                system_prompt=system_prompt,
                messages=history,
            )
            print("history cleared")
            continue
        if user_text == "/sessions":
            summaries = session_summaries(session_file.stem)
            if not summaries:
                print("No saved chat sessions.")
            else:
                print("Saved chat sessions")
                for line in summaries:
                    print(line)
            continue
        if user_text.startswith("/saveas"):
            parts = user_text.split(maxsplit=1)
            if len(parts) != 2 or not parts[1].strip():
                print("Usage: /saveas NAME")
                continue
            new_name = parts[1].strip()
            new_file = session_path(new_name)
            persist_session_if_needed(
                new_file,
                session_name=new_name,
                requested_mode=current_mode,
                runtime=runtime,
                runtime_name=runtime_name,
                host=host,
                port=port,
                reason=str(decision["reason"]),
                system_prompt=system_prompt,
                messages=history,
            )
            session_name = new_name
            session_file = new_file
            print(f"session switched to {session_file.stem}")
            continue
        if user_text.startswith("/rename"):
            parts = user_text.split(maxsplit=1)
            if len(parts) != 2 or not parts[1].strip():
                print("Usage: /rename NAME")
                continue
            new_name = parts[1].strip()
            new_file = session_path(new_name)
            if new_file == session_file:
                print(f"session already named {session_file.stem}")
                continue
            if new_file.exists():
                print(f"session {new_file.stem} already exists")
                continue
            session_file.parent.mkdir(parents=True, exist_ok=True)
            if session_file.exists():
                session_file.rename(new_file)
            else:
                persist_session_if_needed(
                    new_file,
                    session_name=new_name,
                    requested_mode=current_mode,
                    runtime=runtime,
                    runtime_name=runtime_name,
                    host=host,
                    port=port,
                    reason=str(decision["reason"]),
                    system_prompt=system_prompt,
                    messages=history,
                )
            session_name = new_name
            session_file = new_file
            print(f"session renamed to {session_file.stem}")
            continue
        if user_text.startswith("/delete"):
            parts = user_text.split(maxsplit=1)
            if len(parts) != 2 or not parts[1].strip():
                print("Usage: /delete NAME")
                continue
            target_file = session_path(parts[1].strip())
            if target_file == session_file:
                print("cannot delete the active session; switch or exit first")
                continue
            if not target_file.exists():
                print(f"no saved session named {target_file.stem}")
                continue
            target_file.unlink()
            print(f"deleted session {target_file.stem}")
            continue
        if user_text == "/status":
            current_auto = decision_json("auto", env)
            live_statuses = live_runtime_status(env)
            print(f"runtime: {runtime_name} ({runtime})")
            print(f"endpoint: {host}:{port}")
            print(f"streaming: {'on' if stream_responses else 'off'}")
            print(f"turns: {sum(1 for item in history if item['role'] == 'user')}")
            print(f"reason: {decision['reason']}")
            print(f"session: {session_file}")
            if live_statuses:
                print(f"live now: {', '.join(live_statuses)}")
            else:
                print("live now: none")
            print(
                "auto now: "
                f"{current_auto['target_name']} on {current_auto['target_host']}:{current_auto['target_port']}"
            )
            print(f"auto reason: {current_auto['reason']}")
            continue
        if user_text == "/cleanup":
            ok, detail = cleanup_other_runtime(runtime, env)
            if detail:
                print(detail)
            elif ok:
                print("cleanup complete")
            else:
                print("cleanup failed")
            continue
        if user_text.startswith("/stream"):
            parts = user_text.split()
            if len(parts) != 2 or parts[1] not in {"on", "off"}:
                print("Usage: /stream on|off")
                continue
            stream_responses = parts[1] == "on"
            print(f"streaming {'enabled' if stream_responses else 'disabled'}")
            continue
        if user_text.startswith("/switch"):
            parts = user_text.split()
            if len(parts) < 2:
                print("Usage: /switch speed|memory|auto [--replace]")
                continue
            switch_mode = parts[1].strip()
            extra_flags = parts[2:]
            if switch_mode not in {"speed", "memory", "auto"}:
                print("Usage: /switch speed|memory|auto [--replace]")
                continue
            if any(flag != "--replace" for flag in extra_flags):
                print("Usage: /switch speed|memory|auto [--replace]")
                continue
            switch_replace = args.replace or ("--replace" in extra_flags)
            try:
                decision, runtime, host, port, runtime_name, hypura_model = connect_runtime(
                    switch_mode,
                    timeout_s=args.auto_start_timeout,
                    log_path=args.auto_start_log,
                    env=env,
                    replace_live_runtime=switch_replace,
                )
            except Exception as exc:
                print(f"switch failed: {exc}")
                continue
            current_mode = switch_mode
            persist_session_if_needed(
                session_file,
                session_name=session_name,
                requested_mode=current_mode,
                runtime=runtime,
                runtime_name=runtime_name,
                host=host,
                port=port,
                reason=str(decision["reason"]),
                system_prompt=system_prompt,
                messages=history,
            )
            if switch_replace:
                print(f"switched to {runtime_name} on {host}:{port} and replaced the other runtime")
            else:
                print(f"switched to {runtime_name} on {host}:{port}")
            continue

        history.append({"role": "user", "content": user_text})
        try:
            if stream_responses:
                print(f"{runtime_name.lower()}> ", end="", flush=True)
                answer = stream_chat_once(
                    runtime,
                    host,
                    port,
                    history,
                    max_tokens=args.max_tokens,
                    temperature=args.temperature,
                    top_k=args.top_k,
                    top_p=args.top_p,
                    seed=args.seed,
                    hypura_model=hypura_model,
                )
                print()
            else:
                answer = chat_once(
                    runtime,
                    host,
                    port,
                    history,
                    max_tokens=args.max_tokens,
                    temperature=args.temperature,
                    top_k=args.top_k,
                    top_p=args.top_p,
                    seed=args.seed,
                    hypura_model=hypura_model,
                )
                print(f"{runtime_name.lower()}> {answer}")
        except KeyboardInterrupt:
            print()
            break
        history.append({"role": "assistant", "content": answer})
        persist_session_if_needed(
            session_file,
            session_name=session_name,
            requested_mode=current_mode,
            runtime=runtime,
            runtime_name=runtime_name,
            host=host,
            port=port,
            reason=str(decision["reason"]),
            system_prompt=system_prompt,
            messages=history,
        )

    print("Session ended. The server stays running until you stop it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
