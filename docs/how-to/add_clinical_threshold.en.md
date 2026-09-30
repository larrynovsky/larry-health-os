<!-- translation-of: docs/how-to/add_clinical_threshold.md sha256:b1e3cef2eb7a -->
**English** · [Русский](add_clinical_threshold.md)

# How to add or change a clinical threshold

*Document type: how-to. A step-by-step recipe for a specific task.*
*When to use: a new clinical norm is available, an existing threshold needs to move from code into `absolute_thresholds`, or an existing value needs to change.*

Context and rationale — `docs/explanation/section9_data_not_code.md`. Schema — `docs/reference/absolute_thresholds.md`.

---

## Change the value of an existing threshold

The threshold is already in the table → change only data, with no code changes:

```sql
UPDATE absolute_thresholds
   SET value = <new value>, source = '<named document>', source_date = '<document date>',
       norm_kind = '<reference_interval|decision_threshold|stratified_target|personal>',
       next_review = '<YYYY-MM-DD — when to re-check>'
 WHERE metric = '<canonical name>' AND direction = '<floor|ceiling>' AND band_label = '<...>' AND variant = '<...>';
```

Since 2026-09-02, a norm row must carry a **kind** and an **expiration date** (`docs/explanation/norm_kinds.md`).
`unclassified` is allowed only for migration; the person modifying the row issues a verdict. `metric` is the canonical name
from `lab_canon.normalize` (otherwise the threshold will not match the data — `check_threshold_names_reach_data`). An overdue
`next_review` fails the nightly check; this is the mechanism for “update regularly.”

This does not affect the code — it reads the value from the table. Make the change on Studio (the canonical node), not in code.

## Add a lab decision threshold — add a DOCUMENT, not a number (since 2026-09-02)

Lab thresholds for `safety_net` are not entered manually: they are derived from a document. A new analyte from CTCAE requires
one row in `data/norm_docs/ctcae_lab_terms.json` (term → canonical name, direction, unit), then
`python3 -c "import norm_documents as nd; nd.snapshot_ctcae()"` and a commit of the snapshot together with the mapping:
the coherence test `test_snapshot_equals_import_of_document` prevents divergence. A new CTCAE version requires
a file in `data/norm_docs/`, a term mapping for that version, an entry in `norm_documents.CTCAE_FILES` and `DOCUMENTS`, the
`CTCAE_CURRENT` switch, both snapshots (`snapshot_ctcae(id)`), and `diff_ctcae(old, new)` in the commit message; the seed archives the old rows automatically. An analyte
that does not appear in any document is kind 1: it is assessed automatically against the reference interval on the lab report, so no threshold
needs to be added. A personal threshold (such as Amylase) is the only case for a manual row, with
a `source` of the form `*_owner_personal_*` and `norm_kind='personal'`.

## Move a new threshold out of code (daily metrics; for lab thresholds, see above)

### Step 1. Seed it in `_seed_absolute_thresholds()` (health_db.py)

Add a row to the `seeds` list. The fields depend on the form:

- **Absolute:** `(metric, direction, value, reason_template, source, source_date)` — `kind` defaults to `absolute`.
- **Relative** (a baseline multiplier, e.g., `hrv < avg7 * 0.82`): set `kind='relative'`, `baseline='avg7_hrv'`, `value=0.82`, and `variant` if there are several for the metric (`food`/`deep`).
- **Band** (multiple severity thresholds): one row per band with a different `band_label` (`very_low`/`low`).

The seed is idempotent (`INSERT OR IGNORE`). The initial value exists in code ONLY here — this is a legitimate seed cycle under §9.

### Step 2. Rewrite the production code to read the value

Before (the literal violates §9):
```python
low_ready = readiness < 65
```
After (read from its home):
```python
import health_db as _db
low_ready = readiness < _db.get_threshold("readiness", "floor")
```

For a relative threshold, apply the multiplier to the baseline:
```python
mult = _db.get_threshold("hrv", "floor", variant="deep")   # kind=relative
low_hrv = hrv < avg7_hrv * mult
```

`get_threshold` raises `KeyError` if the threshold has not been seeded — this is intentional: a loud failure is better than a silently incorrect threshold. Do not wrap it in `try/except` with a default.

### Step 3. Remove the literal

After step 2, the number must no longer be in the code — otherwise you get split-brain (the value in the two places will diverge).

### Step 4. Add a check against split-brain

A test changes the threshold in the table and checks that behavior changes (with a hardcoded number, the test outcome would not change). See `tests/unit/test_threshold_from_table.py` for an example.

### Step 5. If a test needs a DIFFERENT threshold value, override it instead of inserting

The `db` fixture from workstream F (2026-06-28) **seeds the canonical `absolute_thresholds` itself**
(using the same `_seed_absolute_thresholds()` as the production `init_db`). Therefore, code that reads
a threshold through `get_threshold` no longer fails with `KeyError` on its own in tests.

A test that needs a DIFFERENT value must **override** the row through
`INSERT OR REPLACE` (UPDATE), rather than `INSERT` — otherwise there is a collision on
`UNIQUE(metric, direction, band_label, variant)`. See `_seed_floor` in
`test_threshold_from_table.py` (uses `INSERT OR REPLACE`) for an example.

## Issue a verdict on the norm kind (remaining `unclassified` rows)

The nightly run prints `[health] норм без вида: N` and suggestions from the witness: `HGB floor warn 12 vs
лаборатория 13.5–17.5 (11%) → совпадает: кандидат reference_interval`. There is one question for each row:
**is this a statistic from healthy people (a reference interval) or a number derived from outcomes (a decision threshold)?** If it matches the lab interval →
it is almost always a reference interval. If it differs (Glucose 126 versus 70–100) → it is a decision threshold; name the document
(`source='ADA_2025'`). If it is personal (`Amylase`) → `personal`. The verdict is `UPDATE ... SET norm_kind, source,
source_date, next_review` as above; the machine does not write it on its own.

## Who is the oracle

Who may change the value depends on its class (see the explanation of §9): physiological norms — the patient (personal) or the literature (population); methodological rubrics — the methodology's author; analysis parameters — the engineer. The source (`source`) records whose decision it is.

## Related

- `docs/reference/absolute_thresholds.md` — schema and API.
- `docs/explanation/section9_data_not_code.md` — rationale.
