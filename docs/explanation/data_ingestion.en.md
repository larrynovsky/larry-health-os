<!-- translation-of: docs/explanation/data_ingestion.md sha256:d7febd65fffa -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](data_ingestion.md)

# Data Ingestion: Single Canonical Database (single-primary): Where the One Source of Truth About the Body Comes From

## What Changed

- **A new invariant added** `writer_machine_runs_no_data_jobs` (status: holding): it is now recorded that the machine on which code is written (MacBook) does not run jobs that work with medical data. Permitted jobs on the MacBook are listed explicitly; everything else is considered a violation by construction.
- **Why**: on 06.11 two jobs were found on the MacBook that had been failing into unread logs for months — KAYAK trips and Oura import. The rule is checked automatically: a MacBook snapshot taken every 3 hours carries the list of loaded jobs, and Studio compares it against the allowed list.

**Updated:** 2026-11-06


## Why It Exists

Imagine keeping a health diary in several notebooks at once — one on your desk, one on your phone, one somewhere in the cloud. Sooner or later they start to diverge: one has yesterday's blood pressure, another has the day before's, and it is unclear which one to trust. When there is a lot of data and several devices, this is not a hypothetical inconvenience but an almost inevitable outcome.

Body data arrives from everywhere: wearable devices, photos of lab results, different computers. Without a specific order, each source starts living its own life, producing copies and versions. The single canonical database subsystem exists precisely so that this disorder does not arise: so that there is one point in the system that can be trusted, and so that no other point can quietly pretend to be that one point.

## What It Does, in Plain Words

The entire subsystem is built around one principle: **only one machine can write to the database** — the always-on Studio. Other devices can view the data but not change it. This is not a request or an agreement — it is a technical constraint: a connection from any other machine opens in read-only mode.

If someone still wants to bypass this rule, they need to explicitly and deliberately enable a special flag (`ALLOW_WRITE_NONPRIMARY`). This cannot happen accidentally.

The second important thing is the treatment of stale copies. iCloud can store a copy of the database on other devices, and that copy may lag behind the real one by a day or two. The system does not allow it to quietly substitute itself for current data. If a machine that is not supposed to write has no explicit pointer to where the real database is (`HEALTH_DATA_DIR`), the system does not stay silent or guess — it fails loudly with an error. An explicit failure that cannot be missed is better than yesterday's data presenting itself as today's.

There is a third aspect: the machine on which the project code is written (MacBook) is not supposed to run jobs that work with medical data at all. Only repository maintenance jobs run on a schedule there — backups, log rotation, iCloud conflict resolution. If some process on the MacBook tries to work with data in violation of the rules, it will either hit the write prohibition or miss the canonical database entirely. What is permitted on the MacBook is recorded as an explicit list; everything not on that list is considered a violation by construction, not by mistake.

At the same time, the system is honest about one important thing: a loud failure only counts when someone hears it. If an error occurred but nobody saw it — it does not count as protection.

## What to Honestly Say About Its Limits

All described invariants are holding as of today. But "holding" and "verified under all conditions" are different claims.

Limit of the MacBook check: a snapshot is taken every 3 hours and only while the laptop is on — a new job is visible with a delay of up to 3 hours. A job that runs project code from outside the project folder is not visible at all.

Limit on failure visibility: protection against a silent stale copy works through a loud failure. But the loudness of a failure only matters if someone is present to notice it. For this reason, failures on the MacBook are no longer left to the logs: their list is sent to Studio, where the daily check reads it.

Limit on import: wearable devices and lab result recognition are also part of this subsystem, but their own invariants are described separately and are not verified here.

None of the above means the system is unreliable. It means that reliability is not magic, but specific verified agreements with specific limits.

## Where This Lives in the System

The technical structure of the database is described in `health_db.py` — that is where the connection logic, WAL mode, separation of reading and writing nodes, and the `HEALTH_DATA_DIR` environment variable check live.

The intent of the subsystem as a whole — why it is designed this way and which invariants it holds — is recorded in `subsystem_intent.yaml`. This is a document of intent: it says not how the code is structured, but why it is structured that way.

Together these two files form a pair: one answers the question "what is happening", the other answers "what for".
