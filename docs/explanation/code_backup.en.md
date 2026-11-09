<!-- translation-of: docs/explanation/code_backup.md sha256:3f7f14c1f8aa -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](code_backup.md)

# Code and thread work survive a single-disk failure: why it is not as simple as it looks

## What changed

- **An invariant was added: the catalogue keeps only scales usable by at least one tenant.** A measurement on 2 November showed that, on the genomic installations captured by the chip (~643 thousand positions), 706 out of 772 scales matched fewer than 40% of positions and occupied almost all of the 16.5 GB. Scales like these are now removed and will not be downloaded again; new scales larger than the proven limit are not downloaded; the appearance of a genome larger than any seen before lifts the limit. The writer leaves a record for every run, and the nightly sensor turns red if a download has stalled.

- **An invariant was added for snapshots of the shared catalogue of genetic scales (PGS).** The catalogue is one per machine (16.5 GB), and until 02.11.2026 each of the three runs of the monthly download had been taking a copy of it into its tenant's backup folder and keeping five copies — producing 33 GB in a single morning against 72 GB free, on the same disk as the canon and all its backups. Now only the process that has write access to the catalogue copies and downloads it; the copy sits next to the catalogue and is deleted as soon as the catalogue has passed an integrity check after the download; the space required for a run is calculated, and the same calculation judges the nightly sensor.

- **The thread claim wording was refined.** Exactly how the meaning changed was not determined mechanically; the direction of the edit is unknown. Read this as a refinement, not as confirmation that any guarantee was strengthened.

- **A proven limit was added: the archive section of closed threads is never cleaned automatically.** This is a limitation, not an achievement: the owner did not permit deleting records without proof of a merge, so the section grows — one record per thread closed with a content edit during a rebase. The first such case is recorded: close-rebase-once, 21.09.

**Updated:** 2026-11-02


## Why it exists

When you think about backup, the first mental image is a file sitting somewhere, so everything is fine. This system learned several times that the image is misleading.

The first lesson came overnight: the backup script ran faithfully, copied something faithfully — and failed with an access error on 35 out of 36 nights because it was looking in the wrong place. The backup "existed" in exactly the sense in which it did not.

The second lesson was of a different kind. Code was also being copied — committed directly to the main branch and immediately pushed to the server. This meant that every night unverified, unfinished work was quietly deployed into the working system, bypassing all checks.

The third lesson was found by measurement: it turned out that work in threads — small, separate streams of tasks — existed in a single copy on a single disk from the moment of the first commit until the merge. Almost a thousand lines, more than a day of work, in a single copy. Nobody was making a copy of the threads: the script left before reaching them.

The subsystem exists so that none of these three failures can recur silently.

## What it does, in plain language

There are three separate promises here, and it is important not to conflate them.

**The database is copied where it lives.** The database resides on Studio — that is where it is copied. Not from another machine over a network path, not through a sync folder — right in place. After every cycle the sensor verifies that a fresh copy for each tenant has actually appeared.

**The code snapshot does not touch the main branch.** When the system takes a snapshot of the MacBook file tree, the snapshot goes to a separate place on Studio — away from main, where it cannot accidentally be deployed. The main branch does not move; no deployment occurs.

**Thread work exists on two disks.** Every three hours, thread branches are sent as a copy to Studio — into a dedicated quiet section that does not interfere with the file tree. The sensor checks not that the sender reported success, but that the object physically exists on Studio. The copy is not deleted until it is proven that the content reached main: either the commit has become an ancestor, or its content is already there. If a thread was closed with a complex conflict resolution and this cannot be proven — the copy is not deleted but moved to an archive section. The data is retained.

All three cases share one thing: a copy on a second machine, not a second copy on the same disk.

**The shared catalogue snapshot lives only for the duration of the risk.** This is a fourth subject and the reverse case: here the problem was not that there was no copy, but that there were too many. The genetic-scales catalogue can be re-downloaded, so a copy before a download is only needed for the duration of that download. It is taken by one process — the one that has write access to the catalogue (the owner's container sees it read-only and simply recalculates its scores). After the download the catalogue is checked; if intact — the copy is deleted; if not intact — the copy is kept and a message is sent to the error channel. Example: on 2 November three runs made two copies of 16 GB each and would have kept them for two months; now, on that same day, not a single copy would have remained on the disk by evening.

**The catalogue keeps only what works.** A genetic risk score is a list of genome variants with weights. If only a small fraction of those variants have been measured for a person, the score says nothing. The chip measures ~643 thousand positions, while large scales cover millions of variants and are almost empty on the chip: on 2 November, none of the 439 scales with more than a thousand variants even reached 40%. Therefore, a scale that is useless for all people in the installation is deleted along with its weights and remembered so it is not downloaded again; scales larger than the largest working one are not downloaded. The limit is not permanent: when a person with a full genome arrives, the rejections are reconsidered. The visibility boundary is named: the person in the writer's container is not visible to the writer; on the day of the measurement their coverage matched the partner's.

## What is honest to say about its limits

Each of the three promises holds — and each has a boundary that cannot be hidden in fine print.

**On the database.** The sensor judges the freshness of the backup file — whether it appeared on time. It does not check whether recovery from it is possible. An integrity check is a manual step that the machine does not perform. Also: the database and all its daily copies reside on a single disk of a single machine. There is no offsite — this is a conscious decision made by the owner.

**On the code snapshot.** The sensor checks that the required form is present in the configuration. That the script in practice behaves correctly is guarded by a separate test that runs the real script against the real repository and checks whether the main branch has moved.

**On threads.** Several honest things at once.

The loss window is not zero. Work committed to a thread right now will get a copy only on the next cycle. If the disk dies within those three hours — that work exists nowhere.

The sensor does not trigger immediately — there is an intentional delay. Threads live on average about twelve minutes: if the sensor checked more often, it would trigger on every healthy thread that had not yet had time to send a copy. This is a trade-off, not an oversight.

The second machine stands in the same building. This is protection against disk failure, not against fire.

Uncommitted content in the thread tree is not copied. This is measured and known: at the time of the check there were zero uncommitted lines, and the snapshot honestly reports which trees it did not cover.

The archive section of closed threads is never cleaned automatically. Deleting unproven records is not permitted.

## Where this lives in the system

Everything described here lives in `backup.sh` — a single script that behaves differently depending on what it is copying and where. The `--wip` mode is responsible for thread snapshots. The subsystem's intentions and what it promises are recorded in `subsystem_intent.yaml`. The sensors are separate checks that look at the result rather than taking the script at its word.
