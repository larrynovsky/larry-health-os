<!-- translation-of: tests/README.md sha256:1b22fe666caf -->
**English** · [Русский](README.md)

# tests/ — Health OS Test Suite

Architecture and strategy: see [TEST_ARCHITECTURE.md](../TEST_ARCHITECTURE.md).
Pragmatics and contracts: see [USE_CASES.md](../USE_CASES.md).
Work plan and statuses: kept in `ROADMAP.md` (in the private part of the project).

---

## Quick start

```bash
cd ~/health_scripts

# All unit tests (fast, <1s)
python3.11 -m pytest tests/unit/

# All integration (requires in-memory SQLite fixture)
python3.11 -m pytest tests/integration/

# Full run (no slow and no real Anthropic)
python3.11 -m pytest tests/

# Consistency tests only (RYW / staleness / tentative / ...)
python3.11 -m pytest -m consistency

# A specific UC only
python3.11 -m pytest tests/unit/test_uc_i_02_sec.py

# Including slow ones
python3.11 -m pytest -m "not requires_anthropic_key"

# With the real Anthropic API (paid — will spend tokens)
python3.11 -m pytest -m "requires_anthropic_key" --override-ini="addopts="
```

---

## Structure

```
tests/
├── conftest.py              ← root: sys.path setup + autoreset _time_inject
├── pytest.ini               (in the health_scripts/ root) — markers, addopts
├── unit/                    Layer 1: isolated modules (<100ms)
├── integration/             Layer 2: modules wired together via in-memory SQLite
├── e2e_mock/                Layer 3: end-to-end scenarios with external mocks
├── consistency/             Layer 4: RYW / staleness / tentative / single-primary / causal / eventual
├── snapshots/               Layer 5: JSON / structure baselines (not test code)
├── llm_judge/               Layer 6: D-level via haiku
├── charters/                Layer 7: human review routes (markdown, not code)
├── fixtures/                Shared fixture modules (db, time_travel, telegram, anthropic, ...)
├── plans/                   UC-J-02 test plans (markdown before writing the test)
└── reports/                 Nightly run artifacts (gitignored)
```

---

## Markers

| Marker | Use |
|---|---|
| `unit` | <100 ms, isolated module, dependencies mocked |
| `integration` | modules wired together, in-memory SQLite |
| `e2e_mock` | end-to-end scenario with external APIs mocked |
| `consistency` | distributed properties (RYW / tentative / etc.) |
| `snapshot` | comparison with a reference snapshot |
| `llm_judge` | D-level oracle via haiku |
| `slow` | >5 s — skipped in pre-commit, runs nightly |
| `requires_studio` | works only on the primary machine (infra_config.is_primary) — skipped on the MacBook |
| `requires_anthropic_key` | uses the real Anthropic API — skipped without a key and in pre-commit |

Usage: a decorator on the test or on the module.

```python
import pytest
pytestmark = pytest.mark.unit   # for the whole module

@pytest.mark.consistency
def test_ryw_checkin_to_gp(...):
    ...
```

---

## How to add a new test for a UC

1. **Make sure the UC has `confirmation=confirmed`** in `USE_CASES.md`.
   If it is still `proposed`, the test is not written (rule UC-J-02).

2. **Test plan first**, in `tests/plans/UC-X-NN.md`:
   - what exactly is checked (Then from the UC, B/E/C/D);
   - which mocks are needed;
   - which level (unit/integration/e2e/consistency);
   - blast radius (what breaks if the test is red).

3. **Plan confirmation** — the owner reads it and says «yes» or edits it.

4. **Only then the test code**, in the matching subdirectory:
   - file name: `test_uc_i_02_sec.py` (slug from the UC ID).
   - first line: `pytestmark = pytest.mark.<level>`.
   - docstring: a reference to the UC ID and short pragmatics.

5. **Run** locally on the MacBook → rsync to Studio → `pytest` on Studio.

6. **Update `uc_index.yaml`** (once T-5.1 is ready): add the test path to the UC record.

---

## Using the `_time_inject` fixture

`conftest.py` has an autouse fixture that calls `clear_test_clock()` after every test.
This means time frozen in one test does not leak into the next one.

```python
from datetime import date
from _time_inject import set_test_clock, get_today

def test_stale_labs_warning():
    set_test_clock("2026-05-08")
    # ... code that calls get_today() / get_now() — will see 2026-05-08
    assert get_today() == date(2026, 5, 8)
    # after the test, time is unfrozen automatically
```

Note: time is frozen only for code that **imports and uses**
`_time_inject.get_now()` / `get_today()`. A direct `datetime.now()` / `date.today()`
is not replaced. The Tier-1 files of Health OS (gp_agent, morning_report, integrity_tests,
…) have already been moved to injection (see ROADMAP T-pre.1).

---

## Single-primary guard in tests

`conftest.py` sets `os.environ.setdefault("ALLOW_WRITE_NONPRIMARY", "1")`.
This lets tests on the MacBook write to their own (in-memory or tmp) DB without
tripping the prod guard (`UC-I-07`).

On Studio the variable is not needed — it is the primary there anyway.

---

## Schedule

- **Locally (pre-commit)**: `pytest -m "not slow"` — must be green before a commit.
- **Studio post-commit**: after rsync — `bash run_checks.sh`, which runs
  smoke_tests + integrity_tests.
- **Studio nightly 00:00**: `com.larry.health.test-suite` — the full pyramid via
  `run_full_test_suite.sh`. See TEST_ARCHITECTURE.md §11.

---

## History

| Date | What |
|---|---|
| 2026-05-08 | T-0.1 + T-0.2: pytest setup, structure, conftest, root smoke + _time_inject unit (17 tests, 36/36 smoke_tests on Studio) |
