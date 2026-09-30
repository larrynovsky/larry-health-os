<!-- translation-of: docs/how-to/answer_a_question.md sha256:f76c532ed640 -->
**English** · [Русский](answer_a_question.md)

# How to answer a question from the system

> Document type: How-to (Diátaxis). Mechanics and rationale are in
> [task_reminders_flow](../explanation/task_reminders_flow.md).

The question arrives in Telegram as a separate message: “❓ Question (task #192): …”.
**Reply to that message** — your phone will offer an input field.

The answer goes to three places at once: the task itself (which closes), the system's memory,
and the context for the next GP report.

If replying is inconvenient: `/done 192 визит перенесли на следующий месяц`.
To defer or decline: `/dismiss 192`.

What you cannot do: close the question with a checkmark in Reminders or a button in the dashboard.
Questions are no longer in Reminders, and the dashboard responds “a question is closed by an answer” —
a question closed with nothing is indistinguishable from an answered one, and the doctor would receive silence
instead of your words.
