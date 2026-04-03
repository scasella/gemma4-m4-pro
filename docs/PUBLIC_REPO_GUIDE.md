# Public Repo Guide

Use this when you want to prepare, inspect, or update this public repo.

The steps below assume you are already at the repo root.

## 1. Check the current publish state

Start with the plain status view:

```bash
./publish_status.sh
```

That tells you three important things in one place:

- what is still blocking a public push
- what release stage the repo is in
- the safest next command to run

If you want the same summary in machine-readable form:

```bash
./publish_status.sh --json
```

## 2. Run one cleanup and readiness pass

If you want one command that runs the public preflight, cleans local-only junk, and checks the lean repo shape:

```bash
./prepare_public_push.sh
```

If old failed rehearsal temp copies are still hanging around, clear those first with:

```bash
./prepare_public_push.sh --clean-failure-rehearsals
```

## 3. Preview or apply publish setup

When the repo still needs a license, Git setup, or a real GitHub remote, preview the setup first:

```bash
./make_publish_ready.sh --license mit --holder "Your Name" --remote https://github.com/you/repo.git --dry-run
```

Then run the real setup:

```bash
./make_publish_ready.sh --license mit --holder "Your Name" --remote https://github.com/you/repo.git
```

If you only want to install the license file:

```bash
./install_license.sh mit --holder "Your Name"
```

## 4. Rehearse the full flow safely

If you want to practice the public-release flow in a throwaway copy before touching the live repo:

```bash
./rehearse_publish_flow.sh
```

Keep the temp copy even on success if you want to inspect it afterward:

```bash
./rehearse_publish_flow.sh --keep-temp
```

If a rehearsal fails, it keeps its temp copy automatically so you can inspect what went wrong.

## 5. Inspect or clean saved rehearsal temp copies

Use this helper when you want to see saved rehearsal copies, active rehearsals, or clean them up:

```bash
./rehearsal_temp_status.sh
```

Remove only failure-kept copies:

```bash
./rehearsal_temp_status.sh --clean-failures
```

Remove all saved copies after inspection:

```bash
./rehearsal_temp_status.sh --clean
```

## 6. Finish the first public release

After the first real push, finish the README badge step with:

```bash
./finish_public_release.sh --dry-run
./finish_public_release.sh
```

If you want the lower-level badge helper directly:

```bash
./install_ci_badge.sh
```

## 7. Related docs

- release checklist: [`RELEASE_CHECKLIST.md`](./RELEASE_CHECKLIST.md)
- external runtime and model setup: [`SETUP_EXTERNALS.md`](./SETUP_EXTERNALS.md)
- license choices: [`LICENSE_OPTIONS.md`](./LICENSE_OPTIONS.md)
- public preflight: [`autoresearch/release_readiness_check.py`](../autoresearch/release_readiness_check.py)
- workflow file: [`.github/workflows/release-readiness.yml`](../.github/workflows/release-readiness.yml)
