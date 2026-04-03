#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLASHMOE_HOST="${FLASHMOE_HOST:-127.0.0.1}"
FLASHMOE_PORT="${FLASHMOE_PORT:-8097}"
FLASHMOE_ASK_MODE="${FLASHMOE_ASK_MODE:-auto}"
STREAM="${STREAM:-0}"
SERVER_HINT="${SCRIPT_DIR}/flashmoe_gemma4_serve.sh"

if [[ "$#" -gt 0 ]]; then
  PROMPT="$*"
elif [[ ! -t 0 ]]; then
  PROMPT="$(cat)"
else
  echo "Usage: $0 \"your prompt here\"" >&2
  echo "Or pipe a prompt into stdin." >&2
  exit 1
fi

server_available() {
  FLASHMOE_HOST="${FLASHMOE_HOST}" FLASHMOE_PORT="${FLASHMOE_PORT}" python3 - <<'PY' >/dev/null 2>&1
import os
from urllib import request

url = f"http://{os.environ['FLASHMOE_HOST']}:{os.environ['FLASHMOE_PORT']}/health"
with request.urlopen(url, timeout=2) as response:
    if response.status != 200:
        raise SystemExit(1)
PY
}

run_server() {
  FLASHMOE_HOST="${FLASHMOE_HOST}" FLASHMOE_PORT="${FLASHMOE_PORT}" PROMPT="${PROMPT}" STREAM="${STREAM}" python3 - <<'PY'
import json
import os
import sys
from urllib import request

url = f"http://{os.environ['FLASHMOE_HOST']}:{os.environ['FLASHMOE_PORT']}/v1/chat/completions"
payload = {
    "messages": [{"role": "user", "content": os.environ["PROMPT"]}],
    "temperature": 0,
    "top_k": 1,
    "top_p": 1,
    "max_tokens": 256,
    "stream": os.environ["STREAM"] == "1",
}
req = request.Request(
    url,
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
)
with request.urlopen(req, timeout=300) as response:
    if os.environ["STREAM"] == "1":
        chunks = []
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
            raise SystemExit(1)
        sys.stdout.write("\n")
        raise SystemExit(0)
    body = json.loads(response.read().decode("utf-8"))

print(body["choices"][0]["message"]["content"])
PY
}

run_cli() {
  if [[ "${STREAM}" == "1" ]]; then
    echo "Streaming is only available through the Flash-MoE server path; falling back to the one-shot CLI output." >&2
  fi
  RAW_OUTPUT="$(
    PROMPT="${PROMPT}" TOKENS="${TOKENS:-256}" "${SCRIPT_DIR}/flashmoe_gemma4_best.sh" "${PROMPT}" 2>&1
  )"

  RAW_OUTPUT="${RAW_OUTPUT}" PROMPT="${PROMPT}" python3 - <<'PY'
import os
import sys

text = os.environ["RAW_OUTPUT"].replace("\r\n", "\n")
prompt = os.environ["PROMPT"].strip()

marker = f"> {prompt}"
if marker in text:
    text = text.split(marker, 1)[1]

for stop in [
    "llama_memory_breakdown_print:",
    "log_runtime_summary:",
    "[ Prompt:",
    "Exiting...",
]:
    if stop in text:
        text = text.split(stop, 1)[0]

lines = [line.rstrip() for line in text.splitlines()]
non_empty = [line.strip() for line in lines if line.strip()]
if not non_empty:
    sys.stderr.write("Could not extract answer from Flash-MoE output.\n")
    sys.stderr.write(os.environ["RAW_OUTPUT"] + "\n")
    raise SystemExit(1)

answer = non_empty[-1]
sys.stdout.write(answer + "\n")
PY
}

case "${FLASHMOE_ASK_MODE}" in
  auto)
    if server_available; then
      run_server
    else
      run_cli
    fi
    ;;
  server)
    if ! server_available; then
      echo "The Flash-MoE server is not reachable at ${FLASHMOE_HOST}:${FLASHMOE_PORT}." >&2
      echo "Start it with: ${SERVER_HINT}" >&2
      exit 1
    fi
    run_server
    ;;
  cli)
    run_cli
    ;;
  *)
    echo "Unsupported FLASHMOE_ASK_MODE: ${FLASHMOE_ASK_MODE}" >&2
    exit 1
    ;;
esac
