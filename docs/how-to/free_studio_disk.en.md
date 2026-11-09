<!-- translation-of: docs/how-to/free_studio_disk.md sha256:71c487588b7f -->
**English** · [Русский](free_studio_disk.md)

# How-to: Studio is running out of disk — where to look

The canon, all of its backups and the shared score reference live on one Studio disk (BACKUP_POLICY §R2.5: no offsite), so running out of space hits everything at once. Written after the 2026-11-02 incident (72 GB free, ~33 GB of it eaten by reference snapshots in one morning). Extend only on a new incident.

## Step 1 — how much is free and what is big

```bash
ssh <studio_ssh> 'df -h /System/Volumes/Data | tail -1; du -sh ~/.health_reference ~/health*/backups ~/container_backups ~/.colima 2>/dev/null'
```

The nightly sensor "disk room for the PGS reference top-up" turns red when the next top-up would not fit, and names any hanging snapshots.

## Step 2 — reference snapshots

```bash
ssh <studio_ssh> 'ls -la ~/.health_reference/snapshots/ 2>/dev/null'
```

At rest the folder is empty. A file there is a run after which the reference failed its check (`R2_ref_check` went to the fault channel). **Do not delete by hand**: the next run re-checks the reference and removes the snapshot if the reference is intact; if not, roll back from it.

## Step 3 — deleted, but space did not come back

A deleted file keeps its space while someone else still holds it. Two holders found on 2026-11-02:

```bash
ssh <studio_ssh> 'tmutil listlocalsnapshots / | grep -c TimeMachine
for p in $(pgrep -f com.apple.Virtualization.VirtualMachine); do
  lsof -a -p $p +L1 2>/dev/null | awk "NR>1 {s+=\$7} END {printf \"%d: %.1f GB\n\", $p, s/1e9}"; done'
```

- **Time Machine local snapshots** — hourly, kept about a day. They release on their own; immediately — `tmutil thinlocalsnapshots / <bytes> 4` (hourly restore points are lost; owner's decision).
- **The container VM** (colima) keeps deleted files that a container once opened until it restarts: 16.6 GB on 2026-11-02. `colima restart health` frees it (Health services are down for under a minute; leave the `default` profile with neighbouring projects alone). Not between 03:00 and 08:00: nightly backups and jobs.

## Step 4 — verify

```bash
ssh <studio_ssh> 'df -h /System/Volumes/Data | tail -1'
```

After `colima restart health` — the bot is alive: `docker --context colima-health ps` (all services `Up`) and fresh lines in `/app/logs/bot_err.log` inside `health-bot-1`.
