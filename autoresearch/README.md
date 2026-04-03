# autoresearch

This fork turns `autoresearch` into a local research loop for one job: find the fastest safe way to run **Gemma 4 26B A4B-it Q4_K_M** on a **24 GB M4 Pro MacBook Pro**.

The loop is still agent-driven. The repo gives the agent a fixed benchmark harness, a tracked experiment spec, and a keep-or-discard record of each run. The goal is to maximize **generation tokens per second** while always keeping at least **4 GB free** for other apps.

## Lean release note

This public repo keeps the benchmark harness and curated results, but leaves out the local model file, the huge Flash-MoE sidecar data, and the optional Flash-MoE source checkout.

- The fast Hypura path is included here.
- The Flash-MoE path is still supported, but you need to clone the external runtime checkout described in [`../SETUP_EXTERNALS.md`](../SETUP_EXTERNALS.md).
- The `llama.cpp` baseline is now optional for this lean public repo.

The exact saved result files intentionally kept in this public repo are listed in [`results/curated_results_manifest.json`](./results/curated_results_manifest.json).

## What changed

The original repo searched over training code on an NVIDIA GPU. This fork searches over local runtime settings on macOS.

- `prepare.py` verifies the model and both backends, builds a release Hypura binary if needed, and records the machine profile.
- `train.py` reads `candidate.yaml`, runs correctness checks, warmup, and measured benchmark passes, then logs the result.
- `candidate.yaml` is the tracked experiment spec the agent edits during knob-first work.
- `program.md` tells the agent exactly how to search and when deeper code changes are allowed.
- `progress.py` plots the search history from `results.tsv`.
- `TURBOQUANT_GEMMA4_NOTES.md` records what was learned from exploring the TurboQuant+ stack and whether it is a realistic next step for this exact model and Mac.

## Backends

The first phase compares three local runtime paths:

- `Hypura` via `../hypura-main/target/release/hypura`
- optional `llama.cpp` baseline via `../llama-baseline-build/bin/llama-cli`
- optional `Flash-MoE llama.cpp` via `../anemll-flash-llama.cpp-gemma4/build-smoke/bin/llama-cli`
- optional `Flash-MoE llama.cpp` resident server via `../anemll-flash-llama.cpp-gemma4/build-smoke/bin/llama-server`

`flash-moe-main-2` remains a source of search ideas and heuristics, but the Anemll Gemma 4 branch is now a first-class measured backend in this fork.

For fairness, Hypura runs through its local chat server path for the full measured flow, not only for the short sanity checks. The harness now loads Hypura once, sends the correctness prompts through `/api/chat`, runs warmups and measured passes through the same endpoint, then shuts the server down.

When the backend is `hypura`, the harness also computes a memory reserve from the machine's current memory use plus the configured free-memory floor, then passes that reserve into Hypura. This keeps Hypura's planner aligned with the benchmark rule instead of letting it assume the whole machine is available. You can override that behavior with `backend_config.memory_reserve_gb`, `backend_config.keep_resident_headroom_gb`, `backend_config.preload_headroom_gb`, and `backend_config.gpu_runtime_overhead_gb`.

When the backend is `flashmoe`, the harness runs the Anemll Gemma 4 fork through the same correctness prompts, warmup pass, and measured passes as the other backends. The harness also does a sidecar preflight first. If `backend_config.moe_mode` is `slot-bank`, the candidate is rejected immediately unless the sidecar covers all routed tensors.

When the backend is `flashmoe_server`, the harness starts the Anemll server once, keeps prompt caching off by default for fairness, runs the same correctness prompts and measured passes over HTTP, then shuts the server down. This measures the warm resident-server path without relying on exact-prompt cache hits.

## Benchmark rules

Every candidate is judged the same way:

- Two short correctness prompts must pass first.
- One warmup run is ignored.
- Three measured runs produce the score.
- The score is the **median generation tok/s**.
- Runs are rejected if free memory drops below **4 GB** twice in a row.
- Runs are rejected if swap growth exceeds **0.25 GB**.

