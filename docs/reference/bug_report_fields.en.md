<!-- translation-of: docs/reference/bug_report_fields.md sha256:fd77602de49f -->
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
| `faults` | journal faults for 7 days: type, code location “file:function”, count, fingerprint | `KeyError`, `labs_db.py:build_lab_history_context`, 3, `7f3a1c` |
| `screen` | template names of the bot's latest messages and their time, no text | `07:30 jobs.brief.header` |

`category` values: `report_missing`, `button_silent`, `lab_not_read`, `other`.

`connected` values: `oura`, `apple_health`, `withings`, `genome`, `calendar`, `llm`.

## What is never sent

The text of the bot's messages and yours, error text (an error can carry a lab value or your words),
the tenant name, paths on your machine, any keys or tokens. The receiver does not store your
computer's address.

## Where it is kept

With the developer, in a separate receiver with no access to other data, for 90 days; then deleted
automatically.
