<!-- translation-of: docs/explanation/night_cycle.md sha256:866bfface5f1 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](night_cycle.md)

# Night Decision Pipeline: a Finding Gets a Name, a Class, and a Deadline

## What Changed

- **The decision desk now remembers what was decided.** The invariant `desk_remembers_what_was_decided` (status=holds) has been added: the total number of cards on the desk (which only ever grows) is stored in a database that travels with the system; every nightly pass is checked against it — if the number of cards has decreased, the desk has lost its memory: the cycle puts nothing new on it, and the bell sends a single line about this instead of a list.

- **"Cannot verify here" is not a question for the owner.** The invariant `cannot_judge_is_not_a_question` (status=holds) has been added: if a sensor cannot see its subject in the current environment, the finding goes into the weekly engineering digest, not to the owner's desk and not into the morning bell. Inapplicability of a check does not produce a card with a question.

**Updated:** 2026-09-30


## Why It Exists

Imagine that every night the system notices something: a test failed, a sensor fired, a project has been silent for a week. But if every such observation lives for only one moment — just a line in a log — then tomorrow the same thing looks new again. The day after, new again. It becomes impossible to tell whether this is an old problem nobody solved or a fresh one. It becomes impossible to say what to do with it. It becomes impossible to ask the owner once and wait for an answer — every day the system will ask again, because it does not remember that it already asked.

The pipeline exists precisely to stop that. It gives each finding a stable identity: one name, one home, one deadline. From that point on, a finding is not a line in a log but a card on a desk. It can be tracked, deferred, closed — and you can know who did that and when.

## What It Does, in Plain Words

At night, while you sleep, the pipeline passes through three kinds of events: something broke and went down, something is quietly warning, and — separately — a project that has had no movement for too long. For each such finding the pipeline does several things in sequence.

**Gives it a name.** A finding has one name and it lives in one place. Not two, not one and a half — one. This matters, because if one thing lives under two names in two places, sooner or later one will be closed while the other keeps ringing as though nothing happened.

**Assigns a class.** The finding receives one of three labels: "mine to fix" — the system will handle it; "into the digest" — this is a technical matter, engineers see it but you do not; "your decision" — you need to choose. The distinction here is structural, not based on how confident the model is. A language model's confidence is not calibrated, and an error in the direction of "I'll handle it myself" on an irreversible action is too costly. Therefore the autonomous cycle never takes on more, only less: if there is any doubt, the card is parked with the owner rather than resolved without them.

**Opens a card.** A card is not merely a note. It reaches the desk only when it has three things: a question in your words, a closed list of options, and a clear cost for each. If any of the three fields is missing, it is an incomplete diagnosis — it goes to the engineering queue with a note and is not placed on your desk.

**Rings once a day.** The bell arrives no more than once per day and names each decision that is waiting in a single plain-language line. Not "14 items on the desk" — but what exactly is there. Technical findings that do not require your choice are not part of this ring: they travel in a separate line once a week.

**Tracks memory.** The decision desk never deletes closed cards. The number of cards only grows, and that number is stored separately. If after the system moves the number of cards is suddenly less than what was stored, the desk has lost its memory. In that case the cycle puts nothing new on the desk, and the bell sends you a single line about this, and only that. This happened once in practice: a migration into a container moved the database, the desk was born empty, and the system returned questions that had already been resolved a month and a half earlier.

**Records authorship.** Every closed card has the author of the decision recorded — you, silence, thread closure, or something else from a short closed list. The system does not accept an author outside this list. This is not bureaucracy: a month later, "resolved" without an author reads as your word — and that is a substitution where it is most costly.

**Sets a deadline.** After two weeks of silence a card may close by default — but only if its full triple is filled in (option, rollback, date), the action is reversible, it does not write to your domain, and there is a specific executor who has already carried out the action before the decision was recorded. Without all of that the card waits for you indefinitely. The right to close silently is granted explicitly, to each card individually. By default, the mechanism named "default" defaults to not closing.

**Knows when it does not know.** If a sensor cannot see its subject in this environment — for example, a container cannot see host tasks — it says so explicitly. Such a finding goes into the weekly digest, not to your desk and not into the morning review. "Cannot verify here" is not a question for you.

**Notices sensor silence.** A sensor cannot go silent undetected: if it is blind, stale, or is tracking less than it declared, that is a warning. And that warning is checked before error collection begins, not after — because after, collection would have overwritten the state and any measurement would look fresh.

## What Is Honest to Say About Its Limits

Several things need to be said here without softening.

---

**Automatic application of fixes is not built.**

There is a decision that under three conditions — the action is reversible, there is an independent verifier, there is a downstream oracle — the system could theoretically apply fixes on its own. But the carrier of this mechanism is not built and is not being built now. The reason is not that it was forgotten: over a month and a half of operation there have been almost no red nights, and there is simply no entry point for such a mechanism. Until one appears, the cycle investigates and parks, and a human applies. If the stream of red nights returns, the system will raise a card with a question about revisiting this. Until then the decision remains declarative.

---

**Silence currently resolves nothing in practice.**

The promise that "silence can close a card" holds structurally, but the registry of executors who actually carry out the action is currently empty. This means silence technically works, but there is nothing to apply it to. The decision "silence = delegation" is effectively suspended until the first executor with a test exists.

---

**Four silent failures that look like silence.**

The system is structurally protected against them, but they are worth acknowledging honestly:

- An autonomous actor takes a decision that the owner should have made. From the outside — silence, card closed.
- A card closes by silence where silence should not have applied. From the outside — silence, question "resolved."
- One finding lives under two names, one was closed, the other is ringing. From the outside — a puzzling ring.
- A sensor went blind, and "no errors" became indistinguishable from "I was not looking at anything." From the outside — silence.

All four look the same. The pipeline is designed so that each of them leaves a trace — but a trace, not a guarantee.

---

**Limits that are verified, but not everywhere.**

A card is removed from the desk when its cause has disappeared from today's check.
