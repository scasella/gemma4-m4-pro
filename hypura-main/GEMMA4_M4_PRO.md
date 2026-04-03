# Gemma 4 on a 24 GB M4 Pro

This repo now has a verified path for running `gemma-4-26B-A4B-it` on a 24 GB M4 Pro in text mode.

## Recommended model

Use the 4-bit GGUF:

- `../models/gemma-4-26B-A4B-it-Q4_K_M.gguf`

This is the one that was tested successfully here.

## Recommended way to run it

Use the Ollama-compatible server:

```sh
cd hypura-main
./scripts/serve-gemma4-m4pro.sh
```

Defaults:

- host: `127.0.0.1`
- port: `8080`
- context and runtime knobs: loaded from `../autoresearch/candidate.yaml` when that file exists
- preferred source: `../autoresearch/results/best_candidate.yaml`
- fallback source: `../autoresearch/candidate.yaml`
- dynamic memory reserve: current machine use plus `4 GB`

You can override them:

```sh
PORT=8082 CONTEXT=4096 ./scripts/serve-gemma4-m4pro.sh
```

Other useful overrides:

```sh
THREADS=10 THREADS_BATCH=14 BATCH=512 UBATCH=256 MIN_FREE_GB=4.0 ./scripts/serve-gemma4-m4pro.sh
```

You can also point the launcher at a different tracked candidate:

```sh
AUTORESEARCH_CANDIDATE=../autoresearch/candidate.yaml ./scripts/serve-gemma4-m4pro.sh
```

If both files exist, the launcher prefers the stable best config under `results/` over the editable next-candidate file.

To inspect what the launcher would do without loading the model:

```sh
PRINT_CONFIG_ONLY=1 ./scripts/serve-gemma4-m4pro.sh
```

That dry-run summary shows which config source won, what runtime settings it resolved to, and whether the memory reserve came from the candidate, the environment, or a fresh dynamic calculation.
It now also shows the machine's live used memory, available memory, and current swap use, so you can see why the reserve came out the way it did.

To see exactly what the benchmark loop currently considers the best validated setup:

```sh
cd ../autoresearch
uv run show_best.py
```

If you want a plain side-by-side summary of the fastest path and the lower-memory alternate:

```sh
cd ../autoresearch
uv run runtime_comparison.py
```

That writes `results/runtime_comparison.md`.

If you want the launcher to use the separately tracked current-state recommendation instead of the historical best, opt in explicitly:

```sh
AUTORESEARCH_USE_CURRENT_STATE=1 ./scripts/serve-gemma4-m4pro.sh
```

That only works when the current-state refresh has produced a valid current-state candidate. When it does, the launcher now follows the tested reserve settings from that current-state candidate too. Otherwise the launcher prints a short warning and falls back to the stable historical best.

If you want one command that refreshes the current-state recommendation and then launches it:

```sh
./scripts/serve-gemma4-current-state.sh
```

That wrapper now defaults to a faster single refresh pass for everyday use. If you want a slower, more stable refresh, pass `--repeat 2` or set `CURRENT_STATE_REPEAT=2`.
When you only want to inspect the resolved launch config, `PRINT_CONFIG_ONLY=1` now skips the refresh step by default and uses the most recent saved current-state record instead.
If you want the dry-run to force a fresh refresh anyway, set `REFRESH_CURRENT_STATE=1`.
If the last current-state refresh already found no safe winner and the machine still looks similarly loaded, the refresh step now skips the re-benchmark and reuses that failed result instead of burning time to rediscover the same outcome.

You can pass the refresh arguments through, for example:

```sh
PORT=8082 ./scripts/serve-gemma4-current-state.sh --repeat 1
```

## Quick check

With the server running:

```sh
cd hypura-main
./scripts/smoke-gemma4-m4pro.sh
```

The smoke script asks the server which model is loaded, so you do not need to keep the model name in sync by hand.

Expected answer:

```json
{"message":{"role":"assistant","content":"4"}}
```

## Ask it something

With the server running:

```sh
cd hypura-main
./scripts/ask-gemma4-m4pro.sh "Answer with one lowercase word only: what is the capital of France?"
STREAM=1 ./scripts/ask-gemma4-m4pro.sh "Tell me a short sentence about Paris."
```

The ask script also auto-detects the active served model unless you override `MODEL_NAME`.
Set `STREAM=1` if you want tokens to print as they arrive instead of waiting for the whole answer.

Expected answer:

```text
paris
```

If you want one prompt entrypoint for both runtimes, use the shared wrapper in `autoresearch`:

```sh
cd ../autoresearch
./gemma4_answer.sh --mode speed "your prompt here"
./gemma4_answer.sh --mode memory "your prompt here"
./gemma4_answer.sh --mode auto "your prompt here"
```

`speed` uses the running Hypura server.
`memory` uses the Flash-MoE alternate directly.
`auto` uses the Hypura server when it is reachable and falls back to Flash-MoE when it is not.

## Interactive CLI

The interactive CLI path was also fixed and verified:

```sh
cd hypura-main
target/release/hypura run ../models/gemma-4-26B-A4B-it-Q4_K_M.gguf --interactive --context 4096 --max-tokens 256
```

## What changed

- Gemma 4 prompt formatting now matches the model's turn format.
- Prompt tokenization now treats chat markers like `<bos>` and `<|turn>` as real control tokens instead of plain text.
- Gemma 4's per-layer KV-head metadata is now read correctly, which fixes memory planning on Apple Silicon.
- CLI chat now reuses the same loaded-model path as the server, so the stable server behavior also applies to interactive use.

## Notes

- This is the practical path for text/chat today.
- The default recommended path is still Hypura.
- A separate Flash-MoE alternate now exists under `../autoresearch` for cases where you want a slower but simpler memory-pressure tradeoff.
- The current implementation is for text use, not image input.
- The launcher now prefers a stable exported best-config file from the benchmark loop, so everyday use does not drift when you edit the next experimental candidate.
