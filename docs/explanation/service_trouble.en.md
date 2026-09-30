<!-- translation-of: docs/explanation/service_trouble.md sha256:bab3fc5d765f -->
**English** · [Русский](service_trouble.md)

# The “the system is causing a person trouble” hypothesis: explanation

This page was written by hand (2026-08-02). It is NOT generated: the subsystem has not yet
been added to `subsystem_intent.yaml`. Whether to add it is part of the open question about
documentation architecture, and the decision was deliberately deferred, not forgotten.

## Why this exists

An independently invented example: the bot announces that a training export is ready, but its link
opens an empty page. The person writes, "the training report will not open." The bot explains
where to find the button but does not pass the complaint to the operator. The person stops trying.

In this scenario, all internal sensors may remain green: the process is alive,
the loop runs, the database is intact, deployment succeeded. This does not test the recipient's experience.

The failure signal is already in the conversation, but without a separate assessment path
it can remain trapped in a reassuring reply. Explaining the interface does not establish
that the interface works.

## What is actually broken here

Fixing an individual watcher does not close this class of failure.

The problem arises when **all the system's oracles answer its builder's questions** —
is the process alive, is the loop running, is the database fresh? The recipient's question
also needs an answer: "is the system causing me trouble right now?" Sensors of internals
do not provide outcome coverage: a failure through a different mechanism may go unnoticed.

The person is a channel independent of anticipated failure mechanisms.
In the invented example, their complaint exists but is lost in a polite reply.

## Why the judge is separate from the responder

The assessment “is this person complaining about the system?” comes from a SEPARATE model call, not the one
that wrote the reply.

This is §17: a check that follows the same path as the hypothesis cannot detect
distortion — it confirms the model's consistency with itself. The responding
model here is a participant, not a neutral judge: it has already decided that “everything
is fine” and, asked within the same call, will repeat that verdict.

Formally, this is the same quorum intersection condition as in §17: two reads from one
replica produce one independent read, not two.

## Why a ladder instead of a siren

§13: a person is the top rung, not the first.

At medium confidence, the bot **asks the person themselves**: “are you concerned about what
is in the results, or about how the system is behaving?” This is not hypothesis testing but a way
to construct the hypothesis — the person is the best judge of what they are complaining about. Their answer
also becomes a label that we otherwise do not have.

At high confidence or after confirmation — straight to the operator, without a digest
(owner's decision on 2026-08-02).

The bot does not ask the owner questions: the owner is also the operator, so asking them to clarify makes no sense.

## What this mechanism does NOT do

**It does not catch silence.** A person who stops writing is invisible to the system.
In the invented example, giving up on opening the report produces no new signal.
The branch for periodic checks for silence was removed by the owner's decision on
2026-08-02: the owner has direct contact with the second tenant outside the system, and the tenant will tell them in person.

This is a statement about the ENVIRONMENT (§18): true now, with no invalidation mechanism, it will become
false without a single code change. If a tenant without such contact appears, the subsystem will silently
lose its only channel for people who go quiet.

**It does not measure its own misses.** False alarms are visible and counted through outcomes.
Misses are not: only caught cases are visible. This is unavoidable (RST, Oracle Heuristic:
every encoded oracle is a heuristic). There is just one compensation, and it is indirect —
a clarifying question to the person.

## Why its own table instead of clinical hypotheses

`hypotheses_db` and `hypothesis_outcomes` fit structurally: hypothesis, status,
outcome. But their domain is clinical.

Putting “the system is broken” there would mean defining a row's domain by its table name
rather than by a field — exactly what §16 argues against, only in reverse. So the
subsystem has its own table, `service_trouble`, with outcomes `confirmed` / `false_alarm`.

An outcome is mandatory for more than tidiness: without one, the false-alarm rate cannot be calculated,
so the threshold can never be calibrated, and a month later we would have a third sensor
that turns green by agreeing with itself.

## What can break here

The person's text enters the judge's prompt. It is declared there as data, not instructions,
and the judge returns a structure — a boolean, a number, and a short reason — rather than text
that someone will forward. The caller assembles the operator's message from fields,
so no one can dictate content into it (WSTG-BUSL-07: detection ≠ response).

The threshold is policy, not a constant (§9): its home is `config_db`; code has only a loud
fallback.

## Where this lives in the system

`service_trouble.py` — judgment and storage of the verdict.
`hai_chat.judge_service_trouble` — a separate model call.
`bot/helpers._service_trouble_background` — wiring after replying to the person.
`tests/unit/test_service_trouble*.py` — oracles, including a positive control
with an independently invented system notification; it checks routing, not conversation history.
