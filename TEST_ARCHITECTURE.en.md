<!-- translation-of: TEST_ARCHITECTURE.md sha256:4567a2e9db3f -->
**English** · [Русский](TEST_ARCHITECTURE.md)

# TEST_ARCHITECTURE.md — Larry Health OS automated test architecture and work plan

**Version:** 0.1 | **Date:** 2026-05-08

> This file describes **exactly how** we implement the tests designed in
> `USE_CASES.md`. It uses consistency model terminology (Chapter 7 of
> Tanenbaum) because the system has real distributed properties
> that the tests must account for.

---

## 1. Distributed properties of Health OS that affect tests

Before designing the test architecture, let us establish **what is actually distributed in our system**. Otherwise, tests will be written as if for a monolith and miss an entire class of bugs.

| Area | Concept (from Tanenbaum) | What this means for tests |
|---|---|---|
| TC §1 data freshness (oura ≤26h, etc.) | **Conit** + **staleness deviation** | Tests generate data with a controlled age and check the response |
| `pending_labs/` → approval → `lab_results` | **Tentative write** | Tentative is a separate observable object; the test checks the tentative → committed transition |
| MacBook ↔ Studio code rsync | **Lazy replication** | The test must establish that production logic is read on Studio, not on MacBook |
| iCloud sync of `~/health/` | **Lazy replication** + **Eventual consistency** | UC-A-01: the watcher on Studio triggers after iCloud propagation, not instantly |
| Database on Studio as primary | **Single-primary** (Primary-Backup) | UC-I-07: a write attempt from MacBook = fail. The test checks origin |
| Tasks ↔ Reminders every 3h | **Eventual consistency** + **Pull update** | The test allows a sync window, not an instant update |
| Council Round B sees Round A | **Causal consistency** | Round B cannot start before Round A — that violates causal order |
| `_build_gp_context()` reads many sources | **Read consistency** (potentially) | If context is assembled from sources of different ages, the test must detect it |
| Checkin (evening) → GP daily (next morning) | **Read-your-writes (RYW)** for one user | UC-B-11: GP sees my checkin. Test: write → read |
| Lab data approval → morning report | **Read-your-writes (RYW)** | After ✅, the values are visible in `daily_metrics` by 08:00 |

**Main implication:** a mock that assumes «one database, one memory» is wrong. Tests for UC-A-01, UC-G-02, UC-H-01 must emulate the **time gap** between writing and reading, rather than assume atomicity.

---

## 2. Testing layers (test pyramid)

From bottom to top: from fast and numerous to slow and rare.

### Layer 0 — Static checks (outside the pyramid)
This **already exists** in `check_contracts.py`. Leave it alone — it works as a pre-commit gate.

### Layer 1 — Unit (`check`)
- An isolated module with mocked dependencies.
- Fast (<100ms per test), run on every commit.
- Cover: `B` (negative invariants), `E` (cross-check) for ONE module.
- **Examples:**
  - system work is not added to the person's task list (UC-B-04; UC-B-03 retired 28.09)
  - `classify()` returns `lab` with ≥3 signals (UC-A-01 step 2)
  - `is_financial()` filters bills (UC-A-07)
  - language detector on outward-facing text (UC-I-01)
  - `OWNER_CHAT_ID` filter (UC-I-02)

### Layer 2 — Integration (`integration`)
- Several modules connected through data.
- Primarily through `health.db` (in-memory SQLite or a temporary file).
- Cover: connections between import and context, between the database and the agent.
- **Emulate consistency gaps:** specifically, staleness deviation through timestamps in fixtures.
- **Examples:**
  - HAE → `daily_metrics` → `_build_gp_context` (UC-A-03 + UC-B-02)
  - checkin → `context_events` → GP daily (UC-B-11, RYW for one user)
  - safety_net flagged labs → morning report ⊇ list (UC-D-01)

### Layer 3 — E2E with mocks (`e2e_mock`)
- The full pipeline from trigger to output.
- Mock external systems: Telegram API (telegrabber-style mock), Anthropic (snapshot or scripted), ClinVar/MyVariant (fixture JSON), Oura (fixture), PubMed (fixture).
- **Emulate timing and tentative states** — with delays between steps, not instantly.
- **Examples:**
  - PDF → fswatch → import_all → lab_extractor → pending → approve → lab_results → Council → TG (UC-A-01)
  - genome_update_agent with mock ClinVar diff → narrative → TG (UC-C-03)
  - integrity_tests FAIL → triage_agent → automatic launch (UC-D-04)

