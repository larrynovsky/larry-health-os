<!-- translation-of: docs/explanation/test_architecture.md sha256:59afb8d2b269 -->
**English** · [Русский](test_architecture.md)

# Test architecture

> **Document type:** Explanation (Diataxis) — explains **why** the test
> system is built this way. For **what exists**, see `TEST_ARCHITECTURE.md`,
> `USE_CASES.md`, `uc_index.yaml`. For **how to do it**, see
> `docs/how-to/run_tests.md`, `add_new_uc.md`, `handle_test_failure.md`.

---

## Idea

Health OS makes medical claims. Tests must catch **regressions in
the correctness of those claims**, not just “the code does not crash.” This changes everything
relative to a standard pytest framework.

## Three key principles

### 1. Spec-by-example: `USE_CASES.md` is the single source of truth

The UC catalog describes **desired behavior**, not current behavior. A test against
an `intended` UC is an `expected_gap`, not a “broken test.” A test against
an `implemented` UC is a regression signal.

The `status` × `confirmation` lifecycle distinguishes:

| status | confirmation | a failing test means |
|---|---|---|
| `implemented` | `confirmed` | REGRESSION → CRITICAL → TG alert + Reminder |
| `partial` (gap portion) | `confirmed` | expected_gap → INFO |
| `intended` | `confirmed` | expected_gap → INFO (code has not yet caught up with intent) |
| any | `proposed`/`rejected` | the test should not exist (UC-J-02) |

Without `confirmation = confirmed`, `generate_test.py` refuses to generate
a skeleton — this is disciplined development.

### 2. The automation boundary: B+E+C+D, plus H for quality

Not all medical quality can be automated. A four-level strategy:

- **B (negative invariants)** — what the system must NOT do. The main
  medical invariant: “if NULL, make no claim.”
- **E (cross-check against deterministic truth)** — numbers in the text = numbers
  in the database (not invented).
- **C (snapshot)** — for critical JSON structures. **Rejected** for LLM output
  as an illusion of protection (does not distinguish “code regression” from “LLM instability”).
- **D (LLM judge)** — a separate Claude Haiku with explicit questions. Not a source
  of truth. Never the sole oracle for P0.

For qualitative claims (UC-I-08 oncology context, UC-I-09 genome as a framework,
UC-H-03 Round B deliberation), use `H` (Human charter): a risk-oriented
review path. This is markdown in `tests/charters/`, not code. `assert "хороший
врач"` does not exist.

### 3. Health OS's distributed properties require a separate test layer

The standard test pyramid ignores the fact that the project has:

- **Conit + staleness deviation** (TC §1) — data older than 26 hours/30 days
  must trigger an alert. Testing this requires **time substitution**, otherwise
  it cannot be implemented. `_time_inject.py` solves this centrally.
- **Tentative writes** — `pending_labs/` is a separately observable object
  BEFORE approval. Test the tentative → committed transition.
- **Single-primary** — writes to `health.db` only from Studio. The guard in
  `health_db.get_conn()` returns a read-only connection outside Studio.
- **Read-your-writes** — evening check-in → GP sees it in the morning.
- **Causal consistency** — Council Round B depends on Round A.
- **Eventual consistency** — Tasks ↔ Reminders sync window of 3 hours.

These have a separate layer, `tests/consistency/` (neither unit nor integration —
specifically about distributed properties).

---

## The seven-layer pyramid

```
                    Charter (H)            5 pcs  ← markdown, not code
                  Snapshot (C)            ~1 pc   ← minimal
              LLM-judge (D)               2 pcs   ← skipped without an API key
          Consistency-specific           21 pcs   ← RYW/tentative/staleness/...
        E2E_mock                          5 pcs
      Integration                        14 tests
    Unit                                 ~110 tests  ← foundation
```

Layers 1–4 run on every commit through `pytest -m "not slow"`.
Layer 5 (consistency) is also fast and belongs in the main suite.
Layer 6 (LLM judge) runs only nightly and requires the Anthropic API.
Layer 7 (charter) is not code; it follows a calendar.

---

## Architectural decisions that needed justification

### Why `_time_inject.py` instead of a `clock` parameter in every function

The initial plan was “an optional `clock` parameter in important functions.”
An audit found that 40+ files use `date.today()` /
`datetime.now()`. A parameter in every function adds a lot of ceremony to agent code,
and it is easy to forget to pass it in one place.

`_time_inject.get_now()` / `get_today()` provide global substitution through
`set_test_clock()`. The test changes `_TEST_CLOCK`; production sees real time.
Tests reset it automatically through an autouse fixture in `conftest.py`.

CLI blocks `if __name__ == "__main__":` are deliberately untouched — the test calls
the function directly with fixed dates, bypassing the CLI.

### Why snapshots were rejected for LLM output

A snapshot catches divergence from a reference. But for an LLM, divergence can come
from (a) code regression, (b) model instability, or (c) normal
generation variability. A snapshot cannot distinguish them.

Replacement for UC-A-01 (`lab_extractor`): self-consistency — two runs on
the same PDF; key fields `{name, value, unit, flagged}` must match
bit for bit. Divergence = extractor instability (actionable: the prompt needs
refactoring or temperature=0). This avoids hand-to-hand combat with
references on every change.

### Why test_failure_handler does not repair code itself (Repair narrowed; see amendment below)

Fully automatic Claude patching of production code in a medical system means
a risk of silently weakened assertions. In agent mode, Claude sometimes “fixes”
a test by weakening the check rather than fixing the logic. The cost of a silent
regression in the correctness of medical conclusions exceeds any time savings.

