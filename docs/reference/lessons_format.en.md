<!-- translation-of: docs/reference/lessons_format.md sha256:614388e72561 -->
**English** · [Русский](lessons_format.md)

# Format of the learned-lessons home (`lessons.yaml`)

One engine — `project_context/lessons.py` in health_scripts. Each project has its own file
`lessons.yaml` at the root: health_scripts and two neighbour projects. The letitbe and the-end rituals call
the same command from the root of any of them.

## Commands

| Command | What it does |
|---|---|
| `python3 -m project_context lessons` | form check; exit code 1 and a list of discrepancies, 2 — the home does not exist |
| `… lessons --subject <key>` | what to surface at the start of work on a subject (statuses from `deliver_statuses`) |
| `… lessons --subjects` | the project's vocabulary of subject keys |
| `… lessons --modules a,b` | lessons for the touched modules, any status (this is how preflight prints them) |
| `… lessons --groups` | roll-up by (address, subject); a group with 3 pieces of evidence is ripe |
| `… lessons --selftest` | injected breakages must turn red |

The engine lives in health_scripts: from another project — `PYTHONPATH=~/health_scripts`,
on the MacBook the interpreter is `/opt/homebrew/bin/python3.11`. The root is the git root of the current
directory or `--root <path>`.

## File header

| Key | Meaning |
|---|---|
| `address_kinds` | closed list of address kinds («what exactly to change») |
| `subjects` | vocabulary of subject keys; a key is lowercase Latin; it grows only here |
| `homes` | optional: address kind → `{file, section}`; a lesson's `ref` is looked up BY KEY in this home; `file` is a flat `*.yaml` name at the root |
| `deliver_statuses` | optional: which statuses `--subject` surfaces; default `[adopted, failed]` |
| `adoption_dates` | optional: `required` — every adopted lesson must have `adopted_on` |

## Lesson record

Required fields: `id`, `date`, `thread`, `event` (what happened), `cost` (what it cost),
`cause` (a cause in the design, not in the person who did the work), `address` (`{kind, ref}`), `subject`
(keys from the vocabulary), `outcome` (`{kind: rule|check|none, text}`), `status`, `evidence`, `owner`.

- `status`: observation · in_work · adopted · failed · confirmed. adopted/failed/confirmed
  require `change` — a link to the change that was made.
- `adopted_on`: the adoption date `YYYY-MM-DD`. A lesson of the same group (address + subject key)
  recorded AFTER it turns the guard red: «the rule did not work» — mark the adopted lessons of the
  group as failed. The same day does not count as a repeat.
- `evidence`: a path in the repository or `git:<sha>` — a commit that carries the trace.
- `modules`: optional; the list of modules for which preflight prints the lesson.

The guard turns red on a missing field, an address outside the list, a key outside the vocabulary, an outcome outside the three,
a cause reduced to the person, adopted without `change`, nonexistent evidence, a repeated id,
a home that cannot be read, an accepted home record not revisited after the lesson,
adopted without `adopted_on` (with `adoption_dates: required`), and a repeat after adoption.
It does not check whether the cause is honest — that is human judgment on a sample.

## Where the format came from

It grew in a neighbour project (`intent/13-retro-i-uroki.md`, 22–23.09.2026). On 28.09 the engine became shared,
and the corpus of false paths (C-01…C-86) moved into this home in health_scripts.
