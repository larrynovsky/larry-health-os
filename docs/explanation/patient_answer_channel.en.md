<!-- translation-of: docs/explanation/patient_answer_channel.md sha256:71b27aa255ca -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](patient_answer_channel.md)

# Question → Answer Channel: The System Asks a Person and Hears the Answer: Explanation

## What Changed

- **Added invariant `doctor_tasks_judged_against_what_person_holds` (status: holding).** The doctor asks for the same tests every week in different words. Now a "take a test" task is not created if every test in it is already present in a task that the person has open or dismissed themselves within the last 90 days.

**Updated:** 2026-10-02


## Why It Exists

The doctor prescribes — and then doesn't know what happened next. Whether the person took the test, bought the medication, whether anything changed in how they feel since the last appointment. This gap is not malice: it's simply that between visits the doctor lives inside their own head, and the patient lives inside theirs.

The system could remind via ordinary tasks: "Take test — ✓". The checkbox is ticked, the task is closed. But a checkbox carries no answer. The doctor sees a closed item, not the person's words — "done", "didn't get around to it", "done, but no result yet". From the outside everything looks completed, even though nothing has been communicated.

The question → answer channel exists to close that gap. Its job is not just to remind, but to return the answer to where it is needed: into the doctor's context and into the system's memory.

## What It Does, in Plain Terms

Imagine the system can send questions via a messenger — and waits for actual text, not a checkbox. The question arrives as a bot message with a return address: the person reads it and replies directly in the chat. The answer travels two ways: into the doctor's context — wrapped as the patient's own words so it doesn't mix with the system's instructions — and into memory, where the history is stored.

Closing a question without text is not possible. This is not an agreement between parts of the system but a hard rule enforced by the database itself: an attempt to close a question without an answer will be rejected at the storage level, regardless of who makes the attempt — the bot, the dashboard, or the reminder sync.

Questions live in one place — the tasks table. The assistant in a conversation can accumulate question candidates, but they become real questions only after an explicit selection step: everything does not get poured out all at once. A question is considered delivered only when it has a return address in the messenger — not merely marked as sent. A question without an address is silent: the person can see it in the list but cannot reply.

Who a given question is addressed to — the patient or someone else — is decided by a single judge. If the judge is silent, the default answer is "not the patient": the system would rather say nothing than ask something unnecessary. When the judge is uncertain, it does not stay silent and does not act blindly — it formulates a substantive question for the person: "did it happen", "have symptoms appeared".

How many questions to ask and how often — the system measures by state, not by schedule: a new question will not go out while the queue has more undelivered questions than the daily limit. Whether to re-ask the same question later is decided by the memory timeline axis: how long a given type of answer is considered to live.

Questionnaires are a separate path. If a person completed a questionnaire but none of its subscales could be scored from their answers, that is not "filled in" — it is a failure. The session receives a failure status, the task stays open, and the person is told honestly.

Duplicate doctor requests are suppressed only for tests. Example (invented): in July the doctor asked for "ferritin, vitamin D, glucose"; a week later — "take ferritin"; a month after that — "vitamin D". The second and third tasks bring nothing new, but previously the person received all three, and a task they had dismissed would reappear at the next review. Now the model breaks a new task down into individual tests and for each one names the existing task where that test already appears; the decision "do not create" is made by the code, and only if a number was found for every test. If the new task contains even one new test, it is created in full. Actions and questions are not checked this way: on the history of two people, the model accepted a new request as equivalent to an existing one 9 times out of 168 (an invented example of such an error: "measure blood pressure in the daytime" taken for an earlier "measure blood pressure at night"), and a loss of such a request is not visible from the outside.

## What Is Honest to Say About Its Limits

The most important open issue is the question of whom a given phrasing is addressed to. This is decided by a language model, and its stability has been measured: across six out of seven runs the label never changed and behavior matched the reference on all cases. The seventh run produced a failure not because of the judgment itself but because of parsing the model's response — the cause was found and fixed. On an expanded set of 27 phrasings there was one behavioral discrepancy within a session and several cases where the label changed between individual measurements.

This is not a catastrophe, but it is not a closed question either. The boundary between "addressed to the patient" and "not addressed" — borderline phrasings — remains a place where the model can behave differently across different runs. An example of such a boundary, arrived at independently: "who should choose the format of the academic report?" — where the person's preference and the system's rule point to different addressees. The status of this invariant is open, and presenting it as resolved would not be honest.

Everything else is holding — but "holding" and "verified under any conditions" are different claims. Measurements were made on specific datasets and in specific scenarios. The real flow of questions and phrasings over time will be broader than any of the measurements conducted.

## Where This Lives in the System

The judge logic is in `task_agent.py`: that is where `addressed_to_patient` lives, and where the verdict and the reasoning behind the judgment are stored — they remain in the data but are not rendered to the person in the messenger. The intent of the subsystem and its place among the other channels are described in `subsystem_intent.yaml` — which records that this channel is the mirror of `doctor_in_loop`, except that the input is the patient's answer rather than the doctor's.
