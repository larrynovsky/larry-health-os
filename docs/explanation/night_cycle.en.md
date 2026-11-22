<!-- translation-of: docs/explanation/night_cycle.md sha256:44fef435fcbf -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](night_cycle.md)

# Nightly Decision Pipeline: a Finding Gets a Name, a Class, and a Deadline

## What Changed

- **A fifth closing author — "fixed"** (`decision_has_an_author`). A /bug report card can now be closed by a fix from the `bug_fixes.yaml` registry: that is neither the owner's word nor silence, so the author is recorded separately. The code has had it since v0.2.7; the registry caught up. (Written by hand.)

- **A report card is closed by a fix; recurrence is judged by the fault's version** (`bug_card_closed_by_fix`, new invariant, holds). Example: a fault came from v0.2.6, the fix shipped in v0.2.7 — the card is closed; the same fault again from v0.2.6 — the card stays closed, the old version is still running somewhere; from v0.2.7 — it reopens, the fix did not work. Boundary: only reports from v0.2.7 on carry the fault version. (Written by hand.)

**Updated:** 2026-11-09


## Why It Exists

Imagine a message arriving every day: "Pressure problem detected." It came yesterday too. And the day before. But you cannot tell — is this the same problem you already resolved, or a new one? Did you resolve anything at all? Who resolved it?

That is what the pipeline guards against. Without it, a stream of signals is just noise: every new signal looks like a new problem, old resolutions get lost, and after a month it is impossible to reconstruct what was done and by whom.

The pipeline gives each finding a stable identity: a name (one, permanent), a place (a card with a status), a class (who should resolve it), and a deadline (how long the system can wait before it acts on its own — or defers to a human). Without this, findings cannot be managed: what has no name does not exist as a subject of decision.

## What It Does, in Plain Language

Overnight the system collects everything that happened during the day: errors, warnings, and — separately — silence where there should be none (a project with no activity in a long time also becomes a finding).

Each finding is assigned a **name** — one name, for its entire lifecycle, from one place. This is essential: if the name is stored in two places, sooner or later one copy will be closed while the second keeps ringing as if nothing has been resolved.

The finding then receives a **class** — one of three:

- *System fixes itself* — and does so that same night, without owner involvement.
- *Into the digest* — an engineering matter that a person will see once a week as a single line.
- *Owner decision* — the finding lands on the desk: with a question in the owner's own words, a closed list of options, and a cost for each. If any of these three fields is missing, the card does not go to the desk — it goes to the engineering queue with a note. The system does not pretend it has formulated things well when a card is missing fields.

While a card sits on the desk, a call comes once a day: not just "you have N questions," but each question — one line, its own human-readable name, its nearest deadline.

If the owner stays silent for two weeks, the system may close the card by **default** — but only under very strict conditions: there is a specific action option, there is a rollback, there is a date, there is an executor who has already carried out the action, the action is not irreversible, and it does not touch the owner's personal data. If even one condition is not met, the card waits for a human indefinitely. It is important to understand: "default" here is not something that happens on its own. Each card receives the right to close silently explicitly, one by one. The default is — do not close.

Every closed resolution has a recorded **author**: owner, default, thread closure, or system cycle. Without an author, "resolved" a month later looks like the owner's word — and that substitution is the most dangerous of all precisely because it goes unnoticed.

The system cannot promote its own finding class: an autonomous cycle can only downgrade an action to "defer to owner," but can never resolve a medical question on its own. This is not a question of the system's confidence — its confidence is not calibrated. This is a structural constraint: escalation is simply impossible by design.

Four kinds of silent failure are especially dangerous here — and all of them look like silence from the outside, while silence is a normal state in the system:

1. The system resolved a medical question on its own, though it had no right to.
2. A card was closed by default where a default should not have occurred.
3. One finding received two names in two places; one copy was closed — the second keeps calling.
4. A sensor stopped watching but did not say so — and "no errors" became indistinguishable from "I checked nothing."

Against the fourth — there is a separate rule: a sensor cannot be silent out of blindness. If it cannot see its subject, it must say so out loud. The liveness check for the sensor runs before data collection — otherwise the collection itself would make any reading appear "fresh" and conceal the problem.

There is one more case in which a finding does not become an owner question: when the sensor simply cannot judge in that environment — for example, the system is running in a container and cannot see what exists only on the host machine. In that case it says so directly, and the finding goes into the weekly digest rather than onto the desk.

The decision desk does not forget what has been resolved. The number of cards only grows — keys are not deleted. If after a system migration the number of cards is suddenly fewer than what was remembered, the desk has lost its memory: in that case nothing is placed on the desk, and the owner receives one message about this instead of a list of questions.

## What to Honestly Say About Its limits

All of the described promises hold today. But "holds" and "verified everywhere" are different claims.

**Default resolves nothing in practice yet.** The default promise is structured correctly, but the executor registry is empty: as long as it contains not a single executor with a test, default can only record a resolution but not carry it out. This is stated, not hidden.

**The bell is silent without the repair-session mark.** If the mark indicating that a repair session has been provisioned is absent, the bell does not ring at all, including the case where the desk has lost its memory. Cards are preserved in that case, but the owner never learns about them.

**A disappeared finding can mean two different events.** If a finding existed yesterday and is gone today, it may mean the problem was fixed. Or it may mean the check went silent on its own. There are separate liveness sensors against the second case, but there is no full guarantee. In addition, a check that does not run every day removes the card the following day — and the card's age resets on recurrence.

**If both the desk and the database disappear simultaneously, the safeguard is silent.** The system detects memory loss by comparing the desk against the database. If both disappear together, there is nothing to compare, and no alert will fire.

**The system sees thread status but does not verify how it was set.** A thread is considered closed if the registry carries the status "closed." The rule that only the owner closes a thread is maintained by convention — not by code. A thread with no registry entry (for example, a renamed one) is not stripped of its card: removing it would mean guessing.

**A sensor that described its inability to judge in its own words will become a question again.** The system recognizes "cannot check here" by exact text from one place. A sensor that deviates from that text will not be recognized.

**The engineering queue is visible once a week and only as a single line.** It has no separate age watchdog: if the queue grows and the owner does not open the desk, the system does not signal this.

**Nightly repair holds, but with caveats.** Liveness