### Layer 4 — Consistency-specific (a separate class)
**This is what the test pyramid usually lacks.** Tests of distributed properties:
- **Read-your-writes:** write to `lab_results` → the next `_build_gp_context` call sees the record.
- **Tentative → committed:** `pending_labs/` exists, but the data is not in `lab_results`; after approval, both agree.
- **Single-primary:** mock write with MacBook origin → reject.
- **Staleness:** generate `daily_metrics` with `last_updated` 30h ago → the morning report is sent with a warning.
- **Causal:** Round B called before Round A → the code returns an error.
- **Eventual sync window:** after Reminders sync, task statuses agree; before it, they may differ.

### Layer 5 — Snapshot (`C`)
- Reference JSON / structures for critical areas.
- Regeneration requires manual approval (not automatic).
- Stored in `tests/snapshots/`.
- **Examples:**
  - `lab_extractor` JSON for PDFs from labs A and B
  - GP daily structure for a reference day
  - Council final output for a mock session
  - Constitutions headings/sections (not content)

### Layer 6 — LLM-judge (`D`)
- A separate haiku call with explicit questions about the agent's response.
- An auxiliary check, not a source of truth.
- Runs on the same sample as Layer 5, but checks factual invariants.
- **Examples:**
  - «are the coordinator's claims supported by `<context>`?»
  - «is a source older than 6 months mentioned without a date?»

### Layer 7 — Charter (`H`)
- Not code. Route documents + a mandatory execution calendar.
- Stored in `tests/charters/`.
- **Examples:** `CH-ONCO-01.md`, `CH-DAILY-01.md`, `CH-GENOME-01.md`.

**Test pyramid in numbers** (order of magnitude, not exact proportions):

```
              Charter (H)               5 tests
            Snapshot (C)                10 tests
          LLM-judge (D)                 8 tests
        Consistency-specific            12 tests
      E2E_mock                          15 tests
    Integration                         30 tests
  Unit                                  ~80 tests
```

---

## 3. Directory structure

```
health_scripts/
├── tests/
│   ├── conftest.py              # shared fixtures
│   ├── fixtures/
│   │   ├── db.py                # in-memory SQLite + sample data builders
│   │   ├── telegram.py          # mock TG API + chat capture
│   │   ├── anthropic.py         # scripted Anthropic responses
│   │   ├── clinvar.py           # fixture for MyVariant.info
│   │   ├── oura.py
│   │   ├── pubmed.py
│   │   └── time_travel.py       # control of current time for staleness tests
│   ├── unit/
│   │   ├── test_uc_i_01_lang.py     # language detector
│   │   ├── test_uc_i_02_sec.py      # OWNER_CHAT_ID
│   │   ├── test_uc_b_04_no_system_work_in_person_list.py
│   │   └── ...
│   ├── integration/
│   │   ├── test_uc_a_03_hae.py
│   │   ├── test_uc_b_02_gp_context.py
│   │   ├── test_uc_b_11_checkin_to_gp.py    # RYW
│   │   └── ...
│   ├── e2e/
│   │   ├── test_uc_a_01_lab_to_council.py
│   │   ├── test_uc_c_03_genome_monthly.py
│   │   ├── test_uc_h_01_council_full.py
│   │   └── ...
│   ├── consistency/                 # ← SEPARATE layer
│   │   ├── test_ryw_lab_to_morning.py
│   │   ├── test_tentative_pending_labs.py
│   │   ├── test_single_primary_db.py
│   │   ├── test_staleness_oura_26h.py
│   │   ├── test_causal_council_rounds.py
│   │   └── test_eventual_tasks_reminders.py
│   ├── snapshots/
│   │   ├── lab_extractor/
│   │   │   ├── lab_a_2026-04-15.json
│   │   │   └── lab_b_2026-04-20.json
│   │   ├── gp_daily/
│   │   │   └── reference_2026-04-22.md
│   │   └── council/
│   │       └── reference_session.json
│   ├── llm_judge/
│   │   ├── test_council_facts_grounded.py
│   │   └── test_stale_source_dated.py
│   └── charters/
│       ├── CH-ONCO-01.md
│       ├── CH-DAILY-01.md
│       ├── CH-GENOME-01.md
│       ├── CH-MDT-01.md
│       └── CH-CORR-01.md
├── conftest.py                      # pytest root config
└── pyproject.toml / pytest.ini
```

