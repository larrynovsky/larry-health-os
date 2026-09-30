<!-- translation-of: docs/how-to/thread_worktree.md sha256:ee8b6cf56245 -->
**English** · [Русский](thread_worktree.md)

# How to work on a thread in its own worktree

A procedure for an agent starting and closing a task. Two commands.

## Why — three observed failures, not housekeeping

When two sessions work in the same directory, they **share one git index**.
Over August 30–31, 2026, this caused three different failures within 24 hours:

1. **Someone else's commit takes your files.** `1ed18eb` took two files prepared
   by another session: they were simply sitting in the shared index.
2. **Your commit takes someone else's files.** `45f0ebe` — the same thing in reverse.
   `git add <свой файл>` does not remove someone else's files from the index.
3. **A false green.** A test run in the working copy sees someone else's uncommitted
   changes. On August 29, the gate wiring was green locally and red on Studio for exactly
   this reason.

The third is more dangerous than the first two: it undermines trust in all checks at once.
On September 12, this failure class recurred four more times in one day, across two projects.

The framing (from the `distributed-systems-consistency` lens): the index is a data object with
multiple writers and no critical section. **Write conflicts do not happen
where the data has a single owner.** Your own worktree = your own index = one
owner. Everything else is detection instead of prevention, and depends on memory.

## Start a thread

```bash
scripts/thread_start.sh <short-name>        # for example: merge-gates
```

This creates a `thread/<имя>` branch and a worktree in
`~/.worktrees/<имя каталога репозитория>/<имя нити>` — currently
`~/.worktrees/health_scripts/<имя>`. The directory name comes from git, so the path follows
the repository directory instead of silently becoming outdated.

Do all further work **in this directory**, not in the main copy.

Use Latin letters, digits, and hyphens for the thread name: it becomes part of the branch name and path.

`thread_start.sh` installs the hooks itself. You do not need to run anything manually
after creating the worktree: the hooks directory is shared (`--git-common-dir`), and the installer knows that.

## Close a thread

```bash
scripts/thread_finish.sh <short-name>
```

Run this **from the main copy**, not the thread's worktree: you need to merge into `main`
where `main` is checked out. From the worktree, the script refuses because of the branch and says so explicitly.

The script takes seven steps, and their order is substantive, not cosmetic:

1. **Moves the branch onto the current `main`** (rebase). The thread never merges `main`
   into itself — it catches up once, here;
2. **Tries the merge gates BEFORE the test run** (trial merge without a commit, hook, abort): a gate responds in seconds, the run takes five minutes, and a gate rejection no longer wastes a run (code 14);
3. **Runs the full suite on Studio — once, from the thread's worktree.** After the rebase,
   `main` is an ancestor of the branch, so the future merge commit's tree matches
   the branch's tree, and the run checks exactly what will ship;
4. **Checks whether `main` moved during the run** — if it did,
   merges nothing: the run checked the old base (§12). Exception (September 21):
   changes ONLY in `docs/handoff/**` — the branch is rebased, affected tests
   selected from the changed files run (seconds), and the merge proceeds; no more than 3 rounds;
