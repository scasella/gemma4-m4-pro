# autoresearch

This repo is an agent-driven search loop for finding the fastest safe local runtime setup for Gemma 4 on this Mac.

## Setup

Before running experiments:

1. Read the in-scope files:
   - `README.md`
   - `TURBOQUANT_GEMMA4_NOTES.md`
   - `prepare.py`
   - `train.py`
   - `candidate.yaml`
   - `program.md`
2. Run `uv run prepare.py` if `results/machine_profile.json` is missing.
3. Confirm that:
    - the model exists at `../models/gemma-4-26B-A4B-it-Q4_K_M.gguf`
    - `results.tsv` exists with the correct header
    - both backends are available
   - `uv run show_best.py` reports the current stable best, the recent rerun spread for the same core setup, the current machine-memory snapshot, and whether `candidate.yaml` already matches it
4. Start experimenting. Do not ask for confirmation once the loop begins.

If `candidate.yaml` has drifted and you want to restart from the validated winner, run `uv run restore_best.py` before editing a new candidate.

If you want to refresh the best recommendation for the machine as it is right now, without changing the historical all-time winner, run `uv run refresh_current_state.py`.
If the last current-state refresh already failed and the machine has not improved materially, that command may now skip the rerun and keep the failed result instead of spending time proving the same point again.
If you want to sanity-check the separate Flash-MoE Gemma 4 branch before committing to a sidecar extraction, run `uv run flashmoe_probe.py`.
If you want to rebuild the saved Flash-MoE alternate export, run `uv run sync_flashmoe_best.py`.
If you want to rebuild the saved Flash-MoE resident-server export, run `uv run sync_flashmoe_server_best.py`.
If you want to use the best measured Flash-MoE alternate directly, run `./flashmoe_gemma4_best.sh "your prompt here"`.
If you want that same alternate to print only the answer, run `./flashmoe_gemma4_ask.sh "your prompt here"`.
If you want to keep that alternate open as a resident server for repeated prompts, run `./flashmoe_gemma4_serve.sh`.
If that server is already running, `./flashmoe_gemma4_ask.sh` now uses it automatically unless you force `FLASHMOE_ASK_MODE=cli`.
If you want to save fresh warm-server measurements for the comparison report, run `uv run hypura_server_probe.py` and `uv run flashmoe_server_probe.py`.
If you want to benchmark the resident-server path inside the harness itself, run `uv run train.py --candidate candidates/flashmoe-server-slot-bank-16.yaml`.
If you want one prompt entrypoint for both runtimes, run `./gemma4_answer.sh --mode speed|memory|auto "your prompt here"`.
That answer wrapper now starts the needed server for you by default if the chosen runtime is not already up. Set `AUTO_START_SERVER=0` if you need the older no-launch behavior. In that mode, `auto` still falls back to the Flash-MoE one-shot path when that is the only runnable option.
Add `--replace` if you want it to stop the other live runtime first before switching.
Add `--stream` if you want token streaming explicitly. Interactive terminal use now streams by default; use `--no-stream` to force buffered output.
If you want a quick regression check for the new streaming and cleanup behavior without touching the real model, run `python3 streaming_regression_smoke.py`.
If you want one release-readiness preflight before publishing this workspace, run `python3 release_readiness_check.py`.
The same preflight now runs in the root GitHub Actions workflow on pushes and pull requests.
For the human release pass around that automated preflight, use `../RELEASE_CHECKLIST.md`.
If you need to inspect what is live right now or stop a live server cleanly, use `./gemma4_server_status.sh` and `./gemma4_server_stop.sh`.
If you want a real interactive chat session with history preserved across turns, run `python3 gemma4_chat.py --mode auto`.
Add `--replace` there too if you want it to switch runtimes cleanly instead of leaving the old one up.
Add `--no-stream` if you want buffered replies instead of token streaming.
If you want that chat session to resume later, give it a name with `--session NAME`. Use `python3 gemma4_chat.py --list-sessions` to see the saved sessions on disk.
If you want to inspect or delete a saved chat from the shell, use `python3 gemma4_chat.py --show-session NAME` and `python3 gemma4_chat.py --delete-session NAME`.
If you open a brand-new named chat and exit without actually talking to it, the empty saved session is now cleaned up automatically.
Inside the chat itself, `/help` shows the built-in commands, `/status` shows the current chat runtime plus what `auto` would choose right now, `/switch speed|memory|auto` moves the live chat to a different runtime without leaving the session, `/switch ... --replace` does a true handoff by stopping the other runtime during the switch, `/cleanup` stops the non-active runtime while keeping the current chat where it is, and `/stream on|off` toggles token streaming for new replies. `/sessions` lists the saved sessions, `/saveas NAME` saves the current conversation under a new session name and continues there, `/rename NAME` renames the current saved session, and `/delete NAME` removes another saved session.
If you want one launcher that decides which resident server to start from the machine's current memory state, run `PRINT_DECISION_ONLY=1 ./serve_gemma4_auto.sh` to inspect the choice or `./serve_gemma4_auto.sh` to launch it.
In auto mode, that chooser reuses a single already-running runtime before it considers starting a different one. If both runtimes are already live, it now uses current machine state to decide which one to keep using.
If you want a friendlier everyday starter, run `./gemma4_server_start.sh --mode auto|speed|memory`. It starts the chosen server in the background, waits for readiness, records its state, and then shows the resulting live status. Add `--replace` if you want it to stop the other live runtime first.
`./gemma4_server_status.sh` now shows both runtimes, whether the saved auto-start state still matches a live server, and what `auto` would choose right now.
`./gemma4_server_stop.sh` now defaults to the saved auto-started runtime when there is one, otherwise it stops the only live runtime if there is exactly one. If both runtimes are live, pass `--runtime hypura`, `--runtime flashmoe`, or `--runtime all`.

