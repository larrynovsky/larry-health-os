<!-- translation-of: docs/BACKUP_POLICY.md sha256:5b8117032e66 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](BACKUP_POLICY.md)

# Backup Policy — health.db

> Lesson 2026-06-27: pgs_discovery.py without limits → WAL 31GB → DB corruption.
> Restoring from a 9-day-old backup cost ~4 hours of work.
> This document fixes the rules that make such a gap impossible.

---

## Model

A backup is a replica with a **staleness deviation**.
Acceptable deviation = how much work we are willing to lose and reproduce from scratch.
Under the current system: each day accumulates ~30 min of recoverable work
(Oura, GP report) + irregular operations (constitutions, consilium) ranging from 15 min to 2 hours.

**Staleness SLA = 24 hours** — meaning not a single missed nightly run: from 23.09 the sensor
judges "freshest backup is no older than the last scheduled run of `com.larry.health.backup` per
the live plist" (owner decision "general rule": alert after the first missed run).

---

## Rules

### R0 — Separation of responsibility by machine (2026-05-09, after BUG-BACKUP-DEAD)

Each machine backs up what it is the authority for. Two `com.larry.health.backup` jobs, both at 03:00, do DIFFERENT things:

| Machine | Script | What it backs up | Where to |
|---|---|---|---|
| **Studio** | `backup_studio.sh` | `sqlite3 .backup` for `~/health/data/health.db` (production) and `<neighbour>/<db>.db` | `~/health/backups/`, `<neighbour>/backups/`, 30-day rotation (R1) |
| **MacBook** | `backup.sh` | CODE snapshot: tree in a temporary index → `commit-tree` → `refs/backups/daily-<date>` → `push -f` to Studio | 14 most recent snapshots locally; does not touch the DB and never has |

A tenant that has moved into a container (pilot of wave B, label `RUNTIME` with the word `container` in the root of its data) is skipped by the native `backup_studio.sh`: its database lives in the container volume, and backups are made by the container's own task (`~/health/backups` inside the volume), while a shadow watcher copies a snapshot to the host once an hour. The partner and the neighbouring project are backed up natively, as before. ⟨carriers: backup_studio.sh, tests/unit/test_backup_skips_container_tenant.py⟩

What the MacBook script does **not** do as of 2026-07-24 (owner decision): it does not commit to `main` and does not push `main`. Previously it did `git commit --no-verify` + push — at night unreviewed WIP was deployed, all gates were bypassed (dupgate/L2/docs), and history filled up with "auto: daily backup" messages. Now a snapshot ≠ a commit to `main`: no deployment, no gate bypassing, `main`/index/working tree are untouched. To restore a file: `git checkout refs/backups/daily-<date> -- <file>`. The norm on uncommitted work is `CLAUDE.md §1` (in the private section).

**Why separated — macOS TCC.** Before 2026-05-09 `backup.sh` looked at an iCloud path and **failed with authorization denied 35 nights out of 36** (the only successful backup was 2026-04-20): TCC blocks `sqlite3` on `~/Library/Mobile Documents/` when launched from launchd. A second, independent defect of the same design: after 2026-04-24 (single-primary → Studio) the iCloud copy ceased to be operational, so even without TCC the script was backing up the wrong DB. Both are fixed by one thing — the database is backed up where it is canonical.

**The iCloud DB copy is NOT a backup** (decision 2026-07-02). `~/Library/Mobile Documents/.../health.db` is a non-operational copy: freshness is not guaranteed and not checked, it cannot be relied upon. The registry of all code contact points with iCloud is `icloud_contacts.yaml`; registry completeness is maintained by the census sensor `tests/integration/test_icloud_contact_registry.py`.

### R1 — Daily backup (automatic)

`backup_studio.sh` is launched by launchd (`com.larry.health.backup.plist`) every day at **03:00**.
1. `PRAGMA wal_checkpoint(TRUNCATE)` — flushes WAL before copying
2. `sqlite3 .backup` — hot copy (does not block readers)
3. `PRAGMA integrity_check` — validates the fresh copy immediately
4. Rotation: backups older than 30 days are deleted