Exploratory one-pass runs are still useful for sweeping knobs, but they are not allowed to replace `results/best.json`. Only candidates with at least three measured passes can become the official best result.

Secondary metrics are also logged:

- prompt tokens per second
- approximate time to first token
- approximate load time
- minimum free memory seen

## Quick start

Requirements:

- macOS on Apple Silicon
- Python 3.10+
- `uv`
- the Gemma GGUF placed at `../models/gemma-4-26B-A4B-it-Q4_K_M.gguf`
- optional external Flash-MoE checkout if you want the lower-memory path

Setup:

```bash
uv sync
uv run prepare.py
```

Run the current candidate:

```bash
uv run train.py
```

Run the tracked Flash-MoE comparison candidate:

```bash
uv run train.py --candidate candidates/flashmoe-slot-bank-16.yaml
```

Run the tracked resident-server Flash-MoE comparison candidate:

```bash
uv run train.py --candidate candidates/flashmoe-server-slot-bank-16.yaml
```

Run a one-off probe without editing `candidate.yaml`:

```bash
uv run train.py \
  --override description='"hypura 4096 tb13 probe"' \
  --override measured_runs=1 \
  --override backend_config.threads_batch=13 \
  --skip-plot
```

Refresh the chart:

```bash
uv run progress.py
```

Refresh the exported stable best config from the saved winning artifact:

```bash
uv run sync_best.py
```

Refresh the exported stable Flash-MoE alternate config from the saved best Flash-MoE run:

```bash
uv run sync_flashmoe_best.py
```

Refresh the exported stable Flash-MoE resident-server config from the saved best resident-server run:

```bash
uv run sync_flashmoe_server_best.py
```

Show the current best validated setup and whether `candidate.yaml` has drifted away from it:

```bash
uv run show_best.py
```

`show_best.py` now also prints a short summary of recent reruns of the same core setup, so you can see when the historical best result is drifting away from current machine-state reality.
It also prints a live machine-memory snapshot and the reserve the launcher would use right now, which helps explain degraded current-state refreshes.

Reset `candidate.yaml` back to the saved stable best config:

```bash
uv run restore_best.py
```

Refresh a separate current-state recommendation around the saved best config:

```bash
uv run refresh_current_state.py
```

The launcher can opt into that file with `AUTORESEARCH_USE_CURRENT_STATE=1`, but it stays on the historical best by default.
When the current-state candidate includes tested reserve settings, the launcher now follows those too instead of recomputing a fresh reserve.
There is also a one-command wrapper at `../hypura-main/scripts/serve-gemma4-current-state.sh` that refreshes current-state and then launches with that preference enabled. That wrapper defaults to a faster single refresh pass unless you explicitly ask for a higher `--repeat`.
If you only want to inspect the launch config, `PRINT_CONFIG_ONLY=1` now reuses the most recent saved current-state record instead of re-running the refresh first. Set `REFRESH_CURRENT_STATE=1` if you want that dry-run to force a fresh refresh.
If the most recent current-state refresh already failed and the machine has not improved by at least a modest amount, `refresh_current_state.py` now skips the re-benchmark and records that it reused the failed result.

Probe the local Flash-MoE Gemma 4 branch against this machine and model before attempting a full sidecar extraction:

```bash
uv run flashmoe_probe.py
```

That probe reports how much of the model is routed expert data, what small slot-bank sizes would reserve, and whether there is enough free disk for a full sidecar extraction right now.
You can also ask it to do a one-layer extraction and verification smoke check on the real model without paying for the full sidecar:

```bash
uv run flashmoe_probe.py --smoke-layer 0
```

After the branch is built, you can replay the verified partial Flash-MoE runtime smoke test with:

```bash
./flashmoe_gemma4_smoke.sh
```

There is also a slot-bank smoke wrapper:

```bash
./flashmoe_gemma4_slot_smoke.sh
```

That script now does a coverage preflight first. If the sidecar is partial, it stops immediately and tells you streamed mode is not yet possible.
Both Flash-MoE smoke scripts now accept `N_GPU_LAYERS`, `BATCH`, and `UBATCH` as environment overrides, so you can quickly compare CPU-only and dense/shared GPU-offload paths.
That slot-bank smoke wrapper now defaults to the full routed sidecar, so it works out of the box on this workspace instead of immediately failing on the older one-layer smoke artifact.