## Objective

Maximize **generation tokens per second** while:

- keeping at least **4 GB free**
- avoiding meaningful swap growth
- preserving a sane text answer on the correctness prompts

The benchmark harness already checks the guardrails. Your job is to choose better candidates and keep the search moving.

For Hypura, use the local `serve` + `/api/chat` path for the full run. The harness now keeps one Hypura server alive across correctness, warmup, and measured requests, so the comparison stays focused on the runtime instead of on a weaker CLI wrapper.

The harness also passes Hypura a dynamic memory reserve derived from the machine's current memory use plus the configured free-memory floor. If you need to pin that manually for an experiment, use `backend_config.memory_reserve_gb` and the related headroom overrides in `candidate.yaml`.

Current best-known strict starting point: Hypura, `4096` context, `10` threads, `14` prompt-side batch threads, batch size `512`, and micro-batch size `256`.
There is now also a separate Flash-MoE comparison candidate at `candidates/flashmoe-slot-bank-16.yaml`. Treat that as an alternate runtime experiment, not as the default tracked candidate. The saved stable Flash-MoE export currently points at slot-bank `16`, CPU dense/shared path, and `10/10` threads.
There is also a resident-server Flash-MoE benchmark candidate at `candidates/flashmoe-server-slot-bank-16.yaml`. It uses the same sidecar and core runtime shape, but disables prompt caching inside the benchmark so the result reflects a fair warm-server path instead of exact-prompt reuse.

## What you CAN change

Knob-first phase:

- edit `candidate.yaml`

Later code-change phase, only if both conditions are true:

- Hypura is within 15% of the best valid `llama.cpp` result
- three full knob-family sweeps fail to improve the best score by at least 5%

Allowed Hypura code changes in that later phase:

- expose thread count
- expose batch size
- expose cleaner machine-readable benchmark output

These prompt/runtime controls are now available in `candidate.yaml` as `backend_config.threads`, `backend_config.threads_batch`, `backend_config.batch_size`, and `backend_config.ubatch_size`.
For Flash-MoE candidates, the relevant extra fields are `backend_config.sidecar_dir`, `backend_config.moe_mode`, `backend_config.moe_slot_bank`, and `backend_config.gpu_layers`.

## What you CANNOT change

- do not lower the memory floor below 4 GB unless the human asks
- do not change the model file
- do not patch `../llama-baseline-build`
- do not weaken the correctness checks

## Search order

Use this exact search order.

### Stage 1: baseline matrix

Run these baselines first:

1. `llama_cpp` at context `2048`
2. `llama_cpp` at context `4096`
3. `hypura` at context `2048`
4. `hypura` at context `3072`
5. `hypura` at context `4096`

### Stage 2: llama.cpp knob families

Start from the best valid `llama.cpp` result and sweep one family at a time. Keep only the single best valid candidate from each family before moving on.