---

## 4. Key fixtures (what we build first)

### 4.1 `db` — in-memory SQLite with the real schema
- Takes `CREATE TABLE` from the real `health.db` (through introspection or a dump).
- Populated through builders: `make_daily_metrics(date, hrv=NULL, sleep_total=7.5, ...)`.
- Each test gets a clean database (per-test fixture, not per-session).

### 4.2 `time_travel` — control of `today`/`now`
- `freezegun` or a handmade `Clock` object.
- All agents that call `date.today()` or `datetime.now()` must accept a `clock` parameter (refactor).
- Without this, tests for staleness of 26h/30 days cannot be implemented.
- **This is technical debt in the current code** — it will surface on the first attempt to write the UC-D-05 test.

### 4.3 `telegram_mock`
- Replaces `bot.send_message`, `send_photo`, `InlineKeyboardMarkup`.
- Capture mode: the test sees what was sent, to whom, and in what order.
- Inject mode: the test sends an «update» as the user or an unauthorized chat (for UC-I-02).

### 4.4 `anthropic_mock`
- Two modes:
  - **Scripted** — for unit/integration: returns fixed JSON by prompt pattern.
  - **Recorded** — for snapshot tests: the first run records, subsequent runs replay.
- Makes NO live calls in CI.

### 4.5 `clinvar_fixture`
- JSON fixtures for FUNCTIONAL_WHITELIST rsids + 5-10 reference Pathogenic variants.
- Mock with a distorted schema for UC-K-04.

### 4.6 `oura_fixture`, `hae_fixture`
- Minimal JSON samples for 7d + 90d.
- Edge cases: an empty day, a NULL field, `summary_date` does not match the expected target.

### 4.7 `pending_labs_dir`
- A temporary folder with subdirectories by `visit_key`.
- Fixture for tentative-write tests.

---

## 5. Strategies for consistency scenarios

### 5.1 Read-your-writes (RYW) test
**Scenario:** UC-B-11 (checkin → GP daily the next morning).

```python
def test_checkin_visible_in_gp_next_morning(db, clock):
    # Day N evening
    clock.set("2026-05-08 21:00")
    save_checkin(db, mood=4, energy=3, stress="manageable")

    # Day N+1 morning
    clock.advance(hours=11)  # 08:00 next day
    ctx = build_gp_context(db, target=clock.today())

    assert ctx["recent_checkins"], "checkin from yesterday must be visible"
    assert ctx["recent_checkins"][0]["mood"] == 4
```

### 5.2 Tentative → committed test
**Scenario:** UC-A-01 lab_extractor pipeline.

```python
def test_pending_lab_not_in_lab_results_until_approved(db, pending_labs_dir):
    # Tentative state
    extract_labs_to_pending(pdf="tests/fixtures/sample_lab_a.pdf",
                           output_dir=pending_labs_dir)
    assert (pending_labs_dir / "2026-05-01_lab_a.json").exists()
    assert db.execute("SELECT COUNT(*) FROM lab_results WHERE date='2026-05-01'").fetchone()[0] == 0

    # Commit
    import_from_pending(db, pending_labs_dir / "2026-05-01_lab_a.json")
    assert db.execute("SELECT COUNT(*) FROM lab_results WHERE date='2026-05-01'").fetchone()[0] > 0
```

### 5.3 Single-primary test
**Scenario:** UC-I-07 — a write attempt with MacBook origin → fail.

Option 1 (explicit): add an `origin_host` column to the `_audit_log` table. Test with mock host = MacBook → write rejection.

Option 2 (indirect): test that the database path on MacBook is a read-only iCloud copy, and the one on Studio is operational.

I lean toward **option 1** — an explicit audit that itself becomes a testable invariant.

### 5.4 Staleness test
**Scenario:** UC-D-05 — stale labs → date annotation.

