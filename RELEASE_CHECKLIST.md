# Release Checklist

Use this before publishing this repo or opening a public pull request.

Quick path:

```bash
./publish_status.sh
# then follow the printed Release stage and Suggested next action
# once the repo is publish-ready, that flow now steps through first push, post-push polish, and finally no further publish-tooling steps
```

Optional quick path that also clears failure-kept saved rehearsal temp copies first:

```bash
./prepare_public_push.sh --clean-failure-rehearsals
```

Machine-readable status (`release_stage`, `release_stage_reason`, `blocking_items`, `suggested_next_action_command`, `suggested_next_action_reason`, `suggested_push_command`, and `suggested_post_publish_action_command` are the fastest summary fields):

```bash
./publish_status.sh --json
```

One-command setup path:

```bash
./make_publish_ready.sh --license mit --holder "Your Name" --remote https://github.com/you/repo.git
./make_publish_ready.sh --license mit --holder "Your Name" --remote https://github.com/you/repo.git --dry-run
```

Optional full publish rehearsal in a throwaway copy:

```bash
./rehearse_publish_flow.sh
./rehearse_publish_flow.sh --keep-temp
```

If a rehearsal fails, it now keeps the temp copy automatically so you can inspect what went wrong.
Use `./rehearsal_temp_status.sh` to inspect any saved copies, see active rehearsals that are still running, clean only failure-kept ones, or clean them all afterward.

For the broader release and update flow, start with [`PUBLIC_REPO_GUIDE.md`](./PUBLIC_REPO_GUIDE.md).
This lean public repo intentionally leaves out local model files, raw sidecars, and the optional Flash-MoE source checkout.

After the first public push, add the workflow badge to `README.md` with:

```bash
./finish_public_release.sh --dry-run
./finish_public_release.sh
```

Read [`SETUP_EXTERNALS.md`](./SETUP_EXTERNALS.md) if you want to run those optional paths locally.

## 1. Run the preflight

```bash
cd autoresearch
python3 release_readiness_check.py
```

Expected result:

- `python syntax` passes
- `shell syntax` passes
- `streaming regression smoke` passes
- `show_best summary` passes

## 2. Confirm the main status view still looks right

```bash
cd autoresearch
uv run show_best.py
```

Check that it still shows:

- the best validated setup
- the main user-facing commands
- the streaming smoke command
- the release preflight command

## 3. Skim the public-facing docs

Review these files for stale wording:

- `autoresearch/README.md`
- `autoresearch/program.md`
- `hypura-main/GEMMA4_M4_PRO.md`

Focus on:

- streaming behavior
- chat commands
- cleanup behavior
- the release preflight command
- the CI workflow

## 4. Check that no test helpers are still running

```bash
lsof -nP -iTCP -sTCP:LISTEN | rg 'python|813|815' || true
```

Expected result:

- no temporary fake-server listeners from the streaming smoke tests

## 5. Optional manual sanity check

If you want one quick real-user check on this machine:

```bash
cd autoresearch
./gemma4_answer.sh --mode auto --stream "Tell me a short sentence about Paris."
```

Or:

```bash
cd autoresearch
python3 gemma4_chat.py --mode auto
```

Inside chat:

- `/status`
- `/stream off`
- `/stream on`
- `/cleanup`

## 6. CI expectation

The root workflow at `.github/workflows/release-readiness.yml` now runs the same user-facing preflight, the lean repo audit, and the full publish rehearsal on pushes and pull requests.

## 7. Choose a license before publishing

This repo does not yet include a root `LICENSE` file.

Before making the repository public, decide what license you want and add it at the project root. A short comparison is in [`LICENSE_OPTIONS.md`](./LICENSE_OPTIONS.md).

Quick helper:

```bash
./install_license.sh mit --holder "Your Name"
```


Local verification can create `autoresearch/.venv/` and `autoresearch/__pycache__/`.
They are local-only helper artifacts, already ignored by `.gitignore`, and should not be part of a public push.

Until that file exists:

- outside users do not have clear reuse rights
- the public repo will look unfinished
