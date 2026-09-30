<!-- translation-of: docs/explanation/git_architecture.md sha256:7685086d99c6 -->
**English** · [Русский](git_architecture.md)

# Git architecture: single canonical Studio

> **Document type:** Explanation (Diátaxis) — explains **why** it is this way, not how to do it.
> Specific commands: [docs/how-to/git_workflow.md](../how-to/git_workflow.md).
> Current state: CLAUDE.md § C "Environment" (in the private part) — the rule; [docs/how-to/git_workflow.md](../how-to/git_workflow.md) — commands.

---

## Before 2026-05-09

Two independent git repositories — on MacBook (`~/health_scripts/.git/`) and Studio (`~/health_scripts/.git/`) — were synchronized at the filesystem level through `rsync`. Each machine's git knew nothing of the other's existence: its own `.git/` was its sole source of truth.

This almost worked as long as their HEADs matched. But both machines made **independent auto-commits at 03:00** through `backup.sh`. The content was almost identical (synced files), but git created two different SHAs — each machine had its own "history of the same day." Over a month, the divergence accumulated into dozens of parallel commits.

In distributed systems terms (Tanenbaum §7), this was **active replication**: each "replica" (machine) autonomously applied the same operation. Active replication provides **availability** (any machine can write at any time), but pays in **divergence** — without coordination, histories diverge.

By 2026-05-09, the gap had become painful: I (Claude) had made a series of edits during the day, and there was no unambiguous answer to "which commit should be considered canonical" — MacBook 12:21 (`6fb6fc6`) or Studio 03:00 (`b785a88`)?

## After 2026-05-09

Studio was declared the **single canonical primary** for git. MacBook's `.git/` was deleted. MacBook is now a **read-only working copy**: files are edited here and synced to Studio through doc-sync (for `.md`) or explicit `rsync`/ssh (for `.py`). All commit operations happen only on Studio.

In §7.2 terms, this is a transition from active replication to a **primary-backup protocol**: one machine (Studio) has write authority; the others can only read. This reduces availability (no commits if Studio is offline), but eliminates divergence as a class of problems.

## After 2026-05-23 (Sprint 7-GIT)

Sprint 6 experience showed that "MacBook without `.git/`" was being violated in practice. `.git/` was restored on MacBook on 2026-05-23 (after a failure), and I (Claude) made 17 commits from MacBook in one day. The `post-commit` hook ran `rsync --exclude='.git'` — files reached Studio, but history stayed only on MacBook. Studio HEAD fell behind by the entire Sprint 6 (17 commits sat as uncommitted changes in the working tree). A manual fix through rsync of `.git/` was required (a dangerous operation).

**Architectural conclusion:** "MacBook without `.git/`" is an unstable invariant. AI assistants need local git history (refactor batches, `git status`/`log` without an ssh round-trip), and attempts to prohibit it end with "illegal" restoration. Better to legitimize MacBook git as a clone of Studio and explicitly configure sync through the push protocol.

The new model — **Studio as git remote, MacBook auto-pushes**:

- MacBook has `.git/` (cloned from Studio).
- On Studio: `git config receive.denyCurrentBranch=updateInstead` — pushing to a checked-out branch is allowed; the working tree updates automatically after a fast-forward.
- `git remote add studio <studio_ssh>:~/health_scripts` on MacBook.
- The `post-commit` hook on MacBook runs `git push studio main` (instead of `rsync --exclude='.git'`).
- `backup.sh` (daily 03:00 auto-commit) runs `git commit + git push studio` locally on MacBook.
- ssh-edit on Studio is still allowed (CLAUDE.md §1). After ssh-edit + commit on Studio, the next MacBook session starts with `git fetch studio && git reset --hard studio/main` (or `bash scripts/sync_from_studio.sh`).

**What this provides:**

- Git as the sync protocol — the fast-forward check protects against overwriting. Push fails fast on divergence (MacBook behind Studio); the error goes into `logs/deploy.log`, and the bot is not restarted on outdated code.
- Atomicity: `git push` either succeeds completely or not at all. `rsync .git` could get stuck halfway because of a `.git/index` lock.
- Studio's working tree updates automatically through `updateInstead` — no separate `rsync` step for files.
- A `dirty working tree` on Studio at push time → `updateInstead` refuses. This is the correct signal: someone is editing Studio through ssh-edit; wait for a commit on Studio + pull on MacBook.

**What we get structurally:** Studio remains **canonical** in the sense of "the only production instance" (the bot, database, and scheduled jobs live only here). MacBook is a **full client**, not deprived of VCS. Authority is distributed as follows: new code may come from either machine, but Studio has the final say through the fast-forward check.

