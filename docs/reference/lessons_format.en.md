<!-- translation-of: docs/reference/lessons_format.md sha256:db41622ad9c6 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](lessons_format.md)

# Format of the Lessons Home File (`lessons.yaml`)

One engine — `project_context/lessons.py` in health_scripts. Each project has its own
`lessons.yaml` in the root: health_scripts and two neighbouring projects. The letitbe and the-end
rituals call one command from the root of any of them.

## Commands

| Command | What it does |
|---|---|
| `python3 -m project_context lessons` | shape check; exit code 1 and a list of discrepancies, 2 — no home file |
| `… lessons --subject <key>` | what to deliver at the start of work on a subject (statuses from `deliver_statuses`) |
| `… lessons --subjects` | dictionary of the project's subject keys |
| `… lessons --modules a,b` | lessons touching the given modules, any status (this is how preflight prints them) |
| `… lessons --groups` | summary by (address, subject); a group of 3 witnesses is ripe |
| `… lessons --selftest` | injected breakages must turn red |

The engine lives in health_scripts: from another project — `PYTHONPATH=~/health_scripts`,
on MacBook the interpreter is `/opt/homebrew/bin/python3.11`. Root is the git root of the current
directory or `--root <path>`.

## File Header

| Key | Meaning |
|---|---|
| `address_kinds` | closed list of address kinds ("what exactly to change") |
| `subjects` | dictionary of subject keys; key — lowercase Latin characters; grows only here |
| `homes` | optional: address kind → `{file, section}`; a lesson's `ref` is looked up BY KEY in this home; `file` — a flat `*.yaml` filename in the root |
| `deliver_statuses` | optional: which statuses `--subject` delivers; default `[adopted, failed]` |
| `adoption_dates` | optional: `required` — every adopted entry must have `adopted_on` |

## Lesson Record

Required fields: `id`, `date`, `thread`, `event` (what happened), `cost` (what it cost),
`cause` (cause in the system, not in the person), `address` (`{kind, ref}`), `subject`
(keys from the dictionary), `outcome` (`{kind: rule|check|none, text}`), `status`, `evidence`, `owner`.

- `status`: observation · in_work · adopted · failed · confirmed. adopted/failed/confirmed
  require `change` — a link to the change that was made.
- `adopted_on`: adoption date `YYYY-MM-DD`. A lesson in the same group (address + subject key)
  recorded AFTER this date turns the guard red: "the rule did not fire" — set failed on the
  adopted lessons in the group. The same day does not count as a repeat.
- `evidence`: a path in the repository or `git:<sha>` — the commit carrying the trace.
- `modules`: optional; list of modules for which preflight prints the lesson.

The guard turns red on a missing field, an address outside the list, a key outside the dictionary,
an outcome outside the three, a cause reduced to the person, adopted without `change`, a
non-existent piece of evidence, a duplicate id, a home that cannot be read, an adopted home record
not reviewed after the lesson, adopted without `adopted_on` (when `adoption_dates: required`), and
a repeat after adoption. It does not check the honesty of the cause — that is a human judgement on
a sample.

## Origin of the Format

Grew in a neighbouring project (22–23.09.2026). On 28.09 the engine
became shared, and the corpus of false paths (C-01…C-86) moved into this home in health_scripts.