If you want the best measured Flash-MoE alternate directly, use:

```bash
./flashmoe_gemma4_best.sh "Answer with one lowercase word only: what is the capital of France?"
```

That wrapper uses the saved stable Flash-MoE alternate in this workspace: slot-bank `16`, CPU dense/shared path, full routed sidecar, context `4096`, threads `10`, prompt threads `10`, batch `1`, and micro-batch `1`.

If you want the Flash-MoE alternate to print only the answer, use:

```bash
./flashmoe_gemma4_ask.sh "Answer with one lowercase word only: what is the capital of France?"
```

If you want to keep the Flash-MoE alternate resident and ask repeated prompts through a local server, use:

```bash
./flashmoe_gemma4_serve.sh
FLASHMOE_ASK_MODE=server ./flashmoe_gemma4_ask.sh "Answer with one lowercase word only: what is the capital of France?"
```

The default `flashmoe_gemma4_ask.sh` mode is now `auto`, so it will use that server when it is already running and fall back to the older one-shot CLI path when it is not.
That server launcher now prefers the saved resident-server best candidate if one exists, and only falls back to the older one-shot Flash-MoE export if it does not.

To record the current warm-server behavior into a machine-readable artifact for the comparison report:

```bash
uv run hypura_server_probe.py
uv run flashmoe_server_probe.py
```

To regenerate the plain side-by-side runtime summary:

```bash
uv run runtime_comparison.py
```

If you want one front door for both runtimes, use:

```bash
./gemma4_answer.sh --mode speed "your prompt here"
./gemma4_answer.sh --mode memory "your prompt here"
./gemma4_answer.sh --mode auto "your prompt here"
./gemma4_answer.sh --mode speed --replace "your prompt here"
./gemma4_answer.sh --mode auto --stream "your prompt here"
```

`speed` uses the tuned Hypura server path.
`memory` uses the Flash-MoE alternate and now prefers the resident Flash-MoE server when it is available.
`auto` uses the shared chooser: it prefers a live server, but when both runtimes are live it now looks at the current machine state and picks the one that still makes sense.
By default, `gemma4_answer.sh` now starts the needed server for you if it is not already running. Set `AUTO_START_SERVER=0` if you want the older no-launch behavior. In that mode, `auto` still uses the Flash-MoE one-shot path when that is the only runnable choice.
If you pass `--replace`, it stops the other live runtime first before bringing up the one you asked for.
If you pass `--stream`, the answer prints token by token while it is being generated. Interactive terminal use now streams by default; use `--no-stream` if you want the old buffered behavior.

If you want one quick regression check for the new streaming and cleanup behavior without loading the real model, run:

```bash
python3 streaming_regression_smoke.py
```

That smoke test stands up lightweight fake Hypura and Flash-MoE servers, checks that streaming output arrives in pieces before completion, checks that buffered chat mode stays buffered when streaming is off, and checks that chat cleanup stops the non-active runtime.

If you want one release-readiness preflight before publishing this workspace, run:

```bash
python3 release_readiness_check.py
```

That command runs the public entrypoint syntax checks, runs the streaming regression smoke, and confirms the top-level `show_best.py` summary still renders.
There is now also a GitHub Actions workflow at the project root that runs the same preflight on pushes and pull requests.
For the human side of release prep, there is also a checklist at `../RELEASE_CHECKLIST.md`.

To see what is live right now, or stop a live Gemma server cleanly:

```bash
./gemma4_server_status.sh
./gemma4_server_stop.sh
./gemma4_server_stop.sh --runtime hypura
./gemma4_server_stop.sh --runtime flashmoe
```

If you want a real interactive chat session instead of one question at a time:

```bash
python3 gemma4_chat.py --mode auto
python3 gemma4_chat.py --mode memory --replace
python3 gemma4_chat.py --mode auto --no-stream
python3 gemma4_chat.py --mode auto --session ideas
python3 gemma4_chat.py --list-sessions
python3 gemma4_chat.py --show-session ideas
python3 gemma4_chat.py --delete-session old-ideas
```