In Tanenbaum's terms, this is **multi-master with pessimistic conflict detection through the fast-forward invariant**, not full multi-master (no automatic merge). Divergence is theoretically possible (simultaneous commits on both machines), but rare in practice: AI performs ssh-edit on Studio only when explicitly needed (for example, restoring git). The normal flow is Edit on MacBook → commit → auto-push.

## What we gained

**Eliminated bug classes**:

- Parallel auto-commits with identical content and different SHAs (previously: +2 duplicate commits every day).
- Impossible merge conflicts when trying to reconcile two trees whose ancestor is two weeks old.
- The question "which history is canonical" for critical documents (BLUEPRINT, ARCH_SNAPSHOT, USE_CASES) — now unambiguous.

**A linear history**: each commit has one parent, enabling working `git bisect`, `git blame`, and meaningful `git log`.

## What we lost

**Availability when Studio is offline**. If Tailscale goes down or Studio is off, `backup.sh` on MacBook logs `WARN: ssh trigger failed`, and no commit is made. The next day, once connectivity returns, the commit succeeds normally. This is acceptable: our Tailscale is stable, and losing one auto-commit point is not a disaster (edits remain in files; they are not lost).

**Local git blame on MacBook**. After deleting `.git/`, the MacBook editor no longer shows git history. If blame is needed, use ssh: `ssh studio "git -C ~/health_scripts blame файл.py"`. This is rarely needed in daily work.

**Easy rollback through `git checkout` on MacBook**. Rollback must be done on Studio: `ssh studio "git checkout HEAD~ -- файл.py"`, followed by `rsync` back to MacBook (or wait for doc-sync for `.md`).

## Alternatives considered

**Alternative A: keep two repos and merge regularly.** Rejected — +2 diverging commits every day, leading to 700 unnecessary merge commits in a year.

**Alternative B: make MacBook the source and Studio the mirror.** Rejected — Studio runs production tasks (bot, API, tests), and there is no architectural reason to give MacBook git authority.

**Alternative C: a bare repo on a third machine + push/pull from both.** Rejected as excessive complexity. We have one user, not a team.

> **The "not a team" assumption is outdated (2026-09-11).** There is still one user, but five or more parallel threads, which is indistinguishable from a team to git: simultaneous writers, competition for the deployment channel, and work that must not be mixed into one commit. The decision nevertheless does NOT change: a bare repo on a third machine was not needed; the gap was closed more cheaply — a separate tree per thread (§1 of the multisession protocol) plus a branch guard in post-commit. This note is here not to change the decision, but because the rationale would silently outlive its subject (§18): the next reader would accept "not a team" as a current fact and use it to reject something else. The cost of the current arrangement is stated there too: branches do not deploy, and silence no longer means success.

**Alternative D (chosen): Studio as single canonical, MacBook without `.git/`.** Minimal complexity, zero divergence.

## Compatibility with the single-primary database rule

Database rule (CLAUDE.md §8, introduced 2026-04-24): **Studio is the only primary for health.db**. After the git migration, Studio is also the only primary for the git repo. Both rules follow one architectural idea: **one machine — one responsibility for each resource, MacBook = client**.

In Tanenbaum's terms, this reduces **conit numerical deviation** to zero for both resources.

## Disaster recovery

If Studio is permanently lost (SSD failure, theft, etc.):

1. Time Machine contains a snapshot of `~/health_scripts/`, including `.git/` — restore git history through TM.
2. In parallel: the bundle from 2026-05-09 (`/tmp/macbook_health_v2.bundle`) is stored locally on MacBook — the last git history backup before migration.
3. The iCloud database copy has been stale since 2026-04-24 (under the single-primary rule) — data recovery requires Time Machine.

If MacBook is lost, losses are minimal: restore the working copy through `rsync <studio_ssh>:~/health_scripts/ ~/health_scripts/`.

## Lesson from the 2026-05-10 migration

After reset --hard on MacBook, the canonical contract test caught a regression: `morning_test_summary.py` again had the 7-parameter `save_agent_report` signature instead of 12. Root cause: the W2C-2 fix had been made **only** through an ssh patch on Studio and had not reached MacBook (`.py` is not synced through doc-sync; nobody explicitly rsynced it back to MacBook). The migration bundle was built on MacBook → ssh-only fixes were absent → reset --hard physically removed them from Studio's working tree.

The same applies to `run_full_test_suite.sh` (the W2C-3 case map for the snapshot layer).

**What matters for future migrations**:

