<!-- translation-of: docs/explanation/multitenancy.md sha256:1bcb7fb4b593 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](multitenancy.md)

# Multi-tenancy: tenant isolation: why data does not leak

## What changed

- **The subsystem intent was changed.** The anchor fixes an intent edit — this is the only confirmed change. The specific wording has been clarified; the direction of drift has not been determined programmatically.

**Updated:** 2026-09-24


## Why it exists

Imagine: a single phone with a single app keeps medical records for two people — say, the device owner and their partner. Convenient, but risky. If the system confuses whose data is whose even once, that is not just a bug. That is a leak of someone else's medical history.

That is exactly what happened once: one part of the system "knew" the owner's secrets and used them by default — even when acting on behalf of the partner. As a result, health hypotheses about the partner were sent to the owner. Not out of malice — simply because nobody required the system to state explicitly who the intended recipient was.

The tenant isolation subsystem exists so that this no longer happens silently.

## What it does, in plain words

Each patient has their own database and their own secrets (for example, tokens for sending messages). These are physically separated. But separating folders is not enough: you also need to ensure that no process reaches into someone else's space "out of habit".

The subsystem rests on several principles.

**Silence is forbidden.** If a process is running in multi-tenant mode but has not stated explicitly whose data it is working with, it will receive nobody's data. It will fail loudly, with an error. That is better than silently taking someone else's data.

**Secrets are closed by default.** If a tenant's secrets folder is not configured, the system will refuse rather than fall back to the owner's secrets. It will not guess, it will not find the nearest match — it will refuse.

**Data origin and access rights are separate questions.** The system can answer the question "whose data is this?" — for example, to determine who owns a record. But that answer never opens access to another person's tokens or secrets. At one point these two questions were mixed together — that is exactly what caused the incident. They are now separated deliberately.

**Message delivery is verified.** Every delivery channel — every bot, every message — takes the recipient from the tenant resolver: the system that knows which chat belongs to which patient. This leak point has already been closed and verified.

**A sensor looks for traces of foreign data.** A dedicated tool periodically scans databases and checks whether a fingerprint of one patient's data has appeared in another patient's database. If contamination is present, the system will report it rather than stay silent.

**A boundary the system does not guard.** If two people work on the same computer, files on that computer are not isolated between them. The partner's bot can read a file the owner sent to their own bot — if the file path is physically accessible. This is a known and deliberate decision: people who share one machine already trust each other at the operating-system level. Tenant isolation protects data and secrets inside the system, but not the computer's file system.

## What to say honestly about its limits

Most of the described mechanisms work and have been verified. But there is one area that remains open.

**The shared weekly digest** is a text that concerns both patients at once. The intent is that it should only be sent after each tenant process has given explicit consent ("pass") through its verdict file. If at least one has not given a verdict, the digest waits. If at least one has blocked it, nobody receives it.

The mechanism is built and has been working since 2026-09-05, but the invariant is left open: tests guard the decision function itself ("send or wait"), and the fact that the bot actually calls it before sending is guarded only by a check for its presence in the code. The rule also names a scenario for revision: with three or more people, one crashed bot will silently delay the digest for everyone. So "open" here means "holds, but not proven end to end", not "not built".

## Where this lives in the system

The isolation logic is concentrated in **`secrets_paths.py`** — that is where the rules live about where to look for each tenant's secrets and what to do if the required path is not configured. This file is what implements the "refuse, don't guess" principle.

The subsystem's intentions as a whole — why it is structured exactly this way — are described in **`subsystem_intent.yaml`**. This is not code but an explanation of intent: why these particular boundaries were chosen and what is considered impermissible.

Context about the file-system boundary — why a bot can read someone else's file and why this is a deliberate choice — lives in the **`document_intake`** discussion.