That chat client picks the right runtime, starts it if needed, keeps the conversation history for you, auto-saves the session after each turn, and leaves the server running afterward.
It now streams tokens by default while the model is answering. Use `--no-stream` at startup or `/stream off` inside the chat if you want buffered replies instead.
If you pass `--replace`, it stops the other live runtime first before starting the one you asked for.
If you reuse `--session ideas`, it resumes that conversation from disk even after you quit and restart the client.
`--show-session ideas` prints the saved transcript and metadata from the terminal without starting a server.
`--delete-session old-ideas` removes a saved session from disk without opening the chat.
If you open a brand-new named session and leave without actually chatting, it now cleans that empty session back up instead of leaving clutter behind.
Inside the chat, `/help` shows the built-in commands, `/status` shows the current chat runtime plus what `auto` would choose right now, `/switch speed|memory|auto` moves the live chat to a different runtime without leaving the session, `/switch ... --replace` does a true handoff by stopping the other runtime during the switch, `/cleanup` stops the non-active runtime while keeping the current chat where it is, and `/stream on|off` toggles token streaming for new replies. `/sessions` lists the saved sessions, `/saveas NAME` branches the current conversation into a new saved session name, `/rename NAME` renames the current saved session, and `/delete NAME` removes another saved session.

If you want a simple server start command that uses the same logic as the answer and chat tools, use:

```bash
./gemma4_server_start.sh --mode auto
./gemma4_server_start.sh --mode speed
./gemma4_server_start.sh --mode memory
./gemma4_server_start.sh --mode speed --replace
```

If you want the lower-level launcher that decides which server to start based on the machine's current memory state, use:

```bash
PRINT_DECISION_ONLY=1 ./serve_gemma4_auto.sh
./serve_gemma4_auto.sh
```

That wrapper chooses the fast Hypura server when there is enough room to keep it comfortable, and otherwise chooses the lighter Flash-MoE resident server.
If only one of those servers is already running, auto mode reuses it first instead of trying to start the other one.
If both are already running, auto mode now uses the current machine state to decide which one to keep using.
`gemma4_server_status.sh` now shows both runtimes, whether any saved auto-start state still matches a live server or has gone stale, and what `auto` would choose right now.
`gemma4_server_start.sh` starts the chosen server in the background, waits for it to come up, records its state, and then shows the live status.
If you pass `--replace`, it stops the other live runtime first so you do not leave both engines running at once.
`gemma4_server_stop.sh` now defaults to the recorded auto-started runtime when there is one, otherwise it stops the only live runtime if there is exactly one. If both runtimes are live, it asks you to choose with `--runtime hypura`, `--runtime flashmoe`, or `--runtime all`.

Run a small reproducible sweep without hand-editing temp files:

```bash
uv run sweep.py \
  --label threads-batch-probe \
  --grid backend_config.threads_batch=13,14,15 \
  --override measured_runs=1
```

For Hypura sweeps, `sweep.py` freezes the memory reserve once at the start unless you explicitly override those headroom settings yourself. That keeps the comparison from drifting as file cache and leftover memory state change between runs.

If single-pass runs look noisy, repeat each grid point and use the grouped summary:

```bash
uv run sweep.py \
  --label tb-repeat \
  --grid backend_config.threads_batch=13,14 \
  --override measured_runs=1 \
  --repeat 2
```

## Candidate shape

`candidate.yaml` controls one experiment at a time. The default file starts with a `llama.cpp` baseline.

Current search surfaces:

- backend choice: `llama_cpp` or `hypura`
- backend choice: `llama_cpp`, `hypura`, or `flashmoe`
- context size
- warmup and measured run counts
- runtime guardrails such as memory floor and timeout
- `llama.cpp` knobs:
  - threads
  - prompt threads
  - batch size
  - micro-batch size
  - flash attention mode
  - KV offload
  - KV cache types
  - MoE placement on CPU
  - mmap on/off
  - gpu layers

Hypura now exposes a small set of runtime knobs through the harness too:

