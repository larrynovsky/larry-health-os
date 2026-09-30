<!-- translation-of: docs/how-to/git_workflow.md sha256:853913e0c0e8 -->
**English** · [Русский](git_workflow.md)

# Git workflow

> **Document type:** How-to (Diátaxis): specific commands for everyday tasks.
> Architectural rationale: [docs/explanation/git_architecture.md](../explanation/git_architecture.md).
>
> **Model history:**
> - 2026-05-09 — Studio canonical, MacBook without `.git/`.
> - 2026-05-23 — **Sprint 7-GIT**: MacBook has `.git/` as a clone of Studio; push through `git push studio main`. For the current model, see below.

---

## Where git lives (effective since 2026-05-23)

- **Studio canonical**: `~/health_scripts/.git/` (production reference).
- **MacBook clone**: `~/health_scripts/.git/` (working copy with full history).
- **Remote `studio` on MacBook** = `<studio_ssh>:~/health_scripts`.
- **Studio config**: `receive.denyCurrentBranch=updateInstead` — pushing to the checked-out branch updates the working tree.

`STUDIO="<studio_ssh>"` (from `~/.infrastructure.md`).

---

## View git status

```bash
ssh <studio_ssh> "cd ~/health_scripts && git status"
ssh <studio_ssh> "cd ~/health_scripts && git status -s"   # short form
```

## View git log

```bash
ssh <studio_ssh> "cd ~/health_scripts && git log --oneline -10"
ssh <studio_ssh> "cd ~/health_scripts && git log --since='1 week ago' --oneline"
ssh <studio_ssh> "cd ~/health_scripts && git log --all --oneline --graph -20"
```

## Make a commit (from MacBook: the main flow)

Work spanning more than one commit belongs in its own tree:

```bash
cd ~/health_scripts
scripts/thread_start.sh <slug>
cd ~/.worktrees/health_scripts/<slug>
```

Commit in the MacBook working copy:

```bash
git add file1.py file2.md
git commit -m 'feat: ...' -- file1.py file2.md
# On main, post-commit does git push studio main + service restart.
# On thread/<slug>, deploy is skipped by design.
```

After committing, close the thread **from the main copy**:

```bash
cd ~/health_scripts
scripts/thread_finish.sh <slug>
```

Closing the thread includes rebasing onto `main`, a full suite run on Studio, a `--no-ff` merge, and an explicit deployment call. For troubleshooting, see [thread_worktree.md](thread_worktree.md). For a one-off edit in the main copy, use a commit with explicit paths as above: it will not include the rest of the index.

## Check syntax before committing

Cheaper than diagnosing a failed deployment (SSH PATH has no Homebrew, so use the full path):

```bash
/opt/homebrew/bin/python3.11 -m py_compile <file>.py
```

## Studio: reading and execution only

**Pre-commit rejects** a commit on Studio: single-writer = MacBook, Studio is deploy-only (`e017929`). Editing a file over ssh without committing leaves Studio dirty and breaks the next push: `receive.denyCurrentBranch=updateInstead` requires a clean tree. All edits and commits belong on MacBook (CLAUDE.md (private part) § C “Environment”).

The emergency bypass remains in pre-commit (`scripts/git-hooks/pre-commit`), but opens only at the owner's explicit request in the current session:

```bash
# On Studio, emergency ONLY:
ssh <studio_ssh> "cd ~/health_scripts && HEALTH_ALLOW_STUDIO_COMMIT=1 git commit -am '...'"
# On the MacBook right after, otherwise the next push fails on fast-forward:
cd ~/health_scripts && git fetch studio && git reset --hard studio/main
```

After ssh EXECUTION of code on Studio (tests, generation, migrations: allowed and not a commit), start the next session in the main MacBook copy with `git fetch studio && git reset --hard studio/main` (CLAUDE.md (private part) § C “Environment”).

⚠️ The scripts `scripts/sync_from_studio.sh` / `sync_to_studio.sh` **were deleted on 2026-06-28** (CLAUDE.md (private part) §6 RETIRED). If you encounter them in text, the text is outdated; there is one command: `git fetch studio && git reset --hard studio/main`.

## Nightly code snapshot (daily at 03:00): NOT a commit to main

