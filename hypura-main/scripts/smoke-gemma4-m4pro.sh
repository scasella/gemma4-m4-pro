#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8080}"
MODEL_NAME="${MODEL_NAME:-}"

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

PAYLOAD="$(
  MODEL_NAME="${MODEL_NAME}" python3 - <<'PY'
import json
import os

print(json.dumps({
    "model": os.environ["MODEL_NAME"],
    "stream": False,
    "messages": [
        {"role": "user", "content": "What is 2+2? Answer with one character."}
    ],
    "options": {
        "temperature": 0.0,
        "top_k": 1,
        "top_p": 1.0,
        "num_predict": 16,
        "seed": 1,
    },
}))
PY
)"

curl -s "http://${HOST}:${PORT}/api/chat" \
  -H 'Content-Type: application/json' \
  -d "${PAYLOAD}"
