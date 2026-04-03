#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8080}"
MODEL_NAME="${MODEL_NAME:-}"
MAX_TOKENS="${MAX_TOKENS:-256}"
TEMPERATURE="${TEMPERATURE:-0.0}"
TOP_K="${TOP_K:-1}"
TOP_P="${TOP_P:-1.0}"
SEED="${SEED:-1}"
STREAM="${STREAM:-0}"

if [[ "$#" -gt 0 ]]; then
  PROMPT="$*"
elif [[ ! -t 0 ]]; then
  PROMPT="$(cat)"
else
  echo "Usage: $0 \"your prompt here\"" >&2
  echo "Or pipe a prompt into stdin." >&2
  exit 1
fi

if [[ -z "${MODEL_NAME}" ]]; then
  MODEL_NAME="$(
    HOST="${HOST}" PORT="${PORT}" python3 - <<'PY'
import json
import os
from urllib import request

url = f"http://{os.environ['HOST']}:{os.environ['PORT']}/api/tags"
with request.urlopen(url, timeout=30) as response:
    parsed = json.load(response)

models = parsed.get("models") or []
for item in models:
    name = item.get("name") or item.get("model")
    if name:
        print(name)
        break
else:
    raise SystemExit("No model name found in /api/tags")
PY
  )"
fi

HOST="${HOST}" \
PORT="${PORT}" \
MODEL_NAME="${MODEL_NAME}" \
MAX_TOKENS="${MAX_TOKENS}" \
TEMPERATURE="${TEMPERATURE}" \
TOP_K="${TOP_K}" \
TOP_P="${TOP_P}" \
SEED="${SEED}" \
STREAM="${STREAM}" \
PROMPT="${PROMPT}" \
python3 - <<'PY'
import json
import os
import sys
from urllib import request

payload = {
    "model": os.environ["MODEL_NAME"],
    "stream": os.environ["STREAM"] == "1",
    "messages": [{"role": "user", "content": os.environ["PROMPT"]}],
    "options": {
        "temperature": float(os.environ["TEMPERATURE"]),
        "top_k": int(os.environ["TOP_K"]),
        "top_p": float(os.environ["TOP_P"]),
        "num_predict": int(os.environ["MAX_TOKENS"]),
        "seed": int(os.environ["SEED"]),
    },
}

url = f"http://{os.environ['HOST']}:{os.environ['PORT']}/api/chat"
req = request.Request(
    url,
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)

with request.urlopen(req, timeout=600) as response:
    if os.environ["STREAM"] == "1":
        chunks = []
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
        if not answer:
            raise SystemExit(1)
        sys.stdout.write("\n")
        raise SystemExit(0)
    parsed = json.load(response)

text = parsed.get("message", {}).get("content", "")
if not text:
    sys.stderr.write(json.dumps(parsed, indent=2) + "\n")
    raise SystemExit(1)

sys.stdout.write(text.rstrip() + "\n")
PY
