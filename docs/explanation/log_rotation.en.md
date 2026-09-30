<!-- translation-of: docs/explanation/log_rotation.md sha256:c74d8b1e0854 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](log_rotation.md)

# Log Rotation: The File Gets Trimmed, and the Writer Won't Be Orphaned

## What Changed

- **Invariant `raw_archive_compressed_not_deleted` added** (status: holds): raw device archives are compressed in place; the original is deleted only after the compressed copy has been successfully read by the loader. A failure of this step while the agent is alive is caught by a separate sensor `check_hae_raw_archive_compressed` — based on files on disk.

**Updated:** 2026-09-26


## Why It Exists

Logs are the system's diary. Every time something happens with the metrics, the device, the nightly cycle — the system writes a line to a file. Silently, in the background, without notifications.

The trouble is that this diary never stops and never cleans itself up. By autumn 2026, around 470 megabytes had accumulated on two machines, and the most important file — the one the whole thing was set up for — was not included in the list of monitored logs at all. It simply fell overboard, and nobody noticed.

That is the main danger here: not noise, but silence. The log stopped growing — maybe everything is fine. Or maybe the system has just gone quiet and nobody knows.

Rotation exists so that files do not grow without bound — and so that at any moment it is clear that the system is alive and writing.

## What It Does, in Plain Terms

When a file reaches the threshold, the rotator does two things in sequence: it copies the contents to an archive and truncates the source file to empty. The file stays in the same place, with the same name, with the same internal number — it simply becomes empty and starts filling up again.

Why not just rename the old file and create a new one? Because the program writing to the log keeps the file open. For that program, the file is not a name but an internal address in the storage system, called an inode. Rename the file — the program will not notice and will keep writing to the same place, at the old address. The new file will stay empty forever, and the system will look alive even though it is actually writing into the void.

So a different approach is used here: the contents go into an archive, and the file itself is truncated in place. The address does not change, and the writer won't be orphaned.

Raw device data archives fall under this same logic: exports older than 14 days are compressed in place, and the original is deleted only after the compressed copy has been read by the same loader. Compressing rather than deleting the old data — because how deep the archive goes determines how the system distinguishes "device is absent" from "data is lost" (the `device_metrics_owner` record).

All settings — which files to rotate, at what size, how long to keep them — live in one place: `launchd/health-logs.newsyslog.conf`. Both mechanisms read exactly that file. A second list somewhere else would mean they would eventually diverge, and again go quiet.

The agent runs under the owner's account, without administrator privileges. There is a standard tool for this — newsyslog — but it requires root. So a custom agent running in user space is used here instead.

Kinds of silence the system is able to notice:

**A log is growing but is not in the list.** A sensor checks whether all growing files are accounted for in the config. A warning appears early; a failure appears when the file has already exceeded the rotation threshold.

**The agent has died.** A sensor checks the freshness of the receipt the agent leaves after each run. No fresh receipt means the agent has not been running.

**The agent is alive but broken.** The same sensor checks whether the receipt ends with a mark confirming that the self-check of the truncation succeeded. The receipt proves "started"; the self-check proves "working correctly".

**The agent is alive but the device archive is not being compressed.** A separate sensor looks directly at disk: an uncompressed export older than 15 days is a failure. The receipt says the step was called; the disk says whether it worked.

And one more protection — not a sensor, but a behavior: an empty or unparseable config is an immediate error, not a silent skip. An empty list looks exactly the same as "everything is clean, nothing to rotate."

## What to Honestly Say About Its Limits

All stated invariants hold — this has been verified. But "holds" and "verified everywhere" are different claims, and it is important not to conflate them here.

The boundary around the agent liveness sensor: it reads a local receipt. This means the agent on a MacBook is covered by its own integrity run, not by a shared sensor. If the machines are different, each one is responsible for itself.

There are no other declared boundaries. There are no disputed or unfulfilled invariants either.

## Where This Lives in the System

The core rotation logic lives in `log_rotate.py`; compression of the raw device archive is in `hae_checker.compress_raw_archive`, and its sensor is `integrity_tests.check_hae_raw_archive_compressed`. The configuration — the single list of logs and thresholds — is in `launchd/health-logs.newsyslog.conf`. This subsystem's place in the overall picture, its purpose, and what it connects to are described in `subsystem_intent.yaml`.