`~/Library/LaunchAgents/com.larry.health.backup.plist` on MacBook calls `~/health_scripts/backup.sh`. **Since 2026-07-24 (owner's decision), it does not commit to `main` or push `main`.** It assembles the tree in a temporary index → `commit-tree` → `refs/backups/daily-<дата>` → `push -f` of that ref to Studio. `main`, the index, and the working tree are untouched; no deployment, no gate bypasses. The last 14 snapshots are kept locally.

Before 2026-07-24, the script ran `git commit --no-verify` + `git push studio main`: unchecked WIP was deployed at night, bypassing every gate. If anything still says “nightly `git add -A` will go to canonical,” that is an old version.

Retrieve a file from a nightly snapshot:

```bash
git checkout refs/backups/daily-2026-08-02 -- path/to/file.py
```

If the nightly snapshot failed, check the log:

```bash
tail -20 ~/health_scripts/logs/backup.log
# Look for "git snapshot: FAILED" or "git push snapshot: FAILED"
```

## What to do when the post-commit hook reports `push FAILED`

Message in `logs/deploy.log`: `Studio впереди MacBook или dirty working tree`. This means either:
- Studio’s history is ahead of MacBook. Recovery: `git fetch studio && git rebase studio/main`, then run `git push studio main` manually.
- The working tree on Studio is dirty (someone is editing over ssh without committing). Do not commit on Studio or discard someone else’s work; coordinate preserving the edits on MacBook through `docs/handoff/<нить>/inbox/<твой-слаг>.md` (CLAUDE.md (private part) §22).

```bash
# Check the gap
git fetch studio && git log --oneline HEAD..studio/main
# Pull in Studio
git rebase studio/main   # or git reset --hard studio/main if local commits exist only in push
# Retry
git push studio main
```

## Roll back a file to a previous version

Roll back on MacBook: it is the only writer; the change reaches Studio through the usual post-commit push. Rolling back THROUGH Studio (`ssh … git checkout` + `rsync` back) is the old rsync model, retired on 2026-06-28, and leaves Studio dirty.

```bash
cd ~/health_scripts
git checkout HEAD~1 -- path/to/file
git commit -m 'revert: <file> to HEAD~1, reason' -- path/to/file
# post-commit will push to Studio and restart the bot by itself
tail -3 logs/deploy.log
```

## View git blame

```bash
ssh <studio_ssh> "cd ~/health_scripts && git blame file.py | head -30"
```

## Recover a commit from reflog

reflog is kept for ~30 days. Recover a “forgotten” commit:

```bash
cd ~/health_scripts
git reflog | head -20
# Find the SHA of the commit you need
git checkout <SHA> -- file
git commit -m 'restore: file from <SHA>' -- file
```

## Recover a completely lost repo (disaster)

If Studio is lost along with git:

1. **From Time Machine**: restore all of `~/health_scripts/.git/`.
2. **From a bundle**: MacBook has `/tmp/macbook_health_v2.bundle` from 2026-05-09 (the migration). This is a recovery starting point; everything after it must be committed manually or reconstructed from Time Machine.

## Create a new bundle for archival

At significant milestones:

```bash
ssh <studio_ssh> "cd ~/health_scripts && git bundle create /tmp/health_$(date +%Y-%m-%d).bundle --all"
scp <studio_ssh>:/tmp/health_*.bundle ~/Documents/git_bundles/
```

## What to do after editing a .py or .md file (since 2026-05-23)

For a one-off edit in the main MacBook copy (work spanning more than one commit goes through `thread_start`/`thread_finish`, see above):

```bash
# 1. Edit locally via Edit/Write
# 2. Commit on the MacBook
cd ~/health_scripts && git add file && git commit -m '...' -- file
# the post-commit hook does by itself:
#   - git push studio main   (atomic, fast-forward only)
#   - ssh studio "launchctl restart bot"
# 3. Check
tail -3 ~/health_scripts/logs/deploy.log
# Expect: "push + bot restart OK"
```

Editing directly on Studio over ssh-edit is prohibited, including for a single line: Studio is deploy-only (CLAUDE.md (private part) § C “Environment”).

## What to do when a hook blocks a commit

The pre-commit hook on MacBook checks doc-invariants. If it blocks:

1. Read the hook's message: it identifies the cause.
2. Fix the **cause**; do not bypass it with `--no-verify`.
3. If the hook itself is broken (yaml deadlock), the escape hatch is `git commit --no-verify` with an explicit comment explaining why.

**Prohibition**: an AI assistant does not use `--no-verify` without an explicit user request in the current session.

## Related documents

- [docs/explanation/git_architecture.md](../explanation/git_architecture.md) — why this workflow.
- CLAUDE.md § C “Environment” (private part) — the rule: who writes, the deployment path, tombstones of earlier models.
- [docs/BACKUP_POLICY.md §R0](../BACKUP_POLICY.md) — the split between MacBook (code snapshot) / Studio (sqlite).
- [docs/how-to/update_docs.md](update_docs.md) — workflow for documentation changes.
