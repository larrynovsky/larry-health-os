<!-- translation-of: docs/explanation/task_reminders_flow.md sha256:201240da99de -->
**English** · [Русский](task_reminders_flow.md)

# Tasks and Reminders: where they come from and how they are closed

> **Document type:** Explanation (Diataxis) — explains the full task lifecycle.
> For a reference on the tasks table: [ARCH_SNAPSHOT.md](../../ARCH_SNAPSHOT.md).

---

## Three task sources

1. **GP report** → `task_agent.process_gp_report()` — extracts tasks from the report text
2. **Automatic tasks** → `_auto_create_metric_task()` on emerging_trend or persistent_low
3. **Manual** → the user in Telegram chat, `db.save_task()` directly

---

## Full lifecycle: GP report → macOS Reminders

```
gp_agent generates a report (text)
  └── telegram_bot._send_tasks_from_report(report_text)
        └── task_agent.process_gp_report(report_text, "gp_daily")
              └── extract_tasks_from_report()
                    Claude Haiku + TASK_EXTRACTOR_PROMPT
                    Returns a JSON array:
                    [{ type, priority, content, deadline, reason, fingerprint }]

                    Types: question | lab_test | action | followup
                    Priorities: critical | high | medium | low

                    Fingerprint — the deduplication key:
                    "lab:CEA,CA19.9", "action:BP_week", "lab:CBC"
                    If this fingerprint is already among open tasks → skipped

                    Exclusions (from the prompt):
                    - Abstract advice ("keep up the routine")
                    - Already scheduled appointments

              └── create_reminders_for_tasks(tasks)
                    → create_macos_reminder(task) for each task
                    AppleScript → macOS Reminders, list "Health"

                    Title: emoji + content (❓🧪📋🔔)
                    Body: reason + "\n[task_id:N]"
                    Default deadline:
                      critical: +1 day
                      high:     +3 days
                      medium:   +7 days
                      low:      +14 days
```

---

## The carrier is chosen by what answers the task (2026-09-12)

A physical action is closed by having been done — a checkmark in Reminders can
convey that fact. A question is closed by TEXT, which a checkmark does not carry.
Closing through Reminders or a dashboard button without substantive `resolved_text`
loses the answer. Even saved text is useless if no consumer in the clinical flow
reads it: the next report may ask the same question again.

So `question` (like `assessment`) does not go to Reminders at all —
`task_agent.REMINDER_TYPES_EXCLUDED`. A question goes out as a separate bot message with
`ForceReply`; `tasks.tg_message_id` is the return address by which the reply finds
its task (following `pending_field_reviews`). Delivery is throttled by
`questions.max_per_day` (system_config): the channel must limit the flow of messages.

The SINGLE writer `task_agent.record_answer` receives the answer: it closes the task
(primary — `tasks.resolved_text`), writes a derivative to `memory_facts` with provenance
`task:<id>` and key = the question's fingerprint. Answers enter GP context in the section
`gp_context._build_patient_answers_block`, framed as the patient's words, not
instructions to the model.

Closing without an answer is prohibited by THE DATABASE ITSELF (`health_db.question_answer_gate_ddl`):
the task has three writers, and a check in one would not guard the others.

Asking again is governed by memory's time axis, not the report calendar
(`task_agent.should_ask_again`): `durable` — never ask again,
`standing` — until an explicit change, `transient` — according to `memory_tuning.transient_ttl_days`.

Questions accumulated in conversation (`memory_facts(mem_class='question')`) are
candidates, not a second home: `promote_memory_questions` promotes them in small
batches to `tasks` through the 'only the patient can answer' gate.

## Three ways to close a task

### Path 1 — through Telegram
```
/done <id> [text] → db.resolve_task(id)
                   → task_agent.complete_macos_reminder(id)
                     osascript: finds the reminder with "[task_id:N]" in the body
                     sets completed=true
```