File: `~/health/backups/health_YYYY-MM-DD.db`
Retention: ~33 copies (30-day window).

To check that the backup job is working:
```bash
ssh <studio_ssh> "tail -5 ~/health/logs/backup.log"
```

If the most recent one is older than the last scheduled run (currently — 03:00 today) — the job is broken.
Fix immediately.
`integrity_tests.py` [11] will catch this automatically and send an alert to Telegram.

### R2 — Pre-operation snapshot (manual, before a destructive run)

Before any operation with an unbounded write effect:
```bash
ssh <studio_ssh> "cp ~/health/data/health.db \
  ~/health/data/health.db.before_<operation>_$(date +%Y%m%d_%H%M)"
```

**When mandatory:**
- Running `pgs_discovery.py` (PGS Catalog import) — **automatically** (see below)
- Running `genome_annotator.py` (ClinVar annotation) — manually
- Running `vcf_import_pipeline.py` (full VCF reimport) — manually
- Any script that performs a bulk INSERT without an explicit limit

`pgs_discovery.py` creates a snapshot automatically before Step 4 (import loop):
`~/health/backups/health.before_pgs_discovery_YYYYMMDD_HHMM.db`
via the `sqlite3` online backup API (not `cp` — does not capture an open WAL).

**When not needed:**
- `generate_constitutions.py` (writes only to `constitutions`, does not change raw data)
- `import_oura.py` (upsert by date, idempotent)
- `fill_description_ru.py` (UPDATE by key, idempotent)

**Snapshot lifetime — 60 days (R1b, owner decisions 2026-09-27).** A snapshot is needed to
roll back an action while its consequences have not been verified; after that, rollback goes through daily backups (R1,
30 days). On the morning of 27.09 the lifetime was set to 30 days and only for the data directory; during the day the owner
decided "delete now and make a 60-day rule" — the lifetime became 60 days and was extended to `backups/`.
Before this, snapshots lived forever: measurement on 27.09 — 134 files, 7 GB in the owner's data directory, 26 in the
partner's (BL-SNAPSHOT-SPRAWL-1), and another 16 GB in `backups/` (PGS catalog snapshots of 5–10 GB
monthly, partner's July snapshots at 3.8 GB each). They are deleted by `backup_studio.sh` on the same night as
old backups (`ACT_SNAPSHOT_DAYS`):
- `scripts/rotate_act_snapshots.sh <tenant>/data` — a snapshot is identified by its nature: a SQLite file
  (other than the canonical one), a file with `.bak` in its name, anything in `<tenant>/data/backups/`;
- `scripts/rotate_backup_snapshots.sh <tenant>/backups` — a snapshot is everything except the daily
  `<tenant>_YYYY-MM-DD.db` files (R1) and the `<tenant>/backups/keep/` folder.
Any name can be used. **A snapshot needed for longer than 60 days is placed in `<tenant>/backups/keep/`** —
deliberately, in a single action, and it stays there until explicitly removed.

### R2.5 — The DB has NO offsite (measurement 2026-08-02) ⚠️

`ssh <studio_ssh> tmutil destinationinfo` → **"No destinations configured"**; `tmutil latestbackup` → mount error. Time Machine is not configured on Studio.

Implication: the canonical DB (`~/health/data/health.db`) and **all** 30 days of backups (`~/health/backups/`) reside on a single disk of a single machine. A Studio disk failure takes both out simultaneously. R1 protects against logical corruption (WAL, corruption, erroneous bulk UPDATE) and **does not protect against storage media failure**.

How the discrepancy arose: on 2026-05-09 a decision was made to "delegate offsite to Time Machine" (`BACKLOG_ARCHIVE.md:830`), the decision was NOT carried out, yet the item was archived as closed — leaving nothing to track it. `BLUEPRINT` repeated it as a fact until 2026-08-02. The header of `backup.sh` itself ("Offsite for the DB is not configured yet") had been telling the truth all along, and nobody compared the two. Instance `CLAUDE.md §18` (in the private section): a statement about state OUTSIDE the storage medium, without a counter and without a date.