```python
def test_stale_labs_marked_with_date(db, clock):
    # illustrative example: analyte, value and dates are made up
    save_lab_result(db, date="2025-01-01", name="ANALYTE_X", value=1.0)
    clock.set("2025-10-01")  # 9 months later

    output = gp_agent.daily_report(db, clock=clock)
    assert "2025-01-01" in output, "must mention source date"
    assert any(marker in output.lower()
               for marker in ["требует обновления", "устарел", "9 месяцев"])  # "needs update", "outdated", "9 months"
```

### 5.5 Causal order test (Round B after Round A)
**Scenario:** UC-H-01 — an attempt to run Round B before Round A.

```python
def test_round_b_before_round_a_raises(consultation_session):
    # Simulate an ordering violation
    with pytest.raises(CausalOrderError):
        consultation_session.run_round_b()  # Round A not called
```

### 5.6 Eventual sync window test
**Scenario:** UC-G-02 reminders_sync.

```python
def test_completed_reminder_syncs_within_3h(db, reminders_mock, clock):
    create_task(db, fingerprint="lab:ANALYTE_X")
    create_reminder(reminders_mock, task_id=1)

    # Mark complete in Reminders, but not synced yet
    reminders_mock.complete(task_id=1)
    assert db.task_status(1) == "open"  # not synced yet

    # Run sync (simulating cron every 3h)
    reminders_sync.run(db, reminders_mock)
    assert db.task_status(1) == "done"
```

---

## 6. CI/runner strategy

| Layer | When it runs | Time |
|---|---|---|
| Static + Unit | every commit (post-commit hook) | <10s |
| Integration | every commit | <30s |
| E2E_mock | pre-push + nightly | 1-3 min |
| Consistency | nightly | 30s-1min |
| Snapshot | on touched-file matches + nightly | varies |
| LLM-judge | nightly + manually on demand | costly API usage |
| Charter | on a calendar (see below) | person-hours |

**Charter schedule** (add to integrity_tests as a `charter_due` warning):
- `CH-ONCO-01` — monthly (the 1st)
- `CH-DAILY-01` — every 2 weeks
- `CH-GENOME-01` — after every genome_update narrative
- `CH-MDT-01` — after a major change to prompt structure
- `CH-CORR-01` — after every `longitudinal_analysis` run

If no log entry records charter execution within the window, `integrity_tests` raises WARN.

---

## 7. Work plan (phases)

### Phase 0 — Infrastructure (≈3 days)
**Goal:** basic pytest setup, fixtures, mocks.
**Deliverables:**
- `tests/conftest.py` + the 7 fixture modules from §4
- `pytest.ini` with markers (`@pytest.mark.unit`, `@pytest.mark.consistency`, etc.)
- A hook in `run_checks.sh` to run unit+integration
- The `tests/README.md` document

**Dependencies:** refactor the code to add a `clock` parameter where time_travel is needed. This is a **separate task** and can run in parallel.

### Phase 1 — First batch of 11 UC (≈4 days)
**Goal:** the full UC-J-02 cycle (plan → confirmation → code) for the 11 confirmed UC from §8 of USE_CASES.md.

Order (from simple to complex):
1. `UC-I-02` (`UC-SEC-001`) — Telegram fail-closed. The simplest unit test. A process example.
2. `UC-I-01` (`UC-LANG-001`) — language detector. Unit.
3. `UC-I-03` (`UC-NULL-001`) — NULL ≠ 0. Unit + integration.
4. `UC-A-04` (`UC-OURA-001`) — sleep/activity dates. Unit + integration.
5. `UC-B-03` — retired 28.09 (trends go into review).
6. `UC-B-04` — system work is not added to the person's list. Unit.
7. `UC-D-04` — triage_agent WARN auto-fixes. Integration + e2e_mock.
8. `UC-H-02` — `user_qa.append()` before rounds. Unit (call order).
9. `UC-J-01` + `UC-J-02` — proposal + confirmation. Meta infrastructure.
10. `UC-A-03` (`UC-APPLE-001`) — HAE merge. Integration.
11. `UC-K-01` (`UC-UPSTREAM-PROMPT-001`) — after receiving the upstream URL.