### Path 2 — through macOS Reminders
```
User marks the reminder as completed
  └── LaunchAgent reminders_sync.py (every 3h)
        osascript → completed reminders in "Health"
        regexp: extracts [task_id:N]
        db.resolve_task(task_id)
```

### Exception — a question task (`type='question'`, 2026-09-12)
Paths 1 and 2 do not apply to it. Only an ANSWER closes it:
```
reply to a bot message        → tasks_db.get_task_by_tg_message → record_answer
/done <id> <text>             → record_answer
/done <id> without text       → bot asks for the text
checkmark in Reminders        → reminders_sync skips it (there are no questions there anyway)
dashboard button              → HTTP 409
UPDATE ... status='completed' → RAISE(ABORT) in the database itself
```

### Path 3 — Dismiss
```
/dismiss <id> → db.resolve_task(id, status="dismissed")
The macOS reminder stays (there is no reverse sync)
```

### Exception — a questionnaire task (`type='assessment'`, 2026-09-03)
Closed ONLY by filling it out: bot → “📋 Fill out” (`cb_as:n`) → `assessment_dialog`
→ JSON → `assessment_importer` → an `instrument:*` row in `lab_results` (§16). The dashboard's
`/api/tasks/{id}/done` returns 409: closing a task without completing the questionnaire
does not create a result. An independently invented example: in a training environment,
someone clicks "done" and reloads the page — the task disappears, but there is no assessment record.
Deferring means snooze, which is legitimate. Delivery uses the outbox `jobs.scheduled.deliver_unsent_assessment_tasks`
(5 minutes, `sent_at` stamp by ID): it delivers the "Fill out" keyboard.

---

## Monitoring overdue tasks

```
task_agent.get_weekly_followup()
  db.get_overdue_tasks(days_old=7) → tasks older than 7 days that are not closed
  Sent on Monday 07:00 together with the weekly report
```

---

## Why fingerprint instead of id

GP generates reports daily. Without deduplication, the same task
(“get CEA tested”) would appear every day until completed. A fingerprint
identifies a semantic duplicate regardless of its creation date
or wording in the report.

---

## Why evidence for the judge instead of a “the system knows the answer” filter

A question from memory may concern data already in the database. An independently
invented example: "what values of the training metric Example_A are stored?" —
when Example_A measurements are present in the training dataset.

A filter suggests itself: an analyte is mentioned, its data exists — do not ask the question.
It would be wrong, and here is why. The hypothetical question “did you agree on the dosage of supplement X with your doctor?” also
mentions an analyte, measurements of X also exist, yet only the person has the answer.
A name match does not distinguish a question about a NUMBER from a question about a DECISION.

So a different approach was taken. The rule “questions for data are not for a person” had been in the judge's prompt
from day one. It failed not because the rule was missing but because
the judge did not know whether the system had those measurements. Now it is given a fact:
“the system ALREADY has measurements: <analyte> — N values, latest <date>.” The judge
still gives the verdict. No second judge — a trigger-word list — was added alongside it,
and this is not tidiness but the invariant `single_judge_of_addressing`: two homes for one
judgment diverge silently, and already had in this subsystem.

**The evidence is fail-open, and that matters more than it seems.** Any failure — the name resolver crashes,
lab data cannot be read — produces EMPTY evidence, and the judge decides as before.
This direction was chosen not out of caution in general but because of how this channel fails:
its failure is invisible. A person sees an unnecessary question and can reject it. No one sees
an unasked question. A closed task list does not prove that answers
reached their reader either. Between "asked unnecessarily" and "stayed silent," this subsystem always chooses
the former; the same rule is recorded separately for the judge's uncertainty
(`doubt_resolves_into_a_question`).

The boundary is explicit: evidence is collected only for laboratory analyte names. Sleep
and HRV metrics are not included — their Russian–English mapping still lives privately inside
`monthly_consilium`, and moving it into a public home is separate work, not
an add-on to this task.