Thread family:

- `(10, 10)`
- `(14, 14)`
- `(10, 14)`

Batch family:

- `(256, 128)`
- `(512, 256)`
- `(1024, 512)`

Attention and KV family:

- default
- flash attention forced on
- quantized KV cache
- KV offload off

MoE placement family:

- `off`
- `first_8`
- `first_16`
- `all`

File-loading family:

- `mmap: true`
- `mmap: false`

### Stage 3: decide whether Hypura enters code phase

Only after the knob families stall:

- compare the current best Hypura result with the best valid `llama.cpp` result
- if Hypura is still farther than 15%, stay in knob-first work
- if Hypura is within 15%, you may enter the narrow Hypura code-change phase

## How to run one experiment

1. Edit `candidate.yaml`
2. Keep `description` short and specific
3. Run `uv run train.py`
4. Read:
   - the terminal summary
   - the newest row in `results.tsv`
   - the newest artifact in `results/runs/`
5. If the run is `keep`, advance from that candidate
6. If the run is `discard`, `reject`, or `crash`, change direction

One-pass exploratory sweeps are allowed, but do not treat them as the official winner. Only a candidate with at least three measured runs can replace `results/best.json`.

For one-off probes, you do not need to edit the file at all. Prefer:

```bash
uv run train.py \
  --override description='"your probe label"' \
  --override measured_runs=1 \
  --override backend_config.threads_batch=13 \
  --skip-plot
```

This keeps `candidate.yaml` pointed at the current baseline while still recording a full artifact.

For the Flash-MoE comparison path, start from:

```bash
uv run train.py --candidate candidates/flashmoe-slot-bank-16.yaml
```

For the resident-server Flash-MoE comparison path, start from:

```bash
uv run train.py --candidate candidates/flashmoe-server-slot-bank-16.yaml
```

That candidate expects the full routed sidecar under `results/flashmoe_full_sidecar`. If the sidecar is partial, the new backend preflight will stop immediately and record that as a crash instead of wasting time on a doomed run.
Once the resident-server path has a validated winner, `uv run sync_flashmoe_server_best.py` exports it to `results/best_flashmoe_server_candidate.yaml`, and `./flashmoe_gemma4_serve.sh` will follow that saved file by default.
The day-to-day Flash-MoE wrapper now follows that same full-sidecar assumption.
If you need a quick plain-English comparison of the two best runtime paths, regenerate `results/runtime_comparison.md` with `uv run runtime_comparison.py`.
If the servers are running, regenerate `results/hypura_server_probe_latest.json` and `results/flashmoe_server_probe_latest.json` first with `uv run hypura_server_probe.py` and `uv run flashmoe_server_probe.py` so the report includes the latest warm resident-server behavior for both paths.

For a small systematic comparison, prefer `sweep.py` over ad hoc shell loops. Example:

```bash
uv run sweep.py \
  --label threads-batch-probe \
  --grid backend_config.threads_batch=13,14,15 \
  --override measured_runs=1
```

That will run each candidate through the normal harness, keep `candidate.yaml` untouched, and write a sorted summary under `results/sweeps/`.

For Hypura, the sweep tool freezes the memory reserve once at the start unless you are explicitly sweeping those headroom fields. Use that default. It makes neighboring runs more comparable by preventing later runs from inheriting a tighter reserve just because earlier runs warmed the file cache.

If raw one-pass sweep results bounce around, increase `--repeat` and read the grouped summary first. The grouped view ranks each combo by its median result across repeats instead of by a single run.

## Interpreting results

`results.tsv` columns:

- `run_id`
- `time`
- `backend`
- `status`
- `score`
- `gen_tok_s`
- `prompt_tok_s`
- `ttft_ms`
- `load_s`
- `min_free_gb`
- `swap_delta_gb`
- `description`

Status meanings:

- `keep`: best valid result so far
- `discard`: valid, but not better than the current best
- `reject`: broke a guardrail or failed correctness
- `crash`: command failed or timed out

If every baseline candidate is `reject`, do not assume the harness is wrong. Record that the current GGUF may be incompatible with the 4 GB headroom target on this machine as currently loaded, then continue with lower-memory variants only if the human explicitly allows a smaller floor or a smaller model artifact.

## Persistence rule

Do not stop on your own. Once the loop starts, continue proposing and running candidates until you are interrupted.
