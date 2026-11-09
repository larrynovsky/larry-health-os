<!-- translation-of: docs/explanation/patient_answer_channel.md sha256:741fbcf4a65a -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](patient_answer_channel.md)

# The "question → answer" channel: the system asks a person and hears the answer: explanation

## What changed

- **Added invariant `assessment_catalog_and_producer_durable` (status: holds).** It is recorded that the question producer has a liveness signal on its input: the system records the fact of the background job being launched, not the number of questions raised. With rare conversations, "raised zero" is normal, and a sensor that counts questions would give a false alarm on a healthy system.

**Updated:** 2026-11-07


## Why it exists

When a doctor prescribes something or makes an agreement with a patient, the system is left with a question: did it happen? Was the test taken? Was the medication started? Did the condition change?

A superficial way to find out is to send a reminder that the person simply closes with a checkmark. But a checkmark says nothing about what actually happened. The task list empties, while the patient's words never reach the doctor. Things look fine from the outside — silence on the inside.

This channel exists precisely so that there is no such silence. Its job is to ask a question in such a way that the only way to close it is a text response, and to deliver that response back into the doctor's context.

## What it does, in plain words

**A question is not a reminder.** Each question has a dedicated carrier: a bot message with a reply address. Without an address, the question is considered not delivered — even if it is marked as sent. The person simply will not see a field to respond.

**A question can only be closed with text.** This rule is built into the database itself: it physically does not allow a question to be moved to the "closed" state while there is no response text. This restriction applies to all three places that can touch tasks at all — the bot, the dashboard, and synchronization with Reminders. A check inside one of them would not protect against the others.

**The answer is returned to the doctor.** The patient's text enters the doctor's context as a separate section, marked explicitly as the person's own words. This matters: the same text may enter the system by a second route — through memory — but the sensor intentionally looks for the answer in that specific section; otherwise the second route would give a false sense that everything was delivered.

**Questions have one home.** During a conversation, the assistant may accumulate several possible questions — they exist as candidates. A candidate becomes a real question only through explicit selection. This prevents an avalanche: the system does not overwhelm the person with everything it noticed.

**Question production is limited by data state.** New questions are not raised while too many undelivered ones remain in the queue. Therefore, the frequency of the background job is not of principal importance — the flow is regulated not by a timer, but by actual load.

**Whether to re-ask a question is decided by memory.** The system looks at the nature of the answer: was it a one-time fact, a stable state, or something that changes quickly? This determines whether asking again is needed. The channel has no calendar of its own.

**Who a question is addressed to is decided by one judge.** The same function evaluates every formulation for both the task extractor and the raising of candidates from memory. If the judge is silent, the question does not pass — the system would rather stay silent than ask something unnecessary.

**Duplicates are filtered after the judge, not before.** When two different candidates are rewritten by the judge into one formulation, they become duplicates at that step. The comparison is strict: an extra question costs one message, a lost question is completely invisible from the outside — this is an asymmetric price.

**Uncertainty is not suppressed.** If the judge is unsure — whether a question is addressed to the patient, whether something happened — it neither stays silent nor acts blindly: the formulation is turned into a substantive question for the person. Silence remains only on a complete judge failure — the system distinguishes between failure and uncertainty.

**A doctor's task is verified by meaning.** When a doctor writes "get a test done," the system checks whether there is already an open or recently closed task for the same thing. A duplicate is not created, but a trace remains in the data — a row marked to show the task is covered by an existing one. This matters: a skip without a trace could not be distinguished from a loss.

**Repeatable questionnaires also live in this channel.** Questionnaires are supplied from a catalog. If a completed questionnaire produced no scores at all — that is not a success, but a failure: the task remains open, the person is told honestly, the system warns. Previously, such an import silently closed the task as completed — there was no result, and no one saw this.

**The question producer has a liveness signal on its input.** The system records the fact of the launch — not how many questions were raised. With rare conversations, "raised zero today" is normal, and a sensor that counts questions would go red on a healthy system.

