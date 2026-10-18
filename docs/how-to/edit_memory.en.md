<!-- translation-of: docs/how-to/edit_memory.md sha256:3bfa03f7457c -->
**English** · [Русский](edit_memory.md)

# How to correct memory and add a ban

Health memory is one for every window: the bot, the morning brief, Claude and ChatGPT all read it. If it holds
a wrong record, or you do not want something suggested to you, fix it on the dashboard.

## Remove a wrong record

1. Open the dashboard → **Память** (Memory).
2. Find the record in its section (facts, states, questions…). Each shows where it came from and when.
3. Press **убрать** (remove) and confirm.

The record is switched off, not deleted: no window sees it any more, but it stays in the database with the reason
"removed by the owner on the dashboard". That way the system will not offer the same record again as new.

## Add a ban "don't suggest to me …"

1. **Память** → the **Запреты** (Bans) section at the top of the page.
2. Type what not to suggest, for example "don't suggest running to me", and press **добавить запрет** (add ban).

A ban stays until removed: it goes into the patient brief that the bot and the cloud assistants read.
To lift it, press **убрать** next to it.

Limits: the ban is understood by the model that reads the brief — it is a request to the model, not a filter in code.
Health constitutions and nutrition rules cannot read bans yet.