**Deliverables:** 11 test files + 11 markdown plans (UC-J-02) in `tests/plans/`.

### Phase 2 — Consistency-specific layer (≈3 days)
**Goal:** implement the §5 scenarios (RYW, tentative, single-primary, staleness, causal, eventual).

**Dependencies:** single-primary requires an audit column in the database. This is a migration.

### Phase 3 — Remaining P0 (~30 UC) (≈1.5 weeks)
**Goal:** tests for all confirmed P0.

In parallel: charters are written as markdown documents (this is quick).

### Phase 4 — Snapshot + LLM-judge (≈4 days)
**Goal:** establish references, write judge prompts.

**Main risk:** a snapshot of the Council final output. If the structure changes often, maintenance costs exceed the benefit. We may limit this to 1-2 snapshots of critical areas.

### Phase 5 — Meta + upstream (≈1 week)
- `UC-J-03` `uc_index.yaml` validator
- `UC-J-04` needs_review mechanism
- `UC-K-01` after receiving the upstream URL
- `UC-X-01..03` dependency registry

### Phase 6 — Charter calendar + integration into integrity_tests (≈2 days)
- Add a `charter_due` WARN to `integrity_tests.py`
- Hook in triage_agent: WARN charter overdue → question in TG.

---

## 8. Open architectural questions

1. **`clock` parameter in agents** — a major refactor. Now or after the first batch?
2. **`origin_host` audit column** — database migration. When?
3. **Snapshot storage format** — JSON / YAML / Markdown? I lean toward JSON for machine-readable data, MD for text.
4. **LLM-judge cost** — how many API calls in a nightly run? Budget?
5. **Where UC-J-02 test plans live** — in `tests/plans/UC-A-01.md` or as a separate section in `USE_CASES.md`?
6. **iCloud propagation in e2e** — real iCloud latency or mocks? (An approach borrowed elsewhere: test through a local folder with an artificial delay.)

---

## 9. Tradeoffs (from Tanenbaum § replication for reliability vs performance vs scalability)

Health OS uses **replication for reliability** (single user, not for scalability). This means:
- Scalability tests (load tests) are **not needed**.
- Fault tolerance tests (what happens when Studio goes down? when iCloud is unavailable?) **are needed**, but within limits.
- The main focus is **correct medical conclusions** and **no data loss**.

This affects priorities:
- High: B (negative), E (cross-check), single-primary, RYW.
- Medium: tentative, staleness, eventual sync.
- Low: high-load scenarios, concurrent conflicts between two users (there are no two users).

---

## 11. Schedule and orchestration

### 11.1 Daily run

`com.larry.health.test-suite` — launchd plist, **00:00 in Studio's local time**.

Studio is in the same time zone as home → `local time = home time`. If Studio moves, rewrite the plist.

What runs:

```
00:00  run_full_test_suite.sh
       ├── unit                    (~10s)
       ├── integration             (~30s)
       ├── e2e_mock                (1-3 min)
       ├── consistency             (30-60s)
       ├── snapshot (touched only) (variable)
       └── llm_judge               (3-5 min, expensive)
00:10  test_failure_handler.py    ← new module
00:15  morning_test_summary.py    ← writes a summary to agent_reports
```

**Why 00:00, not 03:00:** by 06:45 (`morningwake` caffeinate) and 08:00 (morning cycle), results must be ready and processed. 00:00 leaves a buffer for retries and diagnosis.

### 11.2 Severity differences by UC status

A test failure is interpreted through the UC `status` from USE_CASES.md:

| UC status | Test failed | Severity |
|---|---|---|
| `implemented` | regression | **CRITICAL** — immediate TG alert + Reminder |
| `partial` (secured part) | regression | **CRITICAL** |
| `partial` (gap part) | expected_gap | INFO — gap-report, no alerts |
| `intended` | expected_gap | INFO — gap-report |
| `speculative` | the test should not exist | possible catalog error, WARN |

**Principle:** `expected_gap` goes into the cumulative gap-report without noise. CRITICAL interrupts sleep.

---

## 12. Test failure handler (what happens on failure)

New module `test_failure_handler.py`. Called after `run_full_test_suite.sh`.

### 12.1 Level A — Deterministic triage

No LLM. Based on log patterns:

