<!-- translation-of: docs/how-to/disposability_gate.md sha256:0f07895387f9 -->
**English** · [Русский](disposability_gate.md)

# How to pass the disposability gate

*Document type: how-to. When to read: a commit is blocked with the message “disposability gate (§15),”
or you are about to create a new `.py` and want to get it right from the start.*

The rule is in `CLAUDE.md §15`. Why it exists: `docs/explanation/disposable_modules_refactor.md`.
Only actions here.

> **The gate judges the INDEX, not the working tree.** Anything unstaged does not exist for it.
> The most common reason for an unexpected block: the module is staged, while the sidecar and test
> sit next to it untracked. `git add` them too.

---

## Immediately, if the commit is blocked

```
python3.11 -m project_context disposability <module-key>
```

The module key is the relative path without `.py`: `brief_cards`, `project_context/dispgate`.
The command prints the six checklist items and, if there is no sidecar yet, a ready-made skeleton: paste it
into `contracts/<ключ-модуля>.json`, fill it out, and retry the commit.

---

## What exactly the gate requires

The gate looks ONLY at new files (`git status` = `A`). It does not judge modified modules.
A leading underscore in the name exempts you from nothing: `_helper.py` is judged like any other.
The only exception is `__init__.py` **without function or class definitions**: a re-export boundary
has neither a surface to declare nor behavior to characterize. Add a `def`
or `class`, and it is judged like an ordinary module.

**1. Sidecar contract `contracts/<ключ>.json`.** Fields: `module` (the same key), `public` (a list of
public module functions), `depends_on` (whose contracts you rely on and which names you use),
`disposability` (the verdict block).

**1a. Every `depends_on` entry must resolve.** Either `contracts/<модуль>.json` exists,
or the entry carries `"legacy": "<почему контракта пока нет>"`. An empty list is a valid answer;
a missing field is not. The `legacy` marker does not exempt you from declaring a dependency: it requires
you to state that there is no contract and why, so someone else's debt does not silently block your work.

```json
"depends_on": [
  {"module": "project_context/indexer", "uses": ["build"], "why": "code index"},
  {"module": "health_db", "uses": ["get_conn"], "why": "DB", "legacy": "contract in the owner's audit"}
]
```

**2. The declared surface matches the actual one.** Every public function without `_` must
be in `public`, and vice versa. If you do not want to declare it, rename it to `_private`.

**3. The verdict block is filled out.** Required: `verdict`, `rationale`, `date`, `oracle`, and an answer for
every item in `items.D1…D6`. An empty string does not count as an answer.

**4. A test that imports the module and calls its public name with a BOUND call.** One file
under `tests/` is enough. Any import form works: `import mod`, `import pkg.mod as m`,
`from pkg import mod`, `from pkg import other, mod`, `from pkg.mod import fn`. The call is found through the AST
and must be bound to your import: `mod.fn(...)` or `fn(...)` for a direct function import.
These do NOT count: a call in a comment, a call inside a string, a method with the same name on another object,
`getattr(mod, "fn")()`. A test that cannot be parsed does not count at all.

> **What is actually checked here is syntax, not execution.** The gate sees a bound call
> in the test text. It does NOT know whether pytest will collect the file, whether the branch containing the call is reachable,
> whether the name is shadowed by the time of the call, or whether the test has even one assert. A call without a single
> assertion is valid characterization from the gate's perspective and useless in practice.
> There is no point strengthening the detector: an empty test will pass any binding check. A person is responsible for
> the quality of the characterization; the gate is responsible only for its presence.

**5. No access to someone else's `_private`.** Neither `from health_db import _validate_db_path` nor
`db._PRIMARY_HOST`. Siblings within one package are not someone else's: `project_context/dispgate` may
call `indexer._manifest`, because a package = one disposable unit.

Size (public functions, lines) only produces a warning. It does not block the commit.

---

## If the module honestly is NOT disposable

This happens, and it is a valid answer. The gate blocks a missing judgment, not a negative one.