The accepted model: **diagnose, don't repair**.
- Level A — deterministic retries based on log patterns.
- Level B — Haiku writes a diagnosis in markdown; a person reads it in the morning.
- A Reminder for 10:00 with a prompt ready to copy and paste — your idea, and it proved
  sound. In the morning: open Reminder → copy → Claude helps investigate.

**Owner's amendment on 2026-08-03 (thread night-cycle): the prohibition was NARROWED, not removed.**
Automatic application of a fix for a red result is allowed only when all three conditions hold:
(1) the action is reversible (`git revert`, not deleting rows or migrating data);
(2) the patch passed an independent reviewer agent (§17 — a prompt aimed at refutation,
without the author's explanations); (3) there is a DOWNSTREAM oracle that turns red — nightly
integrity catches a failure CAUSED BY the fix and parks it again. Everything else remains
“diagnose, don't repair.” The normative text and the cost knowingly accepted by the owner
(a window of up to a day between application and nightly detection) are in the rule set, `CLAUDE.md §13`;
this is a pointer so the page is not read as a current blanket prohibition.
The mechanism (`fix_applier`) has not been built: until it is, the amendment is declarative.

### Why severity depends on UC status, not test type

“Test failed → CRITICAL” creates noise. 70% of tests could target
`intended` UCs, making every nightly run CRITICAL.

“Test failed → CRITICAL depends on UC status” separates regressions from
expected_gap. If UC-A-01 has status=`partial` (automatic Council startup
is deferred to the backlog), a test of that part of the pipeline is an `expected_gap`, not noise.

### Why UC-J-02 prohibits generating a test for an unconfirmed UC

Without this process, an agent could write a test for its own interpretation of a UC
instead of clarifying with the user exactly what is being tested.
`confirmation = confirmed` is an explicit gate: “yes, this is the behavior
we are testing.”

### Why `sys.modules.pop`/reload of shared modules is prohibited in tests (2026-06-22)

Dozens of modules import `health_db` (`treatment_*`, `survivorship`, etc.).
If a fixture does `sys.modules.pop("health_db")` + reimport, it creates a NEW
module object. Modules that imported the old one keep v1, while the fixture/test uses
v2 → **split-brain between two health_db objects**: writes go to one database, reads to another.
The symptom is deceptive — the test passes alone and fails only in the full run
(`pytest tests/`), in UNRELATED files, and depends on collection order.
This is how the whole `tests/` silently stayed red (22 failed) while subsuites were green.

Rules:
- substitute the database through `monkeypatch.setattr(health_db, "DB_PATH", tmp)` (the same
  object, automatic restore), as in `tests/fixtures/db.py`. No pop+reimport.
- if pop is unavoidable (testing an import-time guard), save the ORIGINAL object and
  restore that exact object in `finally`, not a fresh import. `importlib.reload(x)` is gentler —
  it re-executes in place, preserving identity.
- diagnose order-dependent failures by bisection (suspected file BEFORE
  the victim), not by the intuition that “the modules are unrelated.”

⚠️ `run_checks.sh` (post-commit) runs smoke_tests + integrity_tests, NOT pytest —
this class of bugs is caught only by a manual/CI full `pytest tests/` run.

In a medical system, the cost of “a test written for the wrong behavior” is that a regression
is not caught → bad conclusions reach the user.

---

## Contract tests for public APIs (pattern validated 2026-05-10)

A pattern that proved valuable after the 2026-05-10 regression:

**Idea**. If a public function in `health_db.py` (or another core module) has required parameters, it has **N callers** in the code. When its signature changes, a caller may retain an outdated call. If the caller runs infrequently (once a day at 03:00), the bug will not appear immediately. If the crash is silenced by `try/except` (as in `morning_test_summary.save_to_db`), it may stay silent for weeks.

**Test pattern** (see `tests/unit/test_save_agent_report_contract.py`):

1. An AST parser scans all `*.py` in the project.
2. Finds calls to `<func_name>(...)` (by name or through `db.<func_name>`).
3. For each call, collects `args` (positional) + `keywords` (kwargs).
4. Compares them with `inspect.signature(func)` for required parameters.
5. Fails with `файл:строка` and a list of missing args.

**When to apply**:

- Public APIs with `≥2` callers in the project.
- The signature contains required parameters (without defaults).
- Callers run infrequently or through `try/except`.

**Value**: the check takes ~100 ms and catches a class-wide regression in one day rather than after a week of investigation. After the 2026-05-10 episode (reset --hard reverted morning_test_summary to a 7-argument signature; the contract test caught it on the very first night), the pattern is validated in practice.

**Expansion**: after `save_agent_report`, cover the other public functions in `health_db.py` with required args. This is a W3-DOC extension, not a one-off task.

**Limits**:

- Does not catch **semantic** regressions (the caller passed the right number of args but in the wrong order) — that is the job of `inspect.signature` + named-only args in code.
- Does not catch regressions **through indirect calls** (`getattr(db, name)(...)` where `name` is determined at runtime). This class is rare in the project.

---

## Related documents

| Type | Document |
|---|---|
| Reference | `USE_CASES.md` (catalog + rules) |
| Reference | `uc_index.yaml` (machine-readable index) |
| Reference | `external_dependencies.yaml` (upstream artifacts) |
| Reference | `TEST_ARCHITECTURE.md` (layer and fixture details) |
| Reference | `TESTING_CONTRACTS.md` (conit thresholds) |
| How-to | `docs/how-to/run_tests.md` |
| How-to | `docs/how-to/add_new_uc.md` |
| How-to | `docs/how-to/handle_test_failure.md` |
| Reference | `tests/README.md` (quick start) |
| Reference | `tests/charters/*.md` (risk paths) |
