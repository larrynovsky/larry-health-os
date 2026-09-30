<!-- translation-of: docs/how-to/handle_test_failure.md sha256:50039d5b3980 -->
**English** · [Русский](handle_test_failure.md)

# What to do when the nightly test suite fails

> **Document type:** How-to (Diataxis).
> Context: `docs/explanation/test_architecture.md`.
> Implementation: `test_failure_handler.py` + `triage_agent.py`.

---

## Morning flow (normal system behavior)

### 00:00 local time — `com.larry.health.test-suite`

`run_full_test_suite.sh` runs unit → integration → consistency →
e2e_mock → snapshot → llm_judge. Artifacts in `tests/reports/{date}/`:
junit.xml for each layer + `summary.json`.

### 00:10 — `test_failure_handler.py`

Parses junit.xml and classifies failures by level:

- **Level A — deterministic triage.** Patterns:
  - `database is locked` → retry once;
  - `Anthropic timeout` → mark `flaky_today`;
  - `iCloud-evicted` → `flaky_today`.
- **Level B — Haiku diagnosis.** For the remaining CRITICAL failures, one Haiku
  call with a fixed prompt → markdown in
  `tests/reports/{date}/{UC}_diagnosis.md`. Cached by `test_id+commit_hash`.
- **Level C — Repair.** Not implemented. The rule is `CLAUDE.md §13` (private part):
  under the owner's amendment of 2026-08-03, automatically applying changes in response to red results is **restricted, not
  prohibited** (reversibility + an independent reviewer + a downstream oracle
  that turns red). The project has no mechanism exercising this permission.

### 00:15 — `morning_test_summary.py`

Consolidates results into `agent_reports type='test_summary'`. At 08:30, `morning_report.py`
reads the entry and adds a “Tests” section to the morning report.

### 08:00–08:30 — the morning cycle sees the result

Telegram receives:

- **0 regressions** → one line in the morning report: `Тесты: 301 pass`.
- **N regressions > 0** → a separate TG alert before the main report:
  ```
  ⚠️ Test regression: 1 fail(s)
  • unit/test_triage_delivery.py::test_person_questions...
  Diagnosis: tests/reports/2026-05-09/
  ```

### 10:00 — Reminder

For each CRITICAL failure, a separate Reminder in the “Health” list with a prepared
prompt to copy and paste. Open Reminders → copy the body → paste it
into Claude → investigate.

---

## What to do manually: a step-by-step plan for a regression

### Step 1: Read the diagnosis

```bash
ssh <studio_ssh> "ls ~/health_scripts/tests/reports/$(date +%Y-%m-%d)/"
ssh <studio_ssh> "cat ~/health_scripts/tests/reports/$(date +%Y-%m-%d)/{UC_id}_diagnosis.md"
```

The diagnosis is Haiku's hypothesis about the cause. A starting point,
not a final answer.

### Step 2: Open the Reminder

In macOS Reminders → “Health” list. Subject: `Test failure: UC-X-NN`.
The body contains a prepared prompt. Copy it.

### Step 3: Start Claude with this prompt

```
[paste the Reminder body into Claude]
```

Claude will read the diagnosis, the UC contract, and the recent diff, and suggest what to try.

### Step 4: Reproduce locally

```bash
cd ~/health_scripts
python3.11 -m pytest tests/{layer}/test_uc_x_nn_*.py -v
```

If it reproduces, fix the code **(not the test!)**. Run it again.

### Step 5: If it is an xfail candidate, mark it xfail with a rationale

If the regression is accepted as “temporary” (for example, a partial UC):

```python
@pytest.mark.xfail(reason="...", strict=True)
def test_x():
    ...
```

`strict=True` — if the test unexpectedly turns green, pytest will complain.

---

## Scenario: a test from expected_gap suddenly turns green

A test for an `intended` UC passes: this signals that the code has “caught up” with the intent.

1. Read `tests/reports/{date}/junit.xml` and find `XPASS`.
2. Open the UC in `USE_CASES.md` and change `status: intended → partial` or
   `partial → implemented`.
3. Remove the `@pytest.mark.xfail` decorator.
4. Run it → it should be green.
5. Update `uc_index.yaml`.

---

## Scenario: the handler itself did not run

Symptoms: in the morning, TG has **neither** an alert nor a “Tests” section in the report.

```bash
# Check whether the suite ran
ssh <studio_ssh> "ls -la ~/health_test_suite.log"
ssh <studio_ssh> "tail -30 ~/health_test_suite.log"

# Check the launchd agent
ssh <studio_ssh> "launchctl list | grep test-suite"
```

If `launchctl` status does not show `com.larry.health.test-suite`:

```bash
ssh <studio_ssh> "launchctl unload ~/Library/LaunchAgents/com.larry.health.test-suite.plist; launchctl load ~/Library/LaunchAgents/com.larry.health.test-suite.plist"
```

If the suite hangs:

```bash
ssh <studio_ssh> "ps aux | grep run_full_test_suite"
ssh <studio_ssh> "kill -TERM <pid>"
```

`ExitTimeOut=21600` (6 hours) prevents a longer hang; after that, launchd
kills it automatically.

---

## Scenario: the Haiku API budget is exceeded

The monthly report `monthly_api_report.py` (on the 1st at 09:30) shows
spending for the previous month. If it is higher than expected:

1. Check the cache hit ratio: if it is low, the cache is not working.
2. Check the top 5 “expensive” tests: perhaps one test fails every night
   with a different `commit_hash`, so the cache does not help.
3. Decide: mark the test `flaky_today` (Level A) or
   fix it.

There is no hard limit currently (Q5 = C: “spend the first month with caching,
then decide”). If you decide to add one, add a check for the daily total
in `test_failure_handler.py`.

---

## Related documents

- `docs/explanation/test_architecture.md` — why this model.
- `docs/how-to/run_tests.md` — how to run tests.
- `TEST_ARCHITECTURE.md` §11–13 — orchestration in detail.
- `test_failure_handler.py` — implementation.
- `monthly_api_report.py` — spending report.