```json
"disposability": {
  "verdict": "not_disposable",
  "cause": "shared_table_dumping_ground",
  "items": {"D1": "…", "D6": "…"},
  "rationale": "why it can't be done otherwise and what the cost would be",
  "date": "2026-07-26",
  "oracle": "agent"
}
```

`cause` is required and must be a **stable key**, not free text: it is used to count
recurrences. If the same cause appears in three different calendar weeks, it stops being
an engineering detail and becomes an architecture question. Then:

- a line about the cause appears in `python3.11 -m project_context preflight <подсистема>`; the person
  who will work in that area sees it;
- nightly triage reports it to the owner once per run.

The signal repeats until the cause is addressed. Once you have addressed it, record it in `project_context.json`:

```json
"disposability": {"resolved_causes": {"shared_table_dumping_ground": {"date": "2026-08-10", "verdict": "we live with it because …"}}}
```

After that, the cause stays silent forever unless the marker is removed.

---

## What the gate does NOT prove

A green gate means a judgment has been made and recorded. It does **not** mean the module
can actually be rewritten from scratch. “A test exists and calls a public name” is presence,
not behavior; the machine does not check items D1 (one domain) and D6 (shared invariants extracted into a contract)
at all. If the module still cannot be rewritten after a green gate, that is not a gate failure;
it is something the gate does not measure in the first place.

**Known holes, stated explicitly** (two rounds of external review; left intentionally):

- a malformed verdict date passes and resets the cause recurrence counter;
- `getattr(mod, "_private")` bypasses the check for access to someone else's private names;
- renaming a file does not count as a new module;
- outside a git repository, the gate returns success (the shared policy of the family of three gates);
- a staged **symlink** takes the content outside the index: the name is in the snapshot, the bytes are outside;
- `legacy` requires a nonempty string, not meaningful content: `"потом"` passes;
- K2 proves syntax, not execution (see the note above).

These are reproduced by two probes from the working plans (in the private part of the project). If you rely on the gate
where one of these holes exists, rely on yourself.

The list is intentionally not reduced to zero. The gate increases the likelihood that a judgment has been made and
characterization exists. It is neither supposed to nor able to prove the correctness of an arbitrary program
at pre-commit.

---

## Check manually without waiting for a commit

```
git add <new file>
python3.11 -m project_context dispgate          # prints, blocks nothing
python3.11 -m project_context dispgate --block  # exit 6 if not ready
```

Hook exit codes: 3 — preflight receipt, 4 — duplicate gate and TESTING_CONTRACTS generation,
5 — forward-ref in `integrity_tests`, 6 — this gate.

## If the known-bypass ratchet turns red

`tests/consistency/test_dispgate_known_bypasses.py` compares the list of holes in §15 with what
can actually be reproduced. Red does NOT mean “fix the test.” Read which way they diverged:

- **“CLOSED”** — the hole can no longer be reproduced. Remove it from all three places at once:
  `CLAUDE.md` §15, this file, and `EXPECTED` in the test itself. One place is not enough; they will diverge.
- **“RETURNED / NEW”** — a gate regression. Fix the gate or explicitly name the hole in §15;
  silently adding a tag to `EXPECTED` means lying to the rule set.
- **“the harness did not print a single probe”** — the harness itself broke, not the gate.

The ratchet judges the **working tree**, not the last commit: a disposable snapshot of the tree
is assembled before the run (`tests/conftest.py::git_bearing_src`). An uncommitted gate change therefore
affects the verdict, and the run does not require `.git`: it is the same in `~/health_scripts` and in
staging `scripts/test_on_studio.sh`. One harness run takes ~25–75 seconds.

## Related

- `CLAUDE.md §15` — the rule.
- `docs/explanation/disposable_modules_refactor.md` — why.
- `project_context/disposability.json` — the checklist itself and the provenance of the numbers.
- `docs/how-to/discovery_before_build.md` — the neighboring gate (duplicates by data).
