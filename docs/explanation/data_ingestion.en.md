<!-- translation-of: docs/explanation/data_ingestion.md sha256:63200c30621a -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](data_ingestion.md)

# Data Ingestion: Single Canonical Database (single-primary): How the System Decides What to Trust

## Why It Exists

Imagine keeping a health journal in several notebooks at once — on a computer, a tablet, a phone. Sooner or later they will contain different numbers for the same date. Which one do you trust? It's not clear. And if the system automatically picks the "average" or silently shows yesterday's value instead of today's — that is no longer a journal, it is an illusion of order.

Body data arrives from everywhere: from watches, from sensors, from photographs of lab report forms. Without a strict rule about who is allowed to write, it is very easy to end up with several "truths" at once. This subsystem addresses exactly that problem — versions of a single reality that have multiplied out of control.

## What It Does, in Plain Terms

The subsystem is built around one simple principle: **only one machine — Studio, and no other — may write to the database**.

Every other device that has access to the database sees it as read-only. They can view, analyse, and plot charts — but they cannot write anything to it. This is not a technical accident; it is a deliberate choice: one hand writes, the rest only read.

The second important decision concerns the situation when Studio is not nearby. Suppose you open the application on another device and find an old copy of the database there, synced through the cloud a few hours ago. What will the system do? It will **refuse loudly** — report an error — rather than quietly presenting stale data as current. This feels inconvenient, but it is honest: it is better to see "cannot display" than to see yesterday's blood-sugar reading and assume it is today's.

The third point: the database operates in a mode designed for exactly one writer. This is a conscious architectural decision — the system does not pretend that multiple sources can write simultaneously and everything will be fine. It is honestly structured around a single write stream, and therefore does not require the complex maintenance that a separate server would demand.

Importing data from wearable devices and recognising lab results are also part of this same subsystem, but they have their own rules and separate descriptions.

## What to Honestly Say About Its Limits

All three key promises of this subsystem are being met today — there are no known violations and no open questions.

This does not mean the system is all-powerful. It solves a specific problem — preventing silent confusion between versions of data. It does not solve the problem of backup, does not guarantee that data will not be physically lost together with Studio, and is not responsible for what happens before data reaches the database. These questions lie outside this subsystem.

The only way to permit writing from a machine other than Studio is an explicit manual override. This means that in an emergency you have a way out, but it requires a deliberate action rather than triggering by accident on its own.

## Where This Lives in the System

The logic that enforces all three promises lives in **`health_db.py`** — this is the file that, when connecting to the database, checks which machine it is running on and either raises an error or grants write permission.

The overall architectural rationale — why this single-writer-node scheme was chosen at all — is set out in **`CLAUDE.md`**. That document captures the intent of the entire system, and this subsystem appears there as one of the foundational decisions.

The formal description of invariants — exactly what the system promises and their current status — is recorded in **`subsystem_intent.yaml`**. This is the machine-readable "signature" of the subsystem: if anything changes or comes into question, an honest note will appear there first.
