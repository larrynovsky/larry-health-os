<!-- translation-of: docs/explanation/patient_answer_channel.md sha256:32ad76aaf958 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](patient_answer_channel.md)

# The "question → answer" channel: the system asks a person and hears the answer: explanation

## What changed

- **The invariant `doctor_tasks_judged_against_what_person_holds` has been clarified (status: holding).** When the duplicate judge does not create a "submit the test" task, an invisible trace row remains in the database — hidden from the person — containing the IDs of the tasks that covered it. Why: on 04.10 one person's skipped entry left no trace (the report was rebuilt outside the bot), and there was nothing to verify the judge's work against.

**Updated:** 2026-10-04


## Why it exists

A doctor ordered a test — and forgot to ask whether it had been done. A person postponed a visit — and forgot to say so. Between two appointments with a doctor there is a gap of weeks, and everything that happens in between stays invisible if no one actively asks.

The system can ask. But asking and hearing are two different things. A checkbox in a to-do list only says "task closed." It does not say what the person actually answered. If a question lives next to a reminder — say, "submit the test" — it can be accidentally swiped away, and the doctor learns nothing. Silence from the outside looks like completion.

This channel is designed so that the person's answer necessarily reaches the doctor — in words, not as the mere fact of a closed row.

## What it does, in plain terms

**The question lives separately.** The system does not attach a question to the same place where actions and reminders are stored. Every question has its own address: a message in the bot with a return address. That is exactly where a person can reply. That is exactly where the reply travels onward from.

**Only text can close a question.** This is not a politeness rule — it is a constraint at the database level. Three different parts of the system can in principle change the state of a task: the bot, the interface, and reminder synchronization. A check inside one of them does not protect against the others. So the guarantee is placed where it cannot be bypassed: the database itself refuses if anyone tries to close a question without an answer.

**A question is considered delivered only when it has a return address, not merely when it has been sent.** A task marked "sent" but with no address for a reply is mute. The person sees it in the list and cannot respond. The system considers such a question undelivered until an address appears.

**The answer reaches the doctor as the person's words.** Not as a technical instruction from the model, not as an anonymous fact — but as a separate section in the doctor's context, framed so it is clear: this was said by the person. And the same text goes into memory — by two paths, so it is not lost.

**Questions are not pulled from thin air.** The system maintains a single place where questions live — the task table. The assistant in a conversation can accumulate candidates, but they remain candidates until they pass explicit selection. This is not a strictness filter but a defense against noise: an extra question costs one message, a missed important one costs silence that is invisible from the outside.

**Who decides that a question is for the person.** The same judge decides this for both paths: when a task is born from a conversation, and when a candidate is raised from memory. Its silence reads as "not for the patient" — the system will sooner stay quiet than send something unnecessary.

**Doubt is resolved with a question, not silence.** If the judge is unsure — whether something happened, whether a symptom appeared — a substantive question is asked instead of a blind action or silence. Silence remains only in the event of a complete judge failure.

**Whether to re-ask a question is decided by memory, not a schedule.** If the person answered something long-term, the system will not ask again a week later. If the answer was about a current state, it may return. This is not based on a clock but on the nature of the answer itself.

**Question production stops on its own.** The system does not multiply questions while unread ones have accumulated in the queue. The ceiling is one — the same delivery budget as the other channels. How many times per day the raising mechanism runs ceases to matter: new ones are not raised until the old ones have been delivered.

**Questionnaires are honest about failure.** If a person has completed a questionnaire but not a single subscale could be scored from the answers — that is a failure, not a success. The system says so honestly, and the task remains open. Previously such a case was closed as completed, and no one saw that there were no scores.

**Traces remain.** When the judge decides a question is not for the patient, or finds a duplicate task — that is not merely a skip but a record with a reason. Only what exits without a trace counts as a loss. The filter's work is visible.

Repetitions of a doctor's request are suppressed only for tests. An example (invented): in July a doctor requested "test A, test B, test C," a week later — "submit test A," and a month after that — "test B." The second and third tasks carry nothing new. The model breaks the new task down into individual tests and for each one names the existing task — open, deferred, or dismissed by the person within the last 90 days — where it already appears. The decision "do not create" is made by code, and only if an existing task was found for every test; a panel as a whole and an individual indicator from it are treated as different requests. A task that was not created remains in the database as an invisible trace row, so that the judge's work can be verified. Actions and questions are not cross-checked this way: on the histories of two people, the model confused a new request with an old one there noticeably more often, and losing such a request is not visible from the outside.

## What is honest to say about its limits

**One place remains open — and that needs to be said plainly.**

The decision "who is this wording addressed to" is made by the model. Checks showed: on selected cases the behavior is stable — out of seven runs, six passed without a single label change. Parsing of the model's response was breaking; the cause was found and fixed: lost verdicts are now re-queried once more. On an expanded set of wordings, behavioral divergence between runs is gone where there was previously one instance.

But the status "verified" has not been lifted: the reason is drift in the boundary between independent measurements. Two label changes and two drifts out of twenty between measurements — that is not a catastrophe, but it is not confidence either. There are phrasings where the person's preference and the system's rule point to different addressees — and the boundary between them is blurry for the model.

This means: the system does everything described above, and does it reliably — but "whether every wording was understood correctly" remains a question with an open answer. The verdict and its reason are saved in the data and are visible on inspection. Only the task text goes to the person in the chat — the machine's judgment does not get there.

## Where this lives in the system

The main file where the logic lives is `task_agent.py`. That is where the judge sits (`task_agent.addressed_to_patient`), where tasks are born from conversations and candidates are raised from memory, and where deduplication runs — after the judge, not before.

The intent of the subsystem as a whole is described in `subsystem_intent.yaml`. This is not a technical specification — it is more a statement of why the channel exists at all: the patient's answer must return to the clinical loop, not settle into a closed checkbox.
