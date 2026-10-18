<!-- translation-of: docs/explanation/test_architecture.md sha256:74d56b625bf4 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](test_architecture.md)

# Test Architecture

> **Document type:** Explanation (Diataxis) — explains **why** the test
> system is designed the way it is. For **what exists** — see `TEST_ARCHITECTURE.md`,
> `USE_CASES.md`, `uc_index.yaml`. For **how to do things** — see
> `docs/how-to/run_tests.md`, `add_new_uc.md`, `handle_test_failure.md`.

---

## The Idea

Health OS makes medical claims. Tests must catch **regressions in the
correctness of those claims**, not just "the code doesn't crash". This changes
everything relative to a standard pytest scaffold.

## Three Core Principles

### 1. Spec-by-example: `USE_CASES.md` is the single source of truth

The UC catalogue describes **desired behaviour**, not current behaviour. A test
against an `intended` UC is an `expected_gap`, not a "broken test". A test
against an `implemented` UC is a regression signal.

The `status` × `confirmation` lifecycle distinguishes:

| status | confirmation | a failing test means |
|---|---|---|
| `implemented` | `confirmed` | REGRESSION → CRITICAL → TG alert + Reminder |
| `partial` (gap part) | `confirmed` | expected_gap → INFO |
| `intended` | `confirmed` | expected_gap → INFO (code hasn't caught up to the intention yet) |
| any | `proposed`/`rejected` | the test must not exist (UC-J-02) |

Without `confirmation = confirmed`, `generate_test.py` refuses to generate a
skeleton — this is disciplined development.

### 2. Automation boundary: B+E+C+D, plus H for quality

Not all medical quality can be automated. A 4-level strategy:

- **B (negative invariants)** — what the system must NOT do. The primary
  invariant for medicine: "if NULL — do not assert".
- **E (cross-check against deterministic truth)** — numbers in text = numbers
  in the DB (not invented).
- **C (snapshot)** — for critical JSON structures. Rejected for LLM output
  as an illusion of protection (cannot distinguish "regression in code" vs
  "LLM instability").
- **D (LLM-judge)** — a separate Claude Haiku with explicit questions. Not a
  source of truth. Never the sole oracle for P0.

For qualitative claims (UC-I-08 oncology context, UC-I-09 genome-as-frame,
UC-H-03 Round B deliberation) — `H` (Human charter): a risk-oriented review
route. This is not code but markdown in `tests/charters/`. `assert "good
doctor"` does not exist.

### 3. Distributed properties of Health OS require a dedicated test layer

The standard test pyramid ignores what the project actually has:

- **Conit + staleness deviation** (TC §1) — data older than 26h/30 days
  must trigger an alert. Tests for this require **time substitution**,
  otherwise they are unimplementable. `_time_inject.py` solves this
  centrally.
- **Tentative write** — `pending_labs/` is a separate observable object
  BEFORE approval. Tests cover the tentative → committed transition.
- **Single-primary** — writes to `health.db` only from Studio. The guard in
  `health_db.get_conn()` returns a read-only connection on non-Studio.
- **Read-your-writes** — check-in in the evening → GP sees it in the morning.
- **Causal consistency** — Council Round B depends on Round A.
- **Eventual consistency** — Tasks ↔ Reminders sync window 3h.

There is a dedicated layer `tests/consistency/` for these (not unit, not
integration — specifically about distributed properties).

---

## The 7-Layer Pyramid

```
                    Charter (H)            5 шт   ← markdown, не код
                  Snapshot (C)            ~1 шт   ← минимально
              LLM-judge (D)               2 шт    ← skipped без API ключа
          Consistency-specific           21 шт    ← RYW/tentative/staleness/...
        E2E_mock                          5 шт
      Integration                        14 шт
    Unit                                 ~110 шт  ← основа
```

Layers 1–4 run on every commit via `pytest -m "not slow"`.
Layer 5 (consistency) is also fast and is included in the main suite.
Layer 6 (LLM-judge) — nightly only, requires the Anthropic API.
Layer 7 (charter) — not code, runs on a calendar schedule.

---

## Architectural Decisions That Required Justification

### Why `_time_inject.py` rather than a `clock` parameter in every function

The original plan was "an optional `clock` parameter in important functions".
After an audit it turned out that 40+ files use `date.today()` /
`datetime.now()`. Adding a parameter to each one means a lot of ceremony in
agent code and it is easy to forget to pass it in one place.

`_time_inject.get_now()` / `get_today()` — global substitution via
`set_test_clock()`. A test changes `_TEST_CLOCK`; production sees real time.
In tests, automatic reset is done via an autouse fixture in `conftest.py`.

CLI blocks `if __name__ == "__main__":` are intentionally left untouched —
the test calls the function directly with fixed dates, bypassing the CLI.

### Why snapshot for LLM output was rejected

A snapshot catches divergence from a reference. But for an LLM, divergence can
come from: (a) a code regression, (b) model instability, (c) normal generation
variability. A snapshot cannot distinguish between these.

The replacement for UC-A-01 (`lab_extractor`): self-consistency — a double
run on the same PDF, and the key fields `{name, value, unit, flagged}` must
match bit-for-bit. A mismatch = extractor instability (actionable: prompt
refactoring or temperature=0 is needed). This is not hand-to-hand combat with
references on every change.

### Why test_failure_handler does not fix code itself (Repair is narrowed — see the amendment below)

Fully automatic Claude patching of production code in a medical system =
risk of silent weakening of asserts. Claude in agent mode will sometimes
"fix" a test by weakening the check instead of fixing the logic. The cost of
a silent regression in the correctness of medical conclusions outweighs any
time saving.

The accepted model: **diagnose, don't repair**.
- Level A — deterministic retries based on log patterns.
- Level B — Haiku writes a diagnosis to markdown; a human reads it in the
  morning.
- A Reminder at 10:00 with a ready-to-paste prompt — your idea, it turned out
  to be sound. In the morning: open the Reminder → copy → Claude helps work
  it out.

**Owner amendment 2026-08-03 (night-cycle thread): the ban is NARROWED, not lifted.**
Auto-applying a red fix is permitted when all three conditions hold simultaneously:
(1) the action is reversible (`git revert`, not deleting lines and not a data
migration); (2) the patch has passed an independent reviewer agent (§17 — a
refutation prompt, without the author's explanations); (3) there is a
DOWNSTREAM oracle that turns red — the nightly integrity check catches a
breakage CAUSED BY the fix and parks it back. Everything else remains
"diagnose, don't repair". The normative text and the cost accepted by the owner
with full awareness (a window of up to one day between application and nightly
detection) are in the compendium, `CLAUDE.md §13`; this is a pointer so that
the page is not read as a currently operative blanket ban.
The carrier mechanism (`fix_applier`) is not built: until it is, the amendment
is declarative.

### Why severity is based on UC status, not test type

The approach "test failed → CRITICAL" creates noise. 70% of tests may be
against `intended` UCs, and every nightly run will be CRITICAL.

The approach "test failed → CRITICAL depends on UC status" separates
regressions from expected gaps. If UC-A-01 has status=`partial` (Council
auto-launch deferred to backlog), a test for that part of the pipeline is an
`expected_gap`, not noise.

### Why UC-J-02 forbids generating a test for an unconfirmed UC

Without this process, an agent could write a test against its own
interpretation of a UC instead of clarifying with the user exactly what is
being tested. `confirmation = confirmed` is an explicit gate: "yes, this is
the behaviour we are testing".

### Why `sys.modules.pop`/reload of shared modules in tests is forbidden (2026-06-22)

`health_db` is imported by dozens of modules (`treatment_*`, `survivorship`,
etc.). If a fixture does `sys.modules.pop("health_db")` + reimport, a NEW
module object is created. Modules that had already imported the old one hold
v1, while the fixture/test works with v2 → **split-brain between two
health_db instances**: writes go to one DB, reads come from another.
The symptom is insidious — the test passes in isolation but fails only in a
full run (`pytest tests/`), and specifically in UNRELATED files, depending on
collection order. This is how the entire `tests/` suite was silently red
(22 failed) while individual sub-suites were green.

Rules:
- DB substitution — via `monkeypatch.setattr(health_db, "DB_PATH", tmp)` (the
  same object, auto-restore), as in `tests/fixtures/db.py`. Not pop+reimport.
- If pop is unavoidable (test of an import-time guard): save the ORIGINAL
  object and restore exactly that in `finally`, not a fresh import.
  `importlib.reload(x)` is gentler — re-executes in place, identity is
  preserved.
- Diagnosing order-dependent failures — by bisection (suspected file BEFORE
  the victim), not by the intuition "these modules are unrelated".

⚠️ `run_checks.sh` (post-commit) runs smoke_tests + integrity_tests, NOT pytest —
this class of bugs is only caught by a manual/CI full `pytest tests/`.

In a medical system, the cost of "wrote a test against the wrong behaviour" =
a regression is not caught → bad conclusions reach the user.

---

### Why a test waits for its threads before fixture teardown (2026-10-05)

The DB substitution in a test (`monkeypatch`, temporary `DB_PATH`) lives exactly
until fixture teardown. A thread launched by the test does not know about this:
if it runs longer than the test, its late write goes to the path that has
already been restored. At night, that path is the owner's live database.

That is exactly what happened from 30.08 to 04.10. The background council test
waited for the council to be *invoked*, not for the thread to finish. The thread
wrote its verdict to the live database — into the `memory` row with the same
number as the hypothesis in the test database (the number is shared across all
categories) — and created tasks for the owner via the `test` field. The test was
green. Writing was stopped by the `query_only` guard (the treatment-homes thread),
but even then one warning line remained in the nightly log that nobody read.

What is now in place (`tests/conftest.py`, `thread_guard` block; `pytest.ini`):
- a call-phase hook waits for threads spawned by the test — in the call phase,
  i.e. BEFORE fixture teardown; a fixture cannot do this: its teardown would run
  after the substitution had been rolled back;
- threads that did not finish in time — the test is red with the thread's name;
- an exception in a thread — an error, not a warning (`filterwarnings`);
- boundary: an idle AnyIO worker pool worker (TestClient panel) is excluded by
  name — the response has already been delivered by that point; bench
  measurement: 69 panel tests were red only because of it;
- the hypothesis solver writes only to rows with `category='hypothesis'`.

**The nightly suite continues to run against the live environment (06.10.2026,
owner decision).** Running against a DB copy was measured: a copy in a different
directory breaks ~60 tests that compare the data directory against installed
services, producing a cascade of fixture errors. Therefore the nightly run
operates under the guards above, not against a copy.
The AnyIO worker pool worker is excluded from the thread guard by class and idle
state (`idle_workers` queue), not by name: a busy worker is waited for like any
other thread.

The nightly chronicle (`run_checks.sh`) names both ERROR (fixture or teardown
failure) and FAILED: a red guard in a fixture would otherwise pass unnoticed as
"suite unchanged".

## Contract Tests for Public APIs (pattern, validated 2026-05-10)

A pattern that proved valuable after the regression of 2026-05-10:

**Idea**. If a public function in `health_db.py` (or another core module) has
required parameters, it has **N callers** in the codebase. When a signature
changes, one of those callers may remain with a stale call. If the caller runs
infrequently (once a day at 03:00) — the bug may not surface immediately. If the
crash is silenced by `try/except` (as was the case in
`morning_test_summary.save_to_db`) — it can stay silent for weeks.

**Test pattern** (see `tests/unit/test_save_agent_report_contract.py`):

1. An AST parser scans all `*.py` files in the project.
2. It finds calls to `<func_name>(...)` (by name or via `db.<func_name>`).
3. For each call: collects `args` (positional) + `keywords` (kwargs).
4. Compares against `inspect.signature(func)` for required parameters.
5. Fails with `file:line` and a list of missing args.

**When to apply**:

- Public APIs with `≥2` callers in the project.
- Signatures that contain required parameters (no default).
- Callers that run infrequently or through `try/except`.

**Value**: the check takes ~100ms, catches a class-wide regression in one day
rather than after a week of investigation. After the 2026-05-10 episode (reset
--hard rolled `morning_test_summary` back to a 7-arg signature; the contract
test caught it on the very first night) this pattern is validated in practice.

**Expansion**: after `save_agent_report`, the other public functions in
`health_db.py` with required args should be covered. This is a W3-DOC
expansion, not a one-off task.

**Limits**:

- Does not catch **semantic** regressions (a caller passed the right number of
  args but in the wrong order) — that is the job of `inspect.signature` +
  named-only args in the code.
- Does not catch regressions **via indirect calls** (`getattr(db, name)(...)`
  where `name` is determined at runtime). This class is rare in the project.

---

## Related Documents

| Type | Document |
|---|---|
| Reference | `USE_CASES.md` (catalogue + rules) |
| Reference | `uc_index.yaml` (machine-readable index) |
| Reference | `external_dependencies.yaml` (upstream artefacts) |
| Reference | `TEST_ARCHITECTURE.md` (layer and fixture details) |
| Reference | `TESTING_CONTRACTS.md` (conit thresholds) |
| How-to | `docs/how-to/run_tests.md` |
| How-to | `docs/how-to/add_new_uc.md` |
| How-to | `docs/how-to/handle_test_failure.md` |
| Reference | `tests/README.md` (quick start) |
| Reference | `tests/charters/*.md` (risk routes) |