1. **Before any reset --hard to an old commit**, compare md5 hashes of critical `.py` and `.sh` files on both machines. If they differ, MacBook has outdated code, while Studio has current code that will be lost in the reset.
2. **Strategy**: before building the bundle on MacBook, reverse-rsync `studio:~/health_scripts/*.py → MacBook`, then commit "sync: ssh-only edits from Studio" on MacBook, then bundle. This puts fresh Studio code into the bundle.
3. **After any reset --hard**, run `pytest tests/unit/test_save_agent_report_contract.py` and the full suite before the merge commit. This check costs 5 seconds and catches precisely this bug class.

This discipline is formalized in Wave 3-DOC P1-3 as T-P1-А. Today's regression confirms the value of the contract test created on 2026-05-09 specifically for the BUG-AGENTREPORTS-SIG class. The test caught its own predecessor.

## Related documents

- [docs/how-to/git_workflow.md](../how-to/git_workflow.md) — specific commands for daily work.
- CLAUDE.md §8 (in the private part) — the related single-primary rule for health.db.
- [docs/BACKUP_POLICY.md §R0](../BACKUP_POLICY.md) — the MacBook/Studio backup split, aligned with the git migration.
- `CHANGELOG.md` (log in the private part of the project) — the 2026-05-09 v3.2 entry on migration and the 2026-05-10 entry on regression+recovery.

## Uncommitted work: who protects it and who sees it (2026-09-07)

The 05-23 move made MacBook the place where code is written and Studio the deployment target. This
move had a silent side effect, noticed only now.

`uncommitted_watchdog` was built on 05-17 for the class "work was written and not committed;
regressions revealed it a day later." At that time, code was written on Studio, and "forgotten commit"
and "dirty deployment target" referred to the same tree. After 05-23, they are two separate
trees on two machines — but the guard stayed on Studio. It is still needed and catches
real issues (ssh edits bypassing MacBook, a failed push, an `updateInstead` conflict —
the 07-05 incident: 25 hours dirty), but the original class moved with the work and for a month and a half
was covered by nothing except the daily `backup.sh` snapshot. One `launchctl` check on MacBook
would have revealed this — but it was not done, because the guard's docstring
confidently said it guarded exactly that (§18: a claim outlived its
subject).

Fixed from two sides.

**Protection — more often.** `backup.sh --wip` snapshots the working tree every three hours
(launchd `com.larry.health.backup-wip`, MacBook-only) into one rolling
`refs/backups/wip` and pushes it to Studio. The loss window for unsaved work went from
≤24 hours to ≤3. Daily `refs/backups/daily-*` and their rotation of 14 remain
unchanged — they preserve history; wip preserves freshness. The mechanism is the same: snapshot in
a temporary index → `commit-tree` → a ref outside `main`; no deployment and no bypassing gates.

**Observation — from the always-on machine.** `integrity_tests
.check_macbook_uncommitted` compares the newest snapshot with its parent: anything outside
`git_facts.MACHINE_REGENERATED_FILES` is uncommitted human work. A WARN
of class `decide`, meaning a line in the morning delivery, not FAIL: whether to commit
is the owner's decision (§13).

**Why a guard was NOT installed on the laptop.** A laptop cannot have an honest liveness
signal: it legitimately sleeps, travels, and sits with its lid closed; from within, "the guard died"
is indistinguishable from "the lid is closed." A sensor whose silence means both violates
§14 by construction. Studio, however, is always on and already sees MacBook's tree — the snapshot
arrives here on its own. So we judge the artifact and ask the laptop nothing.

**What this filter cost and what it hides.** Without a filter for derived files, the sensor would be
pure noise: the measurement across 14 stored snapshots (09-07) showed a signal on 14 days out of 14, because
`doc_agent` stages `CHANGELOG.md`/`ARCH_SNAPSHOT.md` after EVERY commit, and
`arch_guard` rewrites `.arch_graph.json`. With the filter, 2 out of 14, and both hits
are real: 07-30 (five work files) and 08-04 (a manual edit to `CLAUDE.md`). The cost
is stated honestly: the filter uses NAME, not authorship, so the sensor will not see a manual
edit to `CHANGELOG.md`.

**What remains uncovered, stated aloud.** A stale snapshot cannot distinguish "the laptop is off"
from "the snapshot job is dead" — from here, they are indistinguishable in principle, so the warning
text names both readings, and the threshold stays high (3 days) to avoid ringing on weekends.
Also, `com.larry.health.backup-wip` is outside the `producer_registry` census: that traverses
launchd on the machine running integrity, namely Studio, while this job is on MacBook.
No census of machines other than Studio has been built in the project at all.
