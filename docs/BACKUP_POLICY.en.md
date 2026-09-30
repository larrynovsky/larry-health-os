<!-- translation-of: docs/BACKUP_POLICY.md sha256:9c0799de4325 -->
**English** · [Русский](BACKUP_POLICY.md)

# Backup Policy — health.db

> Lesson of 2026-06-27: pgs_discovery.py without limits → 31GB WAL → DB corruption.
> Restoring from a 9-day-old backup cost ~4 hours of work.
> This document sets out the rules that make such a gap impossible.

---

## Model

A backup is a replica with **staleness deviation** (a time lag).
The acceptable deviation is how much work we are willing to lose and reproduce.
Under the current system, each day adds ~30 minutes of recoverable work
(Oura, GP report) + irregular operations (constitutions, consilium) taking 15 minutes to 2 hours.

**Staleness SLA = 24 hours** — that is, not a single missed nightly run: since 09/23, the sensor
checks “the latest backup is no older than the last scheduled run of `com.larry.health.backup` according to
the live plist” (owner's “general rule” decision: alert after the first missed run).

---

## Rules

### R0 — Split by machine responsibility (2026-05-09, after BUG-BACKUP-DEAD)

Each machine backs up what it is authoritative for. Two `com.larry.health.backup` jobs, both at 03:00, do DIFFERENT things:

| Machine | Script | What it backs up | Where |
|---|---|---|---|
| **Studio** | `backup_studio.sh` | `sqlite3 .backup` for `~/health/data/health.db` (production) and `<neighbour>/<db>.db` | `~/health/backups/`, `<neighbour>/backups/`, 30-day rotation (R1) |
| **MacBook** | `backup.sh` | CODE snapshot: tree in a temporary index → `commit-tree` → `refs/backups/daily-<дата>` → `push -f` to Studio | last 14 snapshots locally; does not touch the database and never has |

A tenant that moved into a container (wave B pilot, a `RUNTIME` marker containing `container` in its data root) is skipped by the native `backup_studio.sh`: its database lives in the container volume and the container's own job backs it up (`~/health/backups` inside the volume), while the shadow watchdog copies a snapshot to the host every hour. The partner and a neighbour project are backed up natively, as before. ⟨carriers: backup_studio.sh, tests/unit/test_backup_skips_container_tenant.py⟩

What the MacBook script has **not** done since 2026-07-24 (owner's decision): it does not commit to `main` or push `main`. Previously it ran `git commit --no-verify` + push — unverified WIP was deployed at night, all gates (dupgate/L2/docs) were bypassed, and history filled up with “auto: daily backup.” Now a snapshot ≠ a commit to `main`: no deployment, no gate bypass, `main`/index/working tree untouched. Restore a file: `git checkout refs/backups/daily-<дата> -- <файл>`. The rule on uncommitted changes is in `CLAUDE.md §1` (in the private part).

**Why the split — macOS TCC.** Before 2026-05-09, `backup.sh` looked at the iCloud path and **failed with authorization denied on 35 nights out of 36** (the only successful backup was on 2026-04-20): TCC blocks `sqlite3` on `~/Library/Mobile Documents/` when launched by launchd. A second, independent defect in the same design: after 2026-04-24 (single-primary → Studio), the iCloud copy was no longer operational, so even without TCC the script backed up the wrong database. Both have the same remedy: back up the database where it is canonical.

**The iCloud database copy is NOT a backup** (decision of 2026-07-02). `~/Library/Mobile Documents/.../health.db` is a non-operational copy: freshness is neither guaranteed nor checked, and it cannot be relied on. The registry of all code contact points with iCloud is `icloud_contacts.yaml`; registry completeness is maintained by the census sensor `tests/integration/test_icloud_contact_registry.py`.

### R1 — Daily backup (automatic)

`backup_studio.sh` is launched by launchd (`com.larry.health.backup.plist`) every day at **03:00**.
1. `PRAGMA wal_checkpoint(TRUNCATE)` — flushes the WAL before copying
2. `sqlite3 .backup` — a hot copy (does not block readers)
3. `PRAGMA integrity_check` — checks the fresh copy immediately
4. Rotation: backups older than 30 days are deleted

File: `~/health/backups/health_YYYY-MM-DD.db`
Retention: ~33 copies (30-day window).

Check that the backup job works:
```bash
ssh <studio_ssh> "tail -5 ~/health/logs/backup.log"
```

If the latest backup is older than the last scheduled run (currently 03:00 today), the job is broken.
Fix it immediately.
`integrity_tests.py` [11] will detect this automatically and send an alert to Telegram.

### R2 — Pre-operation snapshot (manual, before a destructive run)

Before any operation with an unbounded write effect:
```bash
ssh <studio_ssh> "cp ~/health/data/health.db \
  ~/health/data/health.db.before_<operation>_$(date +%Y%m%d_%H%M)"
```

**When required:**
- Running `pgs_discovery.py` (PGS Catalog import) — **automatically** (see below)
- Running `genome_annotator.py` (ClinVar annotation) — manually
- Running `vcf_import_pipeline.py` (full VCF reimport) — manually
- Any script that performs bulk INSERT without an explicit limit

`pgs_discovery.py` creates a snapshot automatically before Step 4 (import loop):
`~/health/backups/health.before_pgs_discovery_YYYYMMDD_HHMM.db`
through the `sqlite3` online backup API (not `cp`, which does not capture an open WAL).

**When not needed:**
- `generate_constitutions.py` (writes only to `constitutions`, does not change raw data)
- `import_oura.py` (upsert by date, idempotent)
- `fill_description_ru.py` (UPDATE by key, idempotent)

**Snapshot lifetime — 60 days (R1b, owner's decisions on 2026-09-27).** A snapshot is needed to
roll back an action while its consequences have not yet been verified; afterward, rollback uses daily backups (R1,
30 days). On the morning of 09/27, retention was set to 30 days and only for the data directory; in the afternoon, the owner
decided “delete now and make the rule 60 days” — retention became 60 days and extended to `backups/`.
Before that, snapshots lived forever: measurement on 09/27 — 134 files, 7GB in the owner's data directory, 26 files in
the partner's (BL-SNAPSHOT-SPRAWL-1), and another 16GB in `backups/` (PGS catalog snapshots of 5–10GB
monthly, the partner's July snapshots of 3.8GB). `backup_studio.sh` deletes them on the same night as
old backups (`ACT_SNAPSHOT_DAYS`):
- `scripts/rotate_act_snapshots.sh <tenant>/data` — a snapshot is recognized by structure: a SQLite file
  (other than the canonical database), a file with `.bak` in its name, anything in `<tenant>/data/backups/`;
- `scripts/rotate_backup_snapshots.sh <tenant>/backups` — everything is a snapshot except daily
  `<tenant>_YYYY-MM-DD.db` files (R1) and the `<tenant>/backups/keep/` folder.
Any name can be used. **A snapshot needed for more than 60 days goes into `<tenant>/backups/keep/`** —
deliberately, in one move, and stays until removed from there.

### R2.5 — The database has NO offsite backup (measured on 2026-08-02) ⚠️

`ssh <studio_ssh> tmutil destinationinfo` → **“No destinations configured”**; `tmutil latestbackup` → mount error. Time Machine is not configured on Studio.

Meaning: the canonical database (`~/health/data/health.db`) and **all** 30 days of backups (`~/health/backups/`) are on one disk in one machine. Failure of Studio's disk takes both at once. R1 protects against logical damage (WAL, corruption, an erroneous bulk UPDATE) and **does not protect against storage failure**.

Where the discrepancy came from: on 2026-05-09, the decision “offsite backup is delegated to Time Machine” was made (`BACKLOG_ARCHIVE.md:830`); the decision was NOT implemented, but the item was archived as closed — nothing was left to watch it. Until 2026-08-02, `BLUEPRINT` repeated it as fact. The header of `backup.sh` itself (“Offsite database backup is not configured yet”) told the truth all that time, and no one compared them. An instance of `CLAUDE.md §18` (in the private part): a claim about state OUTSIDE its artifact, without a counter or a date.

Code resides on two machines: a clone on MacBook + `refs/backups/` on Studio. The database is on one.

**Correction of 2026-09-16: “two machines” was NOT true for all code.**
Before 09/16, only `main` resided on two machines: `post-commit` pushes it and exits before push
if the branch is different (“not main → exit BEFORE push” is written in the hook itself), and the snapshot was taken from
the main working copy. This means committed thread work, from its first commit to merge,
existed in ONE copy. Measurement on 09/16: 992 lines in two threads (`close-rebase-once`
757 lines / 22 hours, `dead-root-loud` 235 / 62 hours), and `git cat-file -e` on Studio answered
“no” for both tips. A pure instance of `CLAUDE.md §18` (in the private part):
a claim about a neighboring machine without a counter or a date outlived its subject.
Since 09/16, `backup.sh --wip` copies thread branches to `refs/backups/thread/*` (the namespace
is inert: `updateInstead` touches Studio's tree only for the checked-out branch), and the sensor
`check_thread_work_has_a_second_copy` checks for the object's PRESENCE on Studio, not a report of
a successful push. A copy is deleted only when PROVEN to have reached `main` (an ancestor, or the same content according to `git cherry`) — the owner's decision:
`git push --prune` would delete the copy of a locally deleted branch, but a branch disappears both on
merge and on loss of its working tree. The loss window is not zero but the snapshot interval (3 hours).
Since 09/21, closing a thread rebases its branch; if its content was rewritten in the process, the copy
is not deleted but moved to `refs/backups/thread-closed/<нить>` — the data remains,
and the list of “work with only one copy” stops filling up with closed threads.
Uncommitted changes in thread working trees are still not copied: the p90 interval between
commits within a thread is 11 minutes, and the snapshot carries `worktrees_uncovered=N` as an explicit
“I do not know about these trees.” The full intent is the `code_backup` entry in `subsystem_intent.yaml`.

**Owner's decision on 2026-08-13: the risk is explicitly accepted; there will be no offsite backup.** The earlier option A (“external disk + Time Machine”, 2026-08-03) was struck out; the `check_offsite_configured` sensor was deleted (`aec8173`). The item was closed by deliberate acceptance of risk, not implementation: failure of Studio's storage takes the canonical database and all copies at once, and since that date only this sentence guards it. If you change your mind, a new sensor is introduced with the new decision, not before (§9: no norm before a mechanism).

### R3 — Integrity check before using a backup

Restore only from a backup that has passed the check:
```bash
sqlite3 /path/to/backup.db 'PRAGMA integrity_check'
# Should return: ok
```

The backup size must be reasonable (< 500MB at the current data volume).
A larger size indicates an uncommitted WAL, not a valid backup.

---

## Sensors (integrity_tests.py [11])

Implemented in `integrity_tests.py`, section [11] “Backup and database size”:

- [x] `check_backup_freshness()` — the latest `<tenant>_*.db` covers the last scheduled backup run (since 09/23; previously 25 hours); FAIL if it does not
- [x] `check_db_size()` — WARN at >300MB, FAIL at >500MB
- [x] Run daily at 07:50 (before the morning Telegram report at 08:00)

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
| **Total for a 1-day gap** | **~60 min** |
| **Extended gap** | **Estimate from the data actually lost** |

---

## Antipatterns (do not do this)

- `backup.sh` without checking the exit code → silent failure
- backup with an open WAL (`cp` while writes are in progress) → invalid file
  → use `.dump` or `VACUUM INTO` for an online backup
- keeping only one backup → no way to roll back 2 days
- treating `integrity_check = ok` as sufficient → does not catch WAL corruption or size bloat
