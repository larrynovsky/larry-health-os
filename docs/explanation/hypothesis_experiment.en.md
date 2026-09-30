<!-- translation-of: docs/explanation/hypothesis_experiment.md sha256:24b5253f0f10 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](hypothesis_experiment.md)

# Hypothesis as experiment, not a chat message: explanation

## What changed

- **The invariant status of `drift_birth_defunct` changed from `doc_drift` to `holds`.** The item about a daily hypothesis-generator from drift is no longer counted as an open incompleteness — the status is closed.

- **The wording of claim `drift_birth_defunct` has been refined.** The specific details of the requirement changed; the direction of the edit cannot be determined automatically — it is unknown whether the requirement became stricter, looser, or simply more precise in meaning.

**Updated:** 2026-08-30

**Updated:** 2026-09-27


## Why it exists

Imagine you wrote to a doctor: "Look, something has been off with my blood pressure for three weeks in a row." The doctor replied, you both agreed to watch it. And all of that stayed as lines in a messenger app — among dozens of other messages, reminders, photos of results.

A month later: do you remember exactly what is waiting to be checked? Do you remember how that conversation ended? Most likely — not fully. What matters dissolves into the stream.

The "Hypothesis as experiment" subsystem exists precisely so that does not happen. Medical assumptions are not lines in a chat thread. They require a life outside the conversation: you need to know what is open, what is being tested, what has already received an answer. Chat cannot do that. A structured record can.

## What it does, in plain terms

When the system notices a persistent anomaly — something that repeats long enough not to be random — a hypothesis is born. Not a message, not a margin note. An actual record: with a birth date, a status, a history of what happened to it.

From there, that record lives its own life.

If a hypothesis is confirmed — it does not simply "close" and get forgotten. It produces an observation protocol and continues to wait for the next data: confirmation is not an end, it is the next step. If rejected — the outcome is also recorded, and that too is knowledge.

When you ask "what is currently open and waiting for data" — you receive a list. Not a retelling of a conversation, not an attempt to recall who said what. A list of records, each of which is in a specific state.

So that the same problem does not produce two identical hypotheses at the same time, the system can recognise that a new assumption is essentially the same as one already open. This works with memory of what has already been confirmed or rejected: there is no point reopening what has already received an answer.

Hypotheses are born once a month — at what the system calls a consilium. This is a deliberate choice, not a technical limitation. Daily fluctuations in most metrics are noise, not a reason for a new assumption. The daily "hypothesis-generator from drift" is intentionally disabled. The serious conversation happens once a month, when enough has accumulated to see the real picture.

The result of that consilium — the conclusion — reaches you regardless of whether the dashboard is open at that moment, whether something in the system has restarted, how much time has passed. The evaluation process lives separately and survives any technical interruptions. When it finishes its work, the result comes to you — even if you closed the browser long ago.

Edits and refinements to records are version-safe: history is preserved, nothing is overwritten without a trace.

## What is honest to say about its limits

All declared invariants of the system are currently satisfied — not one is marked as broken or disputed. But "works" and "verified under all conceivable conditions" are different claims, and honesty requires keeping them separate.

Live verification of delivery and process durability was conducted under specific conditions: a particular browser, a particular session, specific restart scenarios. The fact that the system held up under those checks says that it holds — not that it holds everywhere and always.

Semantic recognition of "this is the same hypothesis" works, but the boundary between "the same thing" and "similar but different" is thin in a medical context. The system does this recognition — how accurate it is in edge cases will require further observation.

The decision to generate hypotheses only once a month is a deliberate choice, not a technical limitation. This means that between something important appearing in the data and the start of its formal consideration, time may pass. The system is designed this way intentionally, but it is worth keeping in mind.

## Where this lives in the system

Hypotheses live in the database maintained by `hypotheses_db.py` — that is where statuses, outcomes, version history, and everything that makes a record a record rather than a message are stored.

The intentions and rules of the subsystem — why it is structured the way it is, what counts as normal and what as deviation — are described in `subsystem_intent.yaml`. This is a document about meaning, not about code.

If you want to understand exactly how something is structured technically — go there. If you want to understand why any of this exists at all — you are already reading the right text.
