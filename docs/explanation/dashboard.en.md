<!-- translation-of: docs/explanation/dashboard.md sha256:5eec149790ba -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](dashboard.md)

# Dashboard: a read-only view into the DB from outside the tailnet perimeter — why a second entry point exists

## What changed

- **Intent revised:** the subtitle changed from "a look at how it works" to "why a second entry point exists" — the frame shifted: the page now explains the dashboard through the motivation for its existence rather than through a description of its internals.
- **write_guard_primary_only — wording clarified:** what changed is how the split-brain protection mechanism is described and exactly what is blocked when the process starts on a non-primary machine. The direction of the clarification was not determined programmatically.

**Updated:** 2026-09-26

**Updated:** 2026-09-26


## Why it exists

When an entire medical history lives in a single database, a simple question eventually arises: how do you look at it without opening the primary tool that carries full write access?

The dashboard is exactly that second entry point. Not a replacement for the main editor, but a read-only view: a convenient way to see health status at any moment, from any device on the home network. It makes no claim to being the primary interface — it reads what is already there and presents it in a comprehensible form.

There is a second reason as well. If a viewing tool is allowed to write anywhere and at any time, it can silently corrupt data — especially when several devices are synchronising through the cloud. The dashboard is designed to prevent that from happening.

## What it does, in plain terms

The dashboard reads the same database that the primary system maintains. Not a copy, not its own version — the one and only instance. As a result, what you see on screen always matches what is actually there: not yesterday's snapshot, but a live state.

Beyond reading, the dashboard can make targeted edits — for example, correcting a record directly in the interface. Those edits go through the same shared database access layer and leave a trace in the log.

There is one important characteristic in the way it is built: the dashboard will not start in write mode if it finds itself on a non-primary machine. This is intentional. If it could write from any device while data is being synchronised through the cloud, two devices could begin making contradictory edits independently of each other — and the database would cease to be a single source of truth. To rule out that situation, the dashboard checks at startup where it is running, and if it is not the primary machine it stops immediately, not silently.

The dashboard is accessible only from the private home network built on tailscale. It does not face the internet and is not reachable at a public address. The network perimeter takes the place of a password.

## Honest things to say about its limits

This needs to be stated plainly, without softening.

**The dashboard has no authentication.** Anyone who is inside the same tailscale mesh can access it — without a username or password. For a household network where all devices are known and trusted, this is a deliberate decision. But applied to medical data it is an assumption, not a guarantee: whoever gets into the mesh sees everything. This question remains open; it is not resolved.

This is not a technical oversight that was forgotten — it is an accepted risk. But calling it protection would be dishonest.

The split-brain protection — the "stop on startup if not on the primary machine" mechanism described above — works and holds. The single database as the sole source of truth also holds. But "holds" and "verified under all conceivable conditions" are different claims, and the second one is not made here.

## Where this lives in the system

The dashboard lives in `dashboard.py`. The intentions and boundaries of the subsystem are described in `subsystem_intent.yaml` — the invariants are recorded there as well, including the open question around authentication. This is not a third-party service or a standalone application: logically the dashboard is part of the same system, simply with a different role and a different access mode.