5. **Merges with `--no-ff`** — so the gate hooks run;
6. **Anchors the handoff note** to the merge commit (the rebase rewrote the thread's internal
   hashes, so the author's anchor would no longer be an ancestor of `main`);
7. **Deploys as an explicit step**, and removes the worktree only after a successful deployment —
   otherwise there would be nowhere to fix it.

Closing now takes about six minutes: the full run is included. That is the cost
of having exactly one run. Previously, there were as many runs as times a neighboring session moved
`main` (measurement from one session on September 15: six runs; only the last proved the result).

## What to do if closing fails

The script distinguishes the cases and tells you which one occurred.

**A gate rejected the merge commit.** Read its message — it names what is missing
(an intent receipt, a sidecar for a new module, a duplicate name). Fix it in your worktree,
commit, and run the closing command again.

**REBASE conflict** (code 11). The thread's changes and `main` touched the same lines before
merging. The script has already run `git rebase --abort`; the branch and worktree are intact. Resolve it
manually in the thread's worktree (`cd ~/.worktrees/health_scripts/<нить> && git rebase main`)
and retry closing. Do not guess at someone else's side — the same rule applies as for
the merge conflict below.

**A merge gate rejected the trial** (code 14). The test run did not start — that is the point. The script restored the main copy after the trial merge. Read the gate's message, fix the issue in the thread's worktree, and retry closing.

**The full run is red** (code 12). Nothing was merged or deployed; the worktree is
still there. If the failure is SOMEONE ELSE'S — not a rare case: on September 15, two such failures lasted a full day —
you can allow closing by stating a reason, and that reason goes into the merge commit:

```bash
THREAD_FINISH_ACCEPT_RED='foreign red BL-XXX-1, reproduced on clean main' \
  scripts/thread_finish.sh <thread>
```

An empty reason is rejected. The purpose of the override is to leave a receipt
in history, not just to “skip”: without it, a thread could become impossible to close because of a neighbor,
encouraging a culture of `--no-verify`.

**The main copy's lock is held** (code 15; since September 23). A neighboring session is closing its thread
and modifying the main copy. The script waited for the lock for up to 10 minutes (`THREAD_FINISH_LOCK_WAIT`,
in seconds) and touched nothing. Retry closing. The lock is held only during the merge,
changelog update, anchor update, and deployment; it is released for the test run. A direct commit to `main`
during this time is rejected by pre-commit with the message `[замок закрытия]` — again, just retry.
Why: on September 23, the anchor from one closing operation landed inside another's merge and became its
merge commit (`d15f91d`). The lock check and path are in `scripts/git-hooks/finish_lock_guard.sh`.

**The main copy is in the middle of someone else's merge** (code 16). Someone manually started `git merge` in
the main copy, bypassing the lock. The script will not commit into someone else's merge. Use
`docs/handoff` to find out whose it is; do not finish or abort it for them.

**`main` moved during the run** (code 13). A neighboring session committed code during those six
minutes (closing handles changes limited to notes on its own, step 4). Nothing was merged. Just retry closing — the script will rebase the branch onto
the new `main` and run the suite again. There is deliberately no internal retry loop: it
would spin precisely when the neighboring session is active.

**A conflict or gate during the actual merge** (code 6). Since September 21, the script runs
`git merge --abort` itself — the main copy is shared and must not sit in the middle of a merge until
someone reads the message. Do not guess at someone else's side —
and if you are closing manually, abort it yourself:

```bash
git merge --abort
```

Keep the branch; your work there is intact. Put a note in the OTHER side's inbox —
`docs/handoff/<чужая-нить>/inbox/<твой-слаг>.md`: whose thread, what conflicts, and
which file. The session with context for one side will resolve it —
the explanation for the other side is in the other commit.

Why not in their `LATEST.md`, as before September 16: that file has one writer —
the thread's author. A neighbor's note in its body is precisely the second writer that
caused conflicts in two of twenty-one closings over a month (§22, measurement on September 16). Read your own
`inbox/` at the start of work along with `LATEST.md`: delivery names the thread and
sender, but cannot open the file for you.

**Someone else's uncommitted work is in the main copy** (code 8). This is a change
to a TRACKED file made by a neighboring thread; the merge would overwrite it, and you cannot decide for
the neighbor. Wait for them to commit. Someone else's untracked drafts
do not block closing — the merge does not touch them.

## If you still work in the main copy

It happens: a one-line edit does not warrant a worktree. Then your only
protection is a **commit with explicit paths**:

```bash
git commit -- <path> <path>
```

`git commit -- <пути>` takes only the named paths and ignores the rest of the index.
Without this, someone else's staged files go into your commit — case 2 above. Running `git add`
on your own file does NOT remove someone else's files from the index; these are different operations.

## What this procedure does NOT do

Your own worktree separates working files and the index. It does NOT separate history (`.git`):
history is shared by all worktrees. So a test that writes a commit into history remains
dangerous — protection comes from clearing the git environment in
`tests/conftest.py`, not from the worktree; the oracle is `tests/unit/test_git_env_isolation.py`.

Also, a merge will not pick up uncommitted work. The script checks for this and
refuses to close a thread while the worktree contains uncommitted changes — silently losing
that work would be worse.

## Why not just `git merge`

Because `git merge` without `--no-ff` performs a fast-forward when `main` has not moved,
and a fast-forward **calls no hooks at all** — neither `pre-commit` nor
`pre-merge-commit`. Measured with a probe on September 12. So the most common case (`main`
did not move overnight) would bypass every gate, with no visible sign.

And `git merge` does not deploy: during a merge, git does not call `post-commit`, which
runs deployment. The work would reach `main` on MacBook and stop silently — neither
reaching Studio nor leaving a line in `deploy.log`.

These are the two reasons closing a thread uses a script instead of a command from memory.
