<!-- translation-of: docs/explanation/time-inject-contract.md sha256:5defe04b379b -->
**English** · [Русский](time-inject-contract.md)

# The single-time contract (`_time_inject`) — why and how

> Explanation (Diátaxis). Reference: the `_time_inject.py` docstring; enforcement:
> `time_contract_sensor.py` + the “single-time contract” check in `integrity_tests`.

## Problem

Production code read the wall clock directly (`datetime.now()`, `date.today()`,
`datetime.utcnow()`) in 152 places (78 files), 49 of them on recency paths
(freshness windows, age, “no older than N days”). Two consequences:

1. **Tests cannot be frozen.** Logic that reads the real `datetime.now()`
   cannot be controlled by `set_test_clock` → the test has to hardcode a date close to “now,”
   and **rots** when the calendar catches up.
2. **Split clocks.** Some code uses the seam (`get_now`), some uses the wall clock. The test
   freezes the seam, the production module lives on the wall clock → the test is green, production is not.

**The 2026-07-21 incident** involved both at once: `test_calendar_tenant_guard` hardcoded
an event on 2026-07-20; `calendar_client._read_cache` cut off `end < datetime.now()`
bypassing the seam. On the 20th the test was green; on the 21st it was red, even though the code had not changed.

## Rule (enforced)

All agent recency logic reads time ONLY through `_time_inject`:

| Before | After |
|---|---|
| `datetime.now(tz)` | `get_now(tz)` |
| `date.today()` | `get_today()` |
| `datetime.utcnow()` | `get_utcnow()`  ← **not** `get_now()`: that is local time, shifted by the time zone |

Tests control time through `set_test_clock(...)` / `clear_test_clock()`.

## Exceptions

- A line marked `# time-inject: ok` — a deliberate direct call (log, a
  `created_at` stamp, an entry point into an external system).
- The `if __name__ == "__main__":` block — a CLI is not required to use the seam.
- `_time_inject.py` itself (where real time lives).

## Sensor and ratchet

`time_contract_sensor.py` scans production for direct calls outside the seam. A hybrid ratchet:

- site in `time_contract_baseline.txt` (legacy) → **WARN** — clear them in batches;
- site absent from the baseline (new / in a clean file) → **FAIL** — stop the leak from day 1.

Once a site is migrated, remove its key from the baseline (or `reconcile`: baseline ∩ current scan).

Plus **production liveness**, `assert_clock_live()`: in a live run, `is_frozen()` must
be `False`. Otherwise, a leaked `set_test_clock` would freeze production time and **blind all
freshness sensors** (GP, backup-SLA, token-expiry, db_perms) — exactly the class these
sensors protect against.

## How to migrate a site (how-to)

1. Import: `from _time_inject import get_now, get_today` (or `get_utcnow`).
2. Replace the direct call using the table above, preserving time zone semantics.
3. Add a **two-sided boundary test**: freeze the clock at `порог−ε` (serves/fresh)
   and `порог+ε` (blocked/stale) with a FIXED data date. Example:
   `tests/unit/test_calendar_end_boundary.py`.
4. Remove the site from the baseline. Run `integrity_tests` — the “single-time contract” check
   should stay green (FAIL=0).

## Status (2026-07-21) — worklist CLOSED

All 152 direct calls (78 files) are closed: migrated to the seam OR marked
`# time-inject: ok` (development scripts, backup filename stamps). `time_contract_baseline.txt`
is **empty** → the ratchet is strict: any new direct call outside the seam → FAIL in `integrity_tests`.
Verified: full regression 2641 passed / 0 failed on the deployed version (Studio).

## Boundary-test coverage (2026-07-21)

Recency comparisons fall into CLASSES; each class has a proven
frozen-clock boundary test (RED verified). We do not multiply duplicates per instance —
that is a count, not a risk; the sensor catches a new bypass, and the general regression covers behavior.

| Comparison class | Example site | Two-sided test |
|---|---|---|
| event “ended” (end<now) | calendar_client | `test_calendar_end_boundary` |
| hourly window (age ≤ N h) | ecg_db:80 | `test_ecg_db::nonsinus_filter_and_window` (relative, both sides) |
| freshness ceiling (utc − N h) | patient_context:304 | `test_patient_context_clock::utc_floor` |
| daily window (today − N d) | gp_context:544, longitudinal:317, api_hae_ingest:37 | `test_gp_specialist_review_block`, `test_longitudinal_cutoff_clock`, `test_hae_ingest_freshness` |
| days-since / age in days | dashboard_filters:43, patient_context:236 | `test_dashboard_filters_clock`, `test_patient_context_clock` |
| age in years | patient_context:25 | `test_patient_context_clock::age_suffix` |
| “today/yesterday/N” label | patient_context:226 | `test_patient_context_clock::age_label` |

Instances of the same classes (≈40: “yesterday” report targets, parameterized `today−window_days`
for labs/literature/survivorship, age calculations, `today+N` deadlines) have low rot risk
(the window is relative to today and parameterized), covered by the general regression 2641/0 + the sensor.
Extract the threshold into a pure helper if the site is embedded (example: `longitudinal._recent_cutoff_iso`,
`patient_context._utc_floor_str`).