The code lives on two machines: a clone on MacBook + `refs/backups/` on Studio. The database — on one.

**Amendment 2026-09-16, and it is about the fact that "two machines" was NOT true for all code.**
Before 16.09 only `main` lived on two machines: `post-commit` pushes it and exits before the push
if the branch is something else ("not main → exit BEFORE push" is written in the hook itself), and the snapshot was taken from the primary copy. This means committed thread work from the first commit to the merge
existed in ONE copy. Measurement on 16.09: 992 lines across two threads (`close-rebase-once`
757 lines / 22 h, `dead-root-loud` 235 / 62 h), and `git cat-file -e` on Studio returned
"no" for both tips. Instance `CLAUDE.md §18` (in the private section) in its pure form:
a statement about a neighbouring machine without a counter and without a date outlived its subject.
From 16.09 `backup.sh --wip` copies thread branches into `refs/backups/thread/*` (the namespace
is inert: `updateInstead` only touches the Studio working tree for the checked-out branch), and the sensor
`check_thread_work_has_a_second_copy` judges the PRESENCE of the object on Studio, not a report of a
successful push. A copy is deleted only when it has DEMONSTRABLY reached `main` (ancestor or same content by `git cherry`) — owner decision:
`git push --prune` would delete the copy of a branch deleted locally, and a branch disappears both on
merge and on tree loss. The loss window is not zero, but the snapshot interval (3 h).
From 21.09 closing a thread moves the branch; if its content was rewritten in the process, the copy
is not deleted but moved to `refs/backups/thread-closed/<thread>` — the data is retained,
and the list of "work in a single copy" stops being cluttered with closed threads.
Uncommitted work in thread working trees is still not copied: the p90 inter-commit interval
within a thread is 11 minutes, and the snapshot carries `worktrees_uncovered=N` as an honest
"nothing known about these trees". The full design is the `code_backup` entry in `subsystem_intent.yaml`.

**Owner decision 2026-08-13: risk explicitly accepted, there will be no offsite.** The former option A ("external drive + Time Machine", 2026-08-03) is crossed out; the `check_offsite_configured` sensor is deleted (`aec8173`). The item is closed not by implementation but by conscious acceptance of risk: a Studio storage failure takes the canonical copy and all backups at once, and from this date that is guarded only by this sentence itself. If you change your mind — a new sensor is created together with a new decision, not before (§9: the norm not before the mechanism).

### R3 — Integrity check before using a backup

Restore only from a backup that has passed validation:
```bash
sqlite3 /path/to/backup.db 'PRAGMA integrity_check'
# Should return: ok
```

The backup size must be reasonable (< 500MB at current data volume).
If larger — this is a sign of an unflushed WAL, not a valid backup.

---

## Sensors (integrity_tests.py [11])

Implemented in `integrity_tests.py`, section [11] "Backup and DB size":

- [x] `check_backup_freshness()` — the most recent `<tenant>_*.db` covers the last scheduled backup run (from 23.09; before that — 25 h); FAIL if not
- [x] `check_db_size()` — WARN at >300MB, FAIL at >500MB
- [x] Fire daily at 07:50 (before the morning Telegram report at 08:00)

---

## Recovery cost (reference)

| What was lost | Recovery time |
|---|---|
| 1 day of Oura | 5 min |
| GP weekly report | 10 min |
| 5 constitutions | 15 min |
| Consilium | 20 min |
| description_ru (description translations) | Depends on volume and source availability |
| Phases E/F/G/H | 30 min |
| init_db + manual ALTERs | 5 min |
| **Total for 1-day gap** | **~60 min** |
| **Extended gap** | **Estimate based on data actually lost** |

---

## Anti-patterns (do not do)

- `backup.sh` without checking exit code → silent failure
- backup with open WAL (`cp` while writes are in progress) → invalid file
  → use `.dump` or `VACUUM INTO` for online backup
- storing only one backup → no ability to roll back 2 days
- treating `integrity_check = ok` as sufficient → does not catch WAL-corruption and size bloat