| Pattern | Action | Idempotency |
|---|---|---|
| `OperationalError: database is locked` | retry 1× with a 5s delay | once per day |
| `Anthropic API timeout` | mark the test as `flaky_today`, do not alert | by day |
| `iCloud-evicted` fixture | re-fetch fixture, re-run | once |
| Snapshot drift with a small timestamp/uuid change | auto-regenerate with a warning | requires confirmation |

If level A fixes it → an entry in `tests/reports/{date}/auto_fixes.log`, no alarm.

### 12.2 Level B — Diagnosis through Claude Haiku

For each remaining CRITICAL failure, one Haiku call with a fixed prompt:

```
[system]
You are the Health OS autotest diagnostician. Read the context and write markdown
with a hypothesis of the cause and 2-3 suggestions of what to try. Do not fix.
Do not write code. Do not use emoji. No filler.

[user]
Failing test: {uc_id} ({uc_alias})
Status UC: {status}
Confirmation: {confirmation}
Owner modules: {owner}

Logs:
{tail of pytest output}

UC contract (Given/When/Then/B/E):
{relevant section from USE_CASES.md}

Recent git diff (24h):
{git log + diff for owner modules}

Questions:
1. What is the most likely cause (1-2 sentences)?
2. What to check first?
3. Does this look like a regression from a fresh commit or an old bug?
```

The response is saved in `tests/reports/{date}/{uc_id}_diagnosis.md`.

### 12.3 Level C — Repair (governed by the rulebook, not this page)

The rule for automatically applying a fix after a failure lives in `CLAUDE.md §13` (in the closed part), alongside
its auto-repair envelope and the owner's 2026-08-03 amendment narrowing the prohibition to three
simultaneous conditions (reversibility, an independent reviewer, an oracle that fails
on breakage downstream). This is a **pointer, not a restatement**.

Why a pointer: from 2026-08-03 to 2026-09-14, this page retained the old wording
«we will not implement it», which was already incorrect, and the docstring of `test_failure_handler.py`
pointed here. A restatement of a rule ages independently of the rule — §18; a second
copy of a verdict adds no knowledge, only another way to diverge.

In practice, today: level C is not implemented in `test_failure_handler.py`,
and the project has no mechanism for automatic application (`fix_applier`). The mechanism's status is in
`subsystem_intent.yaml` (`auto_fix_applier_absent`), so it does not acquire
another home here either.

---

## 13. Result output

### 13.1 Files

```
tests/reports/{YYYY-MM-DD}/
├── summary.json              # machine-readable summary
├── junit.xml                 # standard pytest format
├── auto_fixes.log            # what level A fixed
├── gap_report.md             # expected_gaps (cumulative)
├── {UC-A-01}_diagnosis.md    # diagnosis from Haiku, one per CRITICAL
├── {UC-B-02}_diagnosis.md
└── ...
```

`tests/gap_report.md` — a **current snapshot** of all expected_gap entries. Updated every morning. It does not accumulate history — it reflects the current state of «where the code has not caught up with intent».

### 13.2 Morning report (08:00)

A section is added to `morning_report.py`:

```
## Tests

123 PASS · 4 expected_gap · 0 regression · 1 flaky_today · 2.4s
```

If there is a `regression`, the section goes at the top, not the end.

### 13.3 Telegram

**CRITICAL only** (regression). Format:

```
⚠️ Regression: UC-B-02 GP daily context
Status: implemented → failed
Diagnosis: tests/reports/2026-05-09/UC-B-02_diagnosis.md
Reminder set for 10:00.
```

**Does NOT alert** for expected_gap, flaky_today, infrastructure issues, charter overdue (these go into the morning report as INFO).

### 13.4 Reminder at 10:00 — a ready-to-paste prompt

For each CRITICAL failure, a separate Reminder in the «Health» list with `due=today 10:00`:

**Title:** `Test failure: UC-B-02 (GP daily context)`

**Body:**

```
Copy into Claude:
---
Health OS: test UC-B-02 failed (GP daily context 7d + 4 horizons).
Status UC: implemented · this is a regression, not an expected_gap.

Context:
- diagnosis: ~/health_scripts/tests/reports/2026-05-09/UC-B-02_diagnosis.md
- UC contract: ~/health_scripts/USE_CASES.md §UC-B-02
- test log: ~/health_scripts/tests/reports/2026-05-09/junit.xml
- recent diff: git log --since=2 days ago -- gp_agent.py health_db.py

Task: read the diagnosis, assess the hypothesis, decide what to do.
Remember: do not weaken the assert to get a green test.
```

