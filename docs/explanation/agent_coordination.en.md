<!-- translation-of: docs/explanation/agent_coordination.md sha256:efc16655fe96 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](agent_coordination.md)

# Multiple Sessions in One Repository: Their Own Tree, a Delivered Note, a Lock That Outlives the Session: Explanation

## What Changed

The meaning did not change; the page was regenerated.

**Updated:** 2026-09-30


## Why It Exists

In September, what had previously only been assumed became obvious: the health_scripts repository is served by multiple agent sessions simultaneously — different, independent, with no shared communication channel. Over two days, September 12–13, seven different sessions worked there. They have no instrumental way to see each other: a query of "who is here?" returns nothing — this was verified independently from both sides. The only thing all of them share is the repository.

The problem is not that the sessions behaved badly. The problem is that the tools they used were made for a single occupant. When the occupants became seven, three things broke for real, not hypothetically.

**First** — the git index is shared across the entire working tree. When one session does `git add` and another is also working at that moment, the first session's files can end up in the second session's commit. This is exactly what happened: four times on September 12, including commit `c08d216`, where one session's staged files ended up inside another session's commit. This is not carelessness — this is how git works.

**Second** — notes between sessions existed, but were only delivered if the reader happened to think to look for them. On September 13, two sessions wrote the same module — 167 lines of code for the same six lines of database — simply because neither knew about the other. The `docs/handoff/` folder existed, notes were placed there, but no automatic "read this before starting work" ever looked there.

**Third** — one session's settings leaked into shared state. The sandbox identity — the name and address `t <t@t>` — ended up in the real `.git/config`, and 61 commits from September 12 went out signed under a foreign name. This was noticed and fixed the same day, but the remainder was not cleaned up, and no one saw it.

The subsystem exists precisely against these three damages that actually occurred.

## What It Does, In Plain Words

**Each thread gets its own tree.** When a session begins work longer than a single commit, `thread_start.sh` sets up a separate copy of the repository for it — a worktree — with its own branch. That copy has its own git index. Physically separate. Now `git add` from one session simply does not see the other session's files — not because of an agreement, but because they are in different locations.

**A note is a mechanism, not a reminder.** After each commit, a hook looks at all working trees, compares what has appeared since the reader last saw a note, and prints only the delta. A neighbor's note arrives to you automatically — not because you remembered to visit `docs/handoff/`, but because you made a commit. The mechanism writes to the addressee thread's inbox folder, not to a shared file: each thread has its own inbox, and the only writer to a thread's `LATEST.md` file is the thread itself. This eliminates the conflict that, prior to September 16, occurred during thread closure approximately once every six merges.

**The lock outlives the session.** The discipline that one session enforced is not inherited by the next — if it existed only in memory. Git hooks are reinstalled: on every push received they are re-deployed fresh from the repository. Not once by hand, but every time automatically. That way order does not depend on who established it.

**The sandbox identity is visible.** Before signing a commit, the system asks git: what will you actually sign the next commit with right now? Not "what is written in one config file," but the final answer — accounting for environment variables, nested configs, tree-level settings. If the answer resembles a test identity, the sensor turns red.

**Thread closure separates what belongs from what does not.** When a thread finishes work, its closure checks: are there uncommitted changes in the main copy? If they belong to another thread — it refuses. If they are automatically generated files produced by the system itself, or files that a merge would not touch anyway — it does not interfere. The list of "own" machine-generated files is stored in one place; if that place is unavailable — closure also refuses, rather than guessing.

**Each merged thread leaves a line in the log.** When a thread with code closes, the closure itself writes a line to the changelog — from the thread's commit messages, without model involvement. This is a correction of the September history: from September 11 through 23, the log was silent about 50 threads until the records were restored manually.

## What Is Honest to Say About Its Limits

It is important here not to confuse "holds" with "verified everywhere." These are different claims.

---

**The rule "thread in its own tree" is not enforced — and this is a deliberate choice, not an oversight.**

No mechanism prevents a session from working directly in the main copy. An attempt to build such a blocking gate was evaluated by measurement: it would have stopped 60 out of 60 commits in a week. This is not a gate — it is a change to the entire working arrangement, and the cost of that change has not been measured. The signal "a file this session did not touch ended up in the commit" — the one that could serve as an indicator — is not machine-detectable: the commit has no concept of "this session," and the four cases on September 12 were found by hand after the damage. The verification tool built on September 14, when tested on a live feed, gave 10 out of 20 commits outside its field of view and one false positive. It is not included in pre-commit. Debt BL-THREAD-GATE-1 is open and looking for a narrow signal.

---

**The separation of what belongs from what does not during thread closure holds, but is not verified everywhere.**

If both sides independently rewrote the same auto-artifact — that is a genuine content conflict, and closure will refuse on it. This is a separate debt BL-THREAD-ARTIFACT-MERGE-1. A file rename is seen by git as two entries and attributed entirely to "foreign" — erring on the side of refusal.

---

**Note delivery is delivery, not reading.**

That the note was printed — is proven. That it was read — the machine does not know. The "read" marker is stored in the tree's service directory, and if two sessions are working in the same main copy — they clear each other's markers; without distinguishing session identity there is no fix. The note arrives after the commit, not before: reacting before the change is fixed is not possible. The hook does not fire on merge, rebase, or cherry-pick — there the note is not shown and not counted, it survives until the next ordinary commit. All recent threads are shown together — there is no way to distinguish another thread's note from one's own, because there is no session signature in merge commits and commits with `--no-verify`. The atomicity of the marker is proven by the write method, not by a power-cut test — no such test was conducted and none is planned.

---

**
