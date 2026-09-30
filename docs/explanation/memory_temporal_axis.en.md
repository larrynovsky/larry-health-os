<!-- translation-of: docs/explanation/memory_temporal_axis.md sha256:eb82dc871303 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](memory_temporal_axis.md)

# Memory with a Time Axis: durable / standing / transient: explanation

## Why it exists

The system extracts facts from conversations, but they do not all remain valid for the same length of time. A wholly invented non-medical example: someone first says an exhibition opens on Monday, then moves the opening to Wednesday. If memory keeps both versions as current, it will give contradictory answers. Facts need to be distinguished by whether they endure, remain valid until explicitly superseded, or concern only one day.

The time axis is an attempt to bring order here. Not just to store facts, but to understand how much weight each one carries and for how long.

---

## What it does, in plain terms

Every fact the system learns about you receives a time class. The class is one of three.

**Durable** — things that never change, or almost never change. Genetics, anatomy, blood type. Such a fact does not fade over time: it was true yesterday, it is true today, it will be true in ten years.

**Standing** — things that are true now but may change upon an explicit event. Remission status, a current diagnosis, a treatment plan. Such a fact does not expire simply because a lot of time has passed — it expires only when new information arrives that explicitly supersedes it. This is intentional: losing an important fact is worse than keeping it a little longer than necessary.

**Transient** — things that concern a single moment. A bad night, a one-off blood pressure spike, one anxious day. Such a fact leaves active delivery once its window expires, but it does not disappear — it remains in the database, it simply stops participating in conversation on equal footing with long-term facts.

The important point: the class is not assigned by the model based on its own judgment. It is derived from the structure of the record itself — from how the fact is formatted, whether it has a binding to a specific date or to a relative day. This is a deliberate choice: we tested how much an automatic classifier could be trusted — it turned out to be wrong in roughly half of cases. Structure is more reliable.

---

## What is honest to say about its limits

The time axis is stamped on every record — that part works. But here is what does not yet work as intended: when the system delivers facts to the outside, it does not always force the recipient to look at the time class.

There are queries that return active facts — and that is correct. But there are roughly a dozen modules that read data directly, bypassing the time axis. They receive records and decide for themselves what to do with them. Some of them may not even know that every fact has a class. A complete list of such modules has not been compiled yet.

This means: the label on the record exists, but the system cannot guarantee that everyone who reads facts respects that label. The time class is currently more about how a fact is stored than about how it is used.

This is incompleteness, not a minor bug. It is important to name it honestly.

---

## Where this lives in the system

If you want to look at the internals — here are three entry points.

**`memory_facts_db.py`** — this is where the fact database itself lives, along with the logic for working with records: how they are created, how they are labeled, how they are stored.

**`CLAUDE.md`** — the overall architectural map of the system; the time axis is written into it as one of the principles of how memory is structured.

**`subsystem_intent.yaml`** — this is where intent is recorded: why the subsystem is designed the way it is, which invariants are considered met, and which remain open.
