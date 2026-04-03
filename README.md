# Gemma 4 on a 24 GB M4 Pro

[![Release Readiness](https://github.com/scasella/gemma4-m4-pro/actions/workflows/release-readiness.yml/badge.svg)](https://github.com/scasella/gemma4-m4-pro/actions/workflows/release-readiness.yml)


This repo is a practical local-research and runtime toolkit for running `gemma-4-26B-A4B-it` on a 24 GB M4 Pro MacBook Pro.

It includes:

- a tuned fast path built around Hypura
- a lower-memory alternate built around Flash-MoE
- a benchmark loop for comparing and improving both
- everyday commands for one-shot prompts, resident servers, and interactive chat
- release-readiness checks for the public-facing tools
- a lean public layout that leaves out local models, raw sidecars, and bulk run logs

## Current status

Two runtime styles are available:

- fastest overall: Hypura
- lower-memory alternate: Flash-MoE

The benchmark and status tooling for those live under [`autoresearch/`](./autoresearch).

## Lean public repo note

This GitHub release repo is intentionally lean.

It includes:

- the tuned Hypura source tree used by the fast path
- the benchmark and control scripts
- curated benchmark summaries and the small set of run artifacts needed to support them

It does **not** include:

- local model files
- the huge Flash-MoE sidecar data
- the full raw run-log archive
- the optional Flash-MoE source checkout

If you want the lower-memory Flash-MoE path in this lean repo, follow [`SETUP_EXTERNALS.md`](./SETUP_EXTERNALS.md) and clone the optional runtime into the expected local path.

## Performance at a glance

All numbers below were measured during the original 24 GB M4 Pro research runs with the local `gemma-4-26B-A4B-it-Q4_K_M.gguf` file.

| Runtime | How it is used | Generation speed | Prompt speed | First answer | Load time | Lowest free memory | Swap growth | Warm resident memory |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Hypura | tuned resident server | 57.01 tok/s | 67.44 tok/s | 1025.7 ms | 1.97 s | 9.33 GB | 0.00 GB | about 12.51 GB |
| Flash-MoE | one-shot CLI fallback | 13.90 tok/s | 10.90 tok/s | 71.9 ms | 18.69 s | 8.81 GB | 0.00 GB | n/a |
| Flash-MoE | resident server | 19.11 tok/s | 22.36 tok/s | 3048.2 ms | 9.19 s | 10.70 GB | 0.00 GB | about 4.32 GB |

## What improved during the research

- The fast Hypura path moved from an early strict safe run of `54.53` tok/s to a best validated run of `57.01` tok/s while still keeping about `9.33 GB` free and adding no swap. That is about a `4.6%` speed gain without giving up the comfort margin.
- The biggest Hypura gains came from making the runtime respect a real memory reserve, settling on `4096` context, and tuning the prompt-side worker settings and batch sizes instead of only chasing the main thread count.
- The lower-memory Flash-MoE path became much more practical once it was treated as a resident server instead of only a one-shot command. In fair benchmark mode it improved from `13.90` tok/s to `19.11` tok/s, about a `37%` gain.
- The always-on memory footprint difference is large and worth calling out directly: the fresh warm probe showed Hypura holding about `12.51 GB` resident, while the Flash-MoE resident server held about `4.32 GB`. That is the main reason both paths are still useful.
- Across the best saved runs for both paths, swap growth stayed at `0.00 GB`. The work did not just make the model faster; it also made the runtime behavior much safer and more predictable on this laptop.

If you want the deeper benchmark record behind those numbers, start with [`autoresearch/results/runtime_comparison.md`](./autoresearch/results/runtime_comparison.md).

## Where to start

If you want to use the model:

- everyday prompt and chat commands: [`autoresearch/README.md`](./autoresearch/README.md)
- tuned Hypura server path: [`hypura-main/GEMMA4_M4_PRO.md`](./hypura-main/GEMMA4_M4_PRO.md)

If you want to understand the research state:

- main benchmark and workflow docs: [`autoresearch/README.md`](./autoresearch/README.md)
- agent workflow notes: [`autoresearch/program.md`](./autoresearch/program.md)
- curated runtime comparison: [`autoresearch/results/runtime_comparison.md`](./autoresearch/results/runtime_comparison.md)
- curated results manifest: [`autoresearch/results/curated_results_manifest.json`](./autoresearch/results/curated_results_manifest.json)
- lean layout manifest: [`lean_repo_layout_manifest.json`](./lean_repo_layout_manifest.json)

If you want to prepare a release or update this public repo:

- release checklist: [`RELEASE_CHECKLIST.md`](./RELEASE_CHECKLIST.md)
- external runtime and model setup: [`SETUP_EXTERNALS.md`](./SETUP_EXTERNALS.md)
- automated preflight command: `autoresearch/release_readiness_check.py`
- lean repo audit: `python3 lean_repo_audit.py`
- lean layout manifest: [`lean_repo_layout_manifest.json`](./lean_repo_layout_manifest.json)
- one-command publish status: `./publish_status.sh` (follow the printed Current blockers, Release stage, and Suggested next action; once the repo is publish-ready, it now steps through first push, post-push polish, and finally no further publish-tooling steps)
- machine-readable publish status: `./publish_status.sh --json` (includes `release_stage`, `release_stage_reason`, `blocking_items`, `suggested_next_action_command`, `suggested_next_action_reason`, `suggested_push_command`, and `suggested_post_publish_action_command`)
- one-command public push prep: `./prepare_public_push.sh`
- one-command public push prep plus failure-temp cleanup: `./prepare_public_push.sh --clean-failure-rehearsals`
- one-command publish setup: `./make_publish_ready.sh --license mit --holder "Your Name" --remote https://github.com/you/repo.git`
- preview the publish setup without changing files: `./make_publish_ready.sh --license mit --holder "Your Name" --remote https://github.com/you/repo.git --dry-run`
- one-command full publish rehearsal in a temp copy: `./rehearse_publish_flow.sh`
- keep the rehearsal temp copy even on success: `./rehearse_publish_flow.sh --keep-temp`
- failed rehearsals keep their temp copy automatically for debugging
- inspect saved rehearsal temp copies and active rehearsals: `./rehearsal_temp_status.sh`
- remove only failed saved rehearsal temp copies: `./rehearsal_temp_status.sh --clean-failures`
- remove all saved rehearsal temp copies after inspection: `./rehearsal_temp_status.sh --clean`
- license install helper: `./install_license.sh mit --holder "Your Name"`
- one-command post-push polish: `./finish_public_release.sh`
- preview the post-push polish without changing files: `./finish_public_release.sh --dry-run`
- direct CI badge helper: `./install_ci_badge.sh`
- CI workflow: [`.github/workflows/release-readiness.yml`](./.github/workflows/release-readiness.yml) runs the user-facing preflight, the lean repo audit, and the full publish rehearsal

## How This Repo Is Organized

- [`autoresearch/`](./autoresearch): the main benchmark loop, user-facing prompt/chat commands, status tools, regression smoke tests, and release preflight
- [`hypura-main/`](./hypura-main): the tuned fast runtime path and its launcher/docs
- `anemll-flash-llama.cpp-gemma4/`: optional external checkout for the lower-memory Flash-MoE path
- [`models/`](./models): local model files used by the runtime and benchmark tools
- [`autoresearch/results/runtime_comparison.md`](./autoresearch/results/runtime_comparison.md): curated benchmark summary
- [`autoresearch/results/curated_results_manifest.json`](./autoresearch/results/curated_results_manifest.json): exact saved result files intentionally kept in the lean public repo
- [`lean_repo_layout_manifest.json`](./lean_repo_layout_manifest.json): expected high-level public file layout for this lean release repo
- [`RELEASE_CHECKLIST.md`](./RELEASE_CHECKLIST.md): human release checklist

If you are just trying to use the model, start in [`autoresearch/`](./autoresearch).
If you are trying to understand how the fast runtime was tuned, also read [`hypura-main/GEMMA4_M4_PRO.md`](./hypura-main/GEMMA4_M4_PRO.md).

## Quick commands

One prompt from the project root:

```bash
./try_gemma4.sh "Tell me a short sentence about Paris."
```

Interactive chat from the project root:

```bash
./chat_gemma4.sh
```

Those wrappers default to the same automatic runtime choice as the lower-level `autoresearch` commands, so they pick the fast path when the machine has room and the lighter path when memory is tighter.

One prompt, automatic runtime choice:

```bash
cd autoresearch
./gemma4_answer.sh --mode auto "Tell me a short sentence about Paris."
```

Interactive chat:

```bash
cd autoresearch
python3 gemma4_chat.py --mode auto
```

Release preflight:

```bash
cd autoresearch
python3 release_readiness_check.py
```
