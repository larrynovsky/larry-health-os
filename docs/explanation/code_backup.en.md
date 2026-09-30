<!-- translation-of: docs/explanation/code_backup.md sha256:a35e6a0e3e02 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](code_backup.md)

# Code and thread work survive a single-disk failure: why it is not as simple as it looks

## What changed

- **The claim about threads has been reworded.** Exactly how the meaning changed has not been determined by machine; the direction of the edit is unknown. Read this as a clarification, not as confirmation that the guarantee has been strengthened.

- **A boundary of what is proven has been added: the archive section of closed threads is never cleaned automatically.** This is a limit, not an achievement: the owner did not permit deleting records without proof of merge, so the section grows — by one record for every thread closed with a content edit during rebase. The first such case has been recorded: close-rebase-once, 21.09.

**Updated:** 2026-09-21

**Updated:** 2026-09-21


## Why it exists

When you think about backup, the first picture in your head is a file sitting somewhere, so everything is fine. This system has convinced itself more than once that the picture is misleading.

The first lesson came at night: the backup script ran faithfully, faithfully copied something — and failed with an access error on 35 out of 36 nights, because it was looking in the wrong place. The backup "existed" in exactly the sense in which it did not exist.

The second lesson was of a different kind. Code was also being copied — committed directly to the main branch and pushed to the server immediately. This meant that every night, unreviewed, unfinished work was quietly deployed into the live system, bypassing all checks.

The third lesson turned up through measurement: it turned out that work in threads — small, separate task streams — existed in a single copy, on a single disk, from the first commit until merge. Almost a thousand lines, more than a day of work, in one copy only. Nobody was making a copy of threads at all: the script would leave without reaching them.

The subsystem exists so that each of these three failures cannot happen again silently.

## What it does, in plain words

There are three separate promises here, and it is important not to confuse them with one another.

**The database is copied where it lives.** The database is stored on Studio — and it is copied there. Not from another machine over a network path, not through a sync folder — right in place. After each cycle, a sensor checks that a fresh copy of each tenant has actually appeared.

**The code snapshot does not touch the main branch.** When the system takes a snapshot of the MacBook file tree, the snapshot goes to a separate place on Studio — away from main, somewhere it cannot accidentally be deployed. The main branch does not move; no deployment happens.

**Thread work exists on two disks.** Every three hours, thread branches are sent as a copy to Studio — into a dedicated quiet section that does not interfere with the file tree. The sensor checks not what the sender reported as success, but whether the object physically exists on Studio. The copy is not deleted until it is proven that the content has reached main: either the commit has become an ancestor, or its content is already there. If a thread was closed with a complex conflict resolution and this cannot be proven — the copy is not deleted but moved to the archive section. The data remains.

All three cases share one thing: a copy on a second machine, not a second copy on the same disk.

## What to say honestly about its limits

Each of the three promises holds — and each has a boundary that cannot be hidden in fine print.

**On the database.** The sensor judges the freshness of the backup file — whether it appeared on time. It does not check whether a restore from it is possible. Integrity verification is a manual step that the machine does not perform. And further: the database and all its daily copies reside on a single disk of a single machine. There is no offsite — this is a decision made by the owner, consciously.

**On the code snapshot.** The sensor checks that the required form is present in the configuration. That the script actually behaves correctly in practice is guarded by a separate test that runs the real script against the real repository and checks whether the main branch has moved.

**On threads.** Several honest things at once.

The loss window is not zero. Work committed to a thread right now will receive a copy only on the next cycle. If the disk dies within those three hours — that work exists nowhere.

The sensor does not fire immediately — there is an intentional delay. Threads live on average for about twelve minutes: if the sensor checked more frequently, it would fire on every healthy thread that had not yet had time to send a copy. This is a trade-off, not an oversight.

The second machine is in the same building. This is protection against disk failure, not against fire.

Uncommitted content in the thread tree is not copied. This is measured and known: at the time of the check, uncommitted content was zero lines, and the snapshot honestly reports which trees it did not cover.

The archive section of closed threads is never cleaned automatically. Deleting what is unproven is not permitted.

## Where this lives in the system

Everything described here lives in `backup.sh` — a single script that behaves differently depending on what it is copying and where. The `--wip` mode handles thread snapshots. The subsystem's intentions and what it promises are recorded in `subsystem_intent.yaml`. The sensors are separate checks that look at the result rather than taking the script's word for it.
