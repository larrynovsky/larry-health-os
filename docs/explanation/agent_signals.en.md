<!-- translation-of: docs/explanation/agent_signals.md sha256:eeff39a4a49a -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](agent_signals.md)

# Actions for a Personal Health-Signal Assistant: Intent Explanation

## What Changed

- **The subsystem intent has been clarified.** A second responsibility is now explicitly stated: synchronising protocols with the assistant. Previously the emphasis was placed only on signal filtering and reminders; it is now recorded that the assistant receives the full, up-to-date list of protocols on every change and returns only execution counters — no interpretation, no right to change statuses.

- **The `protocols_primary_copy` invariant added (status: holds).** Explicitly established: the source of truth for protocols is always the system, not the assistant. The assistant does not keep "its own version" — the current list always comes from the system, including removed items. This is a new promise that now has a name and a status.

- **'Holds' means verified in the intended scenarios, not everywhere.** The *holds* status on the new invariant — as with the others — does not mean coverage of edge cases, non-standard configurations, or partial failures. This is a constraint, not an expansion of guarantees.

**Updated:** 2026-11-10


## Why It Exists

When the body is working normally, the best thing the system can do is stay silent. Not send another "everything is fine", not remind about something it already reminded about yesterday, not create a background of notifications that people start ignoring by day three.

But staying silent *always* is also not an option: sometimes something genuinely deserves attention — it may be worth reconsidering workload, the time has come to get a lab test, an supplement needs to be remembered. This subsystem solves exactly that problem: speak only when there is a reason, and do not repeat without cause.

The second intent is to keep the assistant up to date on current protocols. Not relay them in pieces or trust it to remember old versions: on every change it receives the full, fresh list. In return the assistant reports exactly one thing — how many times each item was completed. It does not interpret, does not change statuses, does not make judgements about health.

## What It Does, in Plain Terms

The subsystem acts as a filter and a courier at the same time.

**As a filter** it looks at health signals and decides whether anything should be sent. If indicators are within normal range — no message. If a signal of the same kind has already been sent today — no repeat. A new signal can only appear if something has genuinely changed — for example, stress that was not present in the morning has arisen.

When there is a reason, the subsystem composes one of several types of message:

- **A prompt to reconsider workload** — not a command, but a signal for reflection.
- **A supplement reminder** — taking into account how many times it has already been sent: repeated noise is not endless.
- **A lab-test reminder** — and honesty matters here: the message shows where the due date came from. If the interval was set by a doctor at an appointment, that is visible. If it was set manually or came from system settings — that is visible too. When the system-derived due date has long passed, a soft note "at the nearest draw" is added — but only in that case.

There is one additional special signal: the sensor notices if the morning full pass was not completed and reports it if a recipient is configured.

**As a courier** the subsystem synchronises protocols. When the protocol list changes, the assistant receives it in full — including items that were removed. If the list is empty from the start, nothing is sent. The assistant does not keep "its own version" of the protocols — the current one always comes from the system.

Feedback from the assistant is intentionally narrow: only execution counters in the established format. Nothing else. Protocol status is changed only by the system, not by the assistant.

All of this operates within a single database: the recipient, data, and attempt log belong to a specific process. If no recipient is configured — the entire path is disabled, no messages go anywhere.

Numerical thresholds, percentiles, and lab-test intervals are not held by the system itself — it reads them from a shared configuration where each such value has its own owner.

## What Is Honest to Say About Its Limits

All stated properties of the subsystem — that normal range stays silent, that there are no repeats, that the origin of the interval is shown, that protocols are sent as a full snapshot — have been verified and hold. This does not mean they have been verified under every possible condition. "Holds" and "verified everywhere" are different claims, and only the former is appropriate here.

More specifically: the subsystem's behaviour was verified in the scenarios that were anticipated during development. Edge cases — unusual combinations of signals, non-standard configurations, behaviour under partial failures — are not declared as fully covered.

There are no invariants that honestly need to be described as "does not hold" or "open": all stated properties have *holds* status at the time of this description.

It is also worth understanding separately what the subsystem **is not**: it does not make diagnoses, does not interpret deviations on its own, and does not replace a doctor. It passes signals and synchronises protocols — nothing more.

## Where This Lives in the System

The signal-filtering logic, message composition, and attempt tracking live in **`agent_signals.py`**. The morning-pass sensor and the protocol-synchronisation mechanism are there as well.

The subsystem's intent — why it is structured this way, what decisions were made and why — is recorded in **`subsystem_intent.yaml`**. This is not a technical config but an intent document: it explains what is considered correct behaviour and what the system promises to uphold.

Together these two files give the complete picture: one says *what* happens, the other — *why*.
