<!-- translation-of: docs/how-to/run_tests.md sha256:8f341e716c12 -->
**English** · [Русский](run_tests.md)

# How to run tests

> **Document type:** How-to (Diataxis) — specific commands for specific tasks.
> To understand **why** it works this way, see `docs/explanation/test_architecture.md`.
> To see **what exists**, see `tests/README.md`, `USE_CASES.md`.

---

## Locally on MacBook (pre-commit)

```bash
cd ~/.worktrees/health_scripts/<slug>  # thread tree; for a one-off edit — ~/health_scripts

# Local run on fixtures (does not replace the full run on Studio)
python3.11 -m pytest tests/

# Unit only
python3.11 -m pytest tests/unit/

# Consistency scenarios only
python3.11 -m pytest -m consistency

# A specific UC
python3.11 -m pytest tests/unit/test_uc_i_02_sec.py -v

# One test by name
python3.11 -m pytest tests/ -k "test_owner_filter_passes"

# With the real Anthropic API (paid)
python3.11 -m pytest -m "requires_anthropic_key" --override-ini="addopts="
```

## Full suite when closing a thread

Work spanning more than one commit belongs in a tree created with `scripts/thread_start.sh <slug>`. After committing in the thread’s tree, run this from the main MacBook copy:

```bash
cd ~/health_scripts
scripts/thread_finish.sh <slug>
```

The script rebases the branch onto the current `main`, checks the gates, and runs the full suite on Studio from the thread’s tree **before merging and deploying**. You do not need to run it separately before closing. For the procedure and troubleshooting, see [thread_worktree.md](thread_worktree.md).

## On Studio (after git-push deployment, for debugging)

Before trusting the result, check from MacBook: `git fetch studio && git log studio/main` must show the commit being checked (CLAUDE.md (private part) §12).

```bash
ssh <studio_ssh> "cd ~/health_scripts && /opt/homebrew/bin/python3.11 -m pytest tests/"
```

Or run the full pyramid:

```bash
ssh <studio_ssh> "cd ~/health_scripts && bash run_full_test_suite.sh"
```

This runs automatically at 00:00 every night through `com.larry.health.test-suite`.

## Manually trigger the nightly suite (to debug the handler)

```bash
ssh <studio_ssh> "launchctl kickstart -k gui/$(id -u)/com.larry.health.test-suite"
# Logs:
ssh <studio_ssh> "tail -f ~/health_test_suite.log"
```

## What to do with XFAIL

Tests marked `xfail` document **known bugs** in `partial` UCs.
They do not show up as red failures, but they are not green either. After you fix the bug, xfail
will show up as `XPASS` — a signal to remove the `@pytest.mark.xfail` decorator.

For the list of known XFAILs, see `USE_CASES.md` (the “Confirmed bug” sections in
UC-I-03, UC-A-03).

## What to do with SKIPPED

`skipped` means a test requires an unavailable resource:
- `requires_anthropic_key` — no API key.
- `requires_studio` — runs only on Studio (the primary machine — `infra_config.is_primary()`).
- A module has not been implemented yet.

This is not a failure. When the resource becomes available, remove the skip or rewrite it as
a full test.

## Install dependencies

```bash
# pytest and unit dependencies
/opt/homebrew/bin/python3.11 -m pip install pytest --break-system-packages
```

On Studio, do the same through ssh, using the same path.

## What to add to `~/.gitignore`

```
tests/.pytest_cache/
tests/reports/
tests/.diagnosis_cache/
**/__pycache__/
.pytest_cache/
```

## Related documents

- `tests/README.md` — directory structure, markers, how to add tests.
- `docs/how-to/add_new_uc.md` — the UC-J-02 process.
- `docs/how-to/handle_test_failure.md` — what to do after a nightly failure.