**Fingerprint:** `test_fail:UC-B-02` (UC-G-01 dedup — another failure of the same UC on the same day does not create more Reminders).

### 13.5 Charter schedule

A `charter_due` check is added to `integrity_tests.py`:

```python
def check_charter_overdue():
    for charter, max_age in {
        "CH-ONCO-01": 31,
        "CH-DAILY-01": 14,
        "CH-GENOME-01": 60,    # after each genome_update
        "CH-MDT-01": 90,
        "CH-CORR-01": 30,
    }.items():
        last = read_charter_log(charter)
        if last and (today - last).days > max_age:
            warn(f"Charter {charter} overdue", f"last: {last}")
```

WARN → triage_agent asks in TG: «Run the CH-ONCO-01 review?» with a link to the charter route (the charter itself is in the private part of the project: it was written for the owner's clinical profile).

---

## 14. Changes to the work plan (§7) in light of §11–13

Added to **Phase 0:**
- launchd plist `com.larry.health.test-suite`
- `test_failure_handler.py` (level A + B)
- `morning_test_summary.py`
- Extension of `morning_report.py` («Tests» section)

This adds +2 days to Phase 0. Total Phase 0 = ≈5 days.

---

## 15. Open questions (in addition to §8)

1. **Budget for Haiku diagnosis calls.** With 5 CRITICAL per night × ~5K tokens = ~$0.05. Acceptable. But for recurring failures, cache diagnosis by test+commit hash.
2. **TZ when traveling.** A launchd plist with a fixed `Hour=0` follows the machine's local time. If Studio is in one time zone and you are in another, that is fine (tests run on Studio). If Studio moves, rewrite it.
3. **What to do about charter overdue when you are traveling.** WARN does not block, but reminders accumulate. We may need a command to «postpone the charter for N days».

---

## 15б. What is deliberately NOT done (owner's decisions)

Moved from BLUEPRINT (the «Testing strategy» section) on 2026-08-02: there it sat in an explanation document and read as a status report rather than an adopted decision. Each item is a settled choice with a stated reason, not debt.

- **There is no full LLM-judge eval pipeline** (W3B deferred). Instead, self-consistency double-run is used for critical UC. The owner's reason: «I will notice from the reports».
- **There is no automatic fix for a detected failure — only Diagnose-don't-Repair** (reminder + ready-to-use prompt). The rule lives in `CLAUDE.md §13` (in the closed part), step 1: a failed test does not provide a total, machine-checkable safety predicate, so it cannot satisfy the auto-repair envelope. Precedent: `triage_agent` spawned an agent with `subprocess.Popen` inside the short-lived `morning_report` → launchd reaped the group → SIGKILL before writing; spawning was removed on 2026-06-29. **Since 2026-08-03, the prohibition has been narrowed, not lifted** (the owner's amendment in the same place, `CLAUDE.md §13` (in the closed part)): automatic application is allowed with reversibility + an independent reviewer + an oracle that fails on breakage downstream. The project has no mechanism exercising that right — «no auto-fix» is currently a fact, not a prohibition.
- **There is no charter-overdue mechanism.** The owner's reason: «I will see it myself». (Cf. §13.5 above, where it is described as a design — a plan, not a live mechanism.)
- **The PubMed schema-drift watcher and Anthropic-deprecation watcher have not been built** (W2B-2/3 in the backlog).

## 16. Change history

| Date | Version | Changes |
|------|--------|-----------|
| 2026-05-08 | 0.1 | First draft: 7-layer pyramid + consistency-specific layer; a 6-phase plan; fixtures; integration with TC and USE_CASES.md |
| 2026-05-08 | 0.2 | Added §11 schedule (00:00 local time); §12 test_failure_handler with 3 levels (Triage/Diagnosis/Repair-rejected); §13 output format (TG for regression, Reminder at 10:00 with a ready-to-use prompt); §14 plan adjustment (+2d to Phase 0) |