## What is honest to say about its limits

**Unresolved: who a question is addressed to — not yet conclusively verified.**

This is the one place where the system relies on a language model's judgment, and honesty is mandatory here.

Measurements were conducted: on seven task sets, behavior matched expectations on six out of seven runs; on an expanded sample of 27 formulations — one behavioral discrepancy within a session, two label changes, and two drifts between measurements. A problem with response parsing was found and fixed. The expanded measurements showed improvement.

The status remains open — not because the system breaks, but because the boundary between "addressed to the patient" and "not addressed" sometimes drifts between measurements. Fixing the parsing does not eliminate this drifting boundary.

Practical meaning: the system would rather miss a borderline question than ask a wrong one — an error on the side of caution. But guaranteeing that every borderline formulation will be parsed the same way is not currently possible. The judge's verdict and its reason are stored in the data — this allows discrepancies to be seen after the fact, rather than guessed at.

**What holds, but has not been verified everywhere.**

All other invariants — the prohibition on closing a question without text, the reply address, the return of the answer to the doctor, the single home for questions, deduplication after the judge, a trace from uncertainty — are built on mechanisms that do not depend on the model's judgment: the database, data structure, delivery design. They hold. But "holds" and "verified in every possible scenario" are different claims. The limits of testing are the limits of testing.

## Where this is in the system

The channel's logic is concentrated in **`task_agent.py`**: this is where the judge (`task_agent.addressed_to_patient`) lives, where candidates are raised from memory, and where a task receives or does not receive question status.

The intent of the entire subsystem and its place in the architecture are described in **`subsystem_intent.yaml`** — this is the point where the intent is recorded in words, not code.

The channel is intentionally structured as a mirror of `doctor_in_loop`: there the input is the doctor's response, here it is the patient's response. Both channels close the same loop: the doctor speaks → the system hears → asks the person → the person answers → the answer

The intent of the subsystem as a whole is described in `subsystem_intent.yaml`. This is not a technical specification — rather, a statement of why the channel exists at all: the patient's answer must return to the clinical loop, not settle into a closed checkmark.


## Personal assistant statements (07.11.2026)

A letter from the assistant is an unverified source. `agent_reply.queue_fact_questions`
calls the shared `hai_chat.extract_artifacts` without writing memory; `run_arbiter`
uses this same extractor for dialogue. Statements about the person are taken from the letter
(profile_updates, observations, taking, experiments), but not questions and recommendations.
Names and identifiers pass through `mcp_tools.redact` before the model and before the queue.

`tasks_db.save_task` creates questions with source `assistant_fact:<hash of letter>` and
fingerprint from the letter and the normalized statement. Checking of all statuses and
the limit is performed under a SQLite write transaction. The config_db key
`agent_mail.max_fact_questions_per_letter` sets the limit; the sole default is
in `queue_fact_questions`. A repeat with a different model formulation may be counted as
a different statement, but the overall per-letter limit is preserved. This is textual, not
semantic, deduplication.

The queue `deliver_unsent_question_tasks` delivers the question with ForceReply and saves
the reply address. `task_agent.record_answer` accepts only explicit `да`/`yes` or `нет`/`no`
(case and trailing punctuation do not matter). A `да` answer saves the answer via the existing path:
the memory key is the fingerprint, the source is task:<id>. A `нет` answer closes it as dismissed without
writing to memory. Any other answer leaves the question open. Clinical tables and the profile
are not changed by this path. Refusal is not a medical fact.

The mail job confirms receipt after posting questions: on extraction or write error, the letter
remains received for a retry. Before that confirmation, questions already received may be
waiting in the queue; a retry does not add exact duplicates.
The response system prompt prohibits presenting the letter's statements as confirmed
facts. This is a model instruction, not a proven guarantee of its behavior.

Boundary oracle: `tests/unit/test_agent_reply.py` checks letter → question → answer,
absence of memory before confirmation/on refusal, retries after closing, the limit, and failure
before the mail receipt; LLM and transport are stubbed, data is synthetic.
