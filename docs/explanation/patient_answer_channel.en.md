<!-- translation-of: docs/explanation/patient_answer_channel.md sha256:30b016a2b099 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](patient_answer_channel.md)

# The "question → answer" channel: the system asks a person and hears the reply: explanation

## What changed

- **The `morning_questions_have_return_address` invariant added (status: holds).** Questions raised by the morning review are now explicitly included in the return-address requirement — on equal footing with all other questions in the channel. A task without an address is considered mute regardless of which path created it.

**Updated:** 2026-09-28


## Why it exists

The doctor and the patient share a symmetric problem. The doctor must hear the system: what it thinks, what it suggests, where it sees risk. The patient must hear the system differently: it asks him a question and waits for an answer. The patient's words must travel back — to the doctor, into memory, into the context of the next conversation.

If a question travels as a reminder that can be ticked off, the content of the answer may be lost along the way. The task disappears from the list even though the doctor has not received the person's words.

This is an invisible failure: successfully closing a task does not prove delivery of an answer. Consumers in the clinical flow must read the answer field.

This channel is built so that such silence becomes visible — and then impossible.

## What it does, in plain words

When the system needs to ask a person something — whether a visit happened, what was agreed with the doctor, what has changed in how they feel — it does not send a reminder. It creates a question with a return address: a bot message that can be replied to with text. A question is considered delivered only when it has that address. A task without an address is mute: the person can see it but cannot reply.

A question can only be closed with text. Not a tick, not a swipe — only a written answer. This is not a request to the user; it is a constraint in the database itself: it physically will not allow a question to transition to a closed state without content. This matters because a task has several places from which it can be closed — and a check in one place does not protect against the others.

When an answer arrives, it comes back as the person's own words — into the doctor's context, in a separate section, framed so it cannot be confused with a system instruction. At the same time it is stored in memory. Two paths, not one: one path can fail once.

Questions live in one place — the task table. The assistant can accumulate candidates during a conversation, but they remain candidates until they pass an explicit selection step. One judge decides whether a question is addressed to this particular person. If the judge is silent — the question is not asked. The judge's silence reads as "do not ask," not as "ask everything."

Question production is limited not by a schedule but by the state of the data: new questions are not raised until the queue of undelivered ones has grown shorter. This means the scheduler's run frequency stops mattering — the system will not ask more than it can deliver.

If the judge is uncertain — it neither stays silent nor acts blindly. It asks a substantive question: "did the visit happen," "have symptoms appeared." Silence remains only on a full refusal from the judge — uncertainty and refusal are different things.

When a question has been formulated, the judge receives one piece of information before deciding: whether measurements related to the subject of the question exist in the database. If the system already knows about a test result — the judge knows that and can decide whether to ask again. This is not an automatic filter; it is a clue: the judge issues the verdict itself. If the fact could not be retrieved — the question is more likely asked than dropped.

Whether to ask a question again is decided by memory. Every answer has a class: some things change quickly, some hold for months, some are permanent. That axis determines when to ask again.

There is one more path for questions — the morning review: the system notices that tests have not been submitted for a long time or that a monitoring period has expired. These questions also go through the shared task home and are delivered with a return address. A question is not duplicated on top of an already-open "submit the test" task. Things that belong to someone else in the database — a different person — are not asked.

Questionnaires are a separate path. If a person completed a questionnaire but none of the subscales could be scored from the answers, that does not count as a success. Previously such an import would close the session and the task as completed: there were no scores, and nobody saw that. Now it is a failure: the task stays open, and the person is told honestly.

## What to say honestly about its limits

There is one place where the system cannot offer guarantees, and it is important to say so directly.

**Who a question is addressed to is decided by a model.** This is a judgment, not a hard rule in code. Stability was measured on seven hand-picked cases and a corpus of twenty-seven real phrasings. Most were judged stably, but one out of twenty-seven showed divergence within a session, and two out of twenty changed their label between measurements. These are model-evaluation results; personal question content and chronology are not reproduced here. An independently invented boundary example: "who should choose the format of the training report?" — the addressee depends on whether the question concerns a person's preference or a system rule.

This means: the boundary between "question for the patient" and "question for the doctor" drifts slightly on live input. Not catastrophically, but measurably. The status of this is open, not a solved problem.

The following has been done about this drift: every judge verdict is now saved — not discarded as before — so "then" can be compared with "now." Dropped candidates are recorded with a reason. A repeated review catches cases where the answer could not be read at all. But the drift of the boundary itself — is open.

This does not mean the system asks the wrong person constantly. It means: a small number of phrasings on the boundary between meanings may be evaluated differently on different days. We know this and continue to measure.

Everything else — the guard against closing without text, the return address, delivery of the answer to the doctor, the single question home, deduplication, the judge's behavior under uncertainty — holds. But "holds" and "verified under any conditions" are different claims. Verified on real use of the system at its current scale.

## Where this lives in the system

The channel's logic lives in **`task_agent.py`**: that is where the judge lives, where candidates from memory arrive, and where it is decided whether a question becomes a task. The subsystem's intent and its place in the overall architecture are described in **`subsystem_intent.yaml`** — there you can also find which other parts of the system this channel borders and what is expected of it.