- threads
- batch size
- prompt-side batch threads
- prompt-side micro-batch size
- memory reserve and headroom overrides

Flash-MoE candidates use a narrower config surface:

- `sidecar_dir`
- `moe_mode`
- `moe_slot_bank`
- threads
- prompt threads
- batch size
- micro-batch size
- gpu layers

Flash-MoE server candidates add:

- `parallel`
- `cache_prompt`

The sample starting point is `candidates/flashmoe-slot-bank-16.yaml`, which now matches the best Flash-MoE harness result verified locally so far: slot-bank `16` with the dense/shared path left on the CPU.

The search should still stay narrow. Treat these as the only first-class Hypura knobs unless a later experiment proves there is a strong reason to expose more.

For quick probes, prefer `uv run train.py --override ...` over making ad hoc temporary edits to `candidate.yaml`. This keeps the tracked candidate clean while still writing full run artifacts.

At the moment, the best verified strict setup in this repo is Hypura at `4096` context with `10` generation threads, `14` prompt-side batch threads, batch size `512`, and micro-batch size `256`.

## Output files

- `results.tsv`: one row per run
- `results/best.json`: current best valid result
- `results/best_candidate.yaml`: stable exported config for the current best valid result
- `results/runs/*.json`: full artifact for each run
- `progress.png`: simple chart of score and free memory
- `results/machine_profile.json`: the verified local setup
- `TURBOQUANT_GEMMA4_NOTES.md`: compression feasibility notes for Gemma 4
- `show_best.py`: quick summary of the winning run, winning config, and candidate drift
- `flashmoe_gemma4_best.sh`: one-command runner for the best measured Flash-MoE alternate
- `flashmoe_gemma4_ask.sh`: answer-only wrapper for the best measured Flash-MoE alternate
- `results/best_flashmoe.json`: saved record for the best measured Flash-MoE alternate
- `results/best_flashmoe_candidate.yaml`: stable exported config for the best measured Flash-MoE alternate
- `results/best_flashmoe_server.json`: saved record for the best measured resident-server Flash-MoE alternate
- `results/best_flashmoe_server_candidate.yaml`: stable exported config for the best measured resident-server Flash-MoE alternate
- `gemma4_answer.sh`: one prompt interface for the fast path, the lower-memory path, or auto fallback
- `serve_gemma4_auto.sh`: one launcher that picks the faster or lighter resident server based on current memory state
- `gemma4_server_start.sh`: friendly background server starter for `auto`, `speed`, or `memory`, with `--replace` to switch cleanly
- `gemma4_server_status.sh`: show both runtimes, the saved auto-start state, and the current `auto` recommendation
- `gemma4_server_stop.sh`: stop the recorded auto-started server, or a chosen live runtime, and clear saved state when appropriate
- `gemma4_chat.py`: interactive multi-turn chat client that auto-picks and auto-starts the right runtime
- `results/chat_sessions/*.json`: saved interactive chat sessions that can be resumed later
- `results/runtime_comparison.md`: side-by-side summary of the best overall path and the best Flash-MoE alternate
- `results/hypura_server_probe_latest.json`: fresh warm-request snapshot for the running Hypura server
- `results/flashmoe_server_probe_latest.json`: fresh warm-request snapshot for the running Flash-MoE server
- `restore_best.py`: reset the tracked candidate file back to the saved stable best
- `sweep.py`: run small override grids and write a sorted sweep summary under `results/sweeps/`
- `refresh_current_state.py`: run a small repeated sweep around the saved best and record a current-state winner separately

## Notes

- This fork is text-only for now.
- The search is tuned for this exact Mac and model file, not as a universal benchmark.
- `llama.cpp` is treated as a fixed baseline. The research loop may edit `candidate.yaml` freely, but it should not patch the local `llama.cpp` tree.
- On this specific 24 GB machine, the 15.6 GB GGUF leaves very little room once macOS and normal background apps are already using memory. If the first baseline runs all come back as `reject`, that is useful evidence: the current model artifact may simply be too large to satisfy a strict 4 GB free-memory floor without moving to a smaller quant or relaxing the floor.
