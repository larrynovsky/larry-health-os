<!-- translation-of: docs/reference/bug_report_fields.md sha256:26b5b44471ea -->
**English** · [Русский](bug_report_fields.md)

# What goes into a fault report

> **Genre (Diátaxis): reference.** The contents of the report sent by the `/bug` command
> (and `python3 -m bug_outbox`). The schema has one home in the code — `bug_report.py`; this page is
> checked against it by `tests/unit/test_bug_report_fields_doc.py`.

No field holds free text: each is an enumeration, a number or a string with a narrow pattern. The
report is not scrubbed — it is assembled from these fields only.

| Field | What it holds | Example (invented) |
|---|---|---|
| `schema` | report form number | `1` |
| `report_id` | a random installation id and the report's sequence number | `3f9c0a7b5e21d4c8:4` |
| `release` | release version; `local` — built yourself | `v0.2.2` |
| `platform` | system and architecture | `linux/aarch64` |
| `category` | the button the person picked | `report_missing` |
| `connected` | whether connected (yes/no); key values are never read | `{"oura": true, ...}` |
| `faults` | journal faults for 7 days: type, code location “file:function”, count, fingerprint. A fault without an exception has type `none` and the location of the function that recorded it | `KeyError`, `labs_db.py:build_lab_history_context`, 3, `7f3a1c` |
| `faults[].detail` | optional: up to 4 tokens of 120 characters, each prefixed with its fault place. Only `run_checks.*` (pytest node id without parameters), `monthly_consilium` / `consilium_roster` (role key + exception class or `unanswered`), `llm_admission` (role + model id + verdict kind). Never journal text | `monthly_consilium:neurology:TimeoutError` |
| `screen` | template names of the bot's latest messages and their time, no text; the `/bug` command's own messages are not included | `07:30 jobs.brief.header` |

`category` values: `report_missing`, `button_silent`, `lab_not_read`, `other`.

`connected` values: `oura`, `apple_health`, `withings`, `genome`, `calendar`, `llm`.

Details come from a separate structured writer field, never from error text. Narrow patterns are checked both before sending and on receipt. Digits are allowed only within model ids; tokens with digits in test names, roles or exception classes, spaces, pytest parameters or an unsupported shape are dropped whole. The report does not promise a complete list of causes. Details are shortened if the report exceeds its size limit. If an old receiver rejects the schema, delivery retries without `detail`, using the same report id.

## What is never sent

The text of the bot's messages and yours, error text (an error can carry a lab value or your words),
the tenant name, paths on your machine, any keys or tokens. The receiver does not store your
computer's address.

## Where it is kept

With the developer, in a separate receiver with no access to other data, for 90 days; then deleted
automatically.
