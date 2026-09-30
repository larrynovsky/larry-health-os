<!-- translation-of: docs/explanation/onboarding_answers.md sha256:9e9781520811 -->
**English** · [Русский](onboarding_answers.md)

# Onboarding: where each answer goes and why it goes there

> Document type: Explanation (Diátaxis). How to go through it: [redo_onboarding](../how-to/redo_onboarding.md);
> questions: `methodology/instruments/onboarding.json`; profile fields: `methodology/profile_fields.yaml`.

The first point: onboarding is not a questionnaire for someone to process later. Each answer is immediately
written where the code reads it. If an answer is saved to a file with no consumer,
it does not reach the profile or affect the next report. Saving an answer alone
does not prove that the system has taken it into account.

## What goes where

| Question | Destination | Reader |
|---|---|---|
| name, date of birth, sex, height, weight, smoking, fasting labs | `patient_profile` (via `profile_db.apply_stated`, the same validation used for conversation) | doctor's report, reference ranges by sex and age, body mass index in the condition summary |
| home | `system_config` `location.home_lat/lon` | distinguishing home from travel; home determines the report's time zone |
| report time | `system_config` `schedule.morning_brief` | morning report schedule (recalculated immediately) |
| health problems | proposals for the problem list, one per line | the person confirms with `/approve` — only then do they enter the medical record |
| medications and supplements | `medications`, in the person's words | treatment summary, doctor's context |
| what to connect | action tasks with instructions | the person's task list |

## Why problems require confirmation and medications do not

The problem list is a medical record. Only a person's click writes to it: this is how it works for all
sources, including the doctor model (registry entry `problem_list_proposals.owner_gate_kept`).
Onboarding is no exception. Medications are recorded immediately: these are the person's own words,
the model has not parsed them, and there is nothing to distort them.

## What onboarding deliberately does not ask

Diabetes type, allergies, blood type (owner's decision on 23.09): “they will tell us themselves or provide
examination results.” The general rule: a question belongs in onboarding only if its answer
has a reader in the code. An answer that no one will read is worse than not asking — it looks
as though it has been taken into account.

## Boundary

Service tokens (Oura, iPhone Health) are not accepted through chat: a Telegram message lives
on someone else's servers. Onboarding only creates a “connect” task with a link to instructions.
