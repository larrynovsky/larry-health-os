<!-- translation-of: docs/explanation/wave8_observations.md sha256:a704e23f8be3 -->
**English** · [Русский](wave8_observations.md)

# Wave 8 — Data sources & cascades (observations)

A draft file for accumulating observations during Phase 3 operation. After Phase 3 closes → conversion into a full `wave8_data_sources_plan.md`.

Contract: write briefly, one observation per item. Do not try to design the solution — record the observation and context.

## 1. Data from external sources (not entered manually)

User observations, 2026-05-15:

### 1.1 Weight → Apple Health

- `patient_profile.identity.weight_kg` is currently editable through inline editing
- Correct behavior: comes from `daily_metrics.weight` through Apple Health import
- In the dashboard it should be **read-only** with an “Apple Health” label and an “edit in Apple Health” hover tooltip
- Open question: show a 30/90-day trend next to the value?

**Discovery 2026-05-16:** `SELECT updated_by, COUNT(*) FROM patient_profile GROUP BY updated_by` showed that Apple Health **does not write to `patient_profile`** directly. Sources: manual, dashboard, patch_personal_data.py, rule9-audit. This means the read-only lock is not “protection against overwriting” but **a new integration** syncing `daily_metrics.weight` → `patient_profile.identity.weight_kg`. A separate Wave 8 subtask.

### 1.2 (reserved) What other fields come from Apple Health?

Candidates to check — exploration of `daily_metrics` needed:
- `heart_rate_*` (resting, hrv)
- `sleep_*` (total, deep, rem, efficiency)
- `steps`, `active_energy`
- `body_fat_percentage` (if there is a smart scale)
- `bp_systolic`, `bp_diastolic` (if measured)

For each, check whether a corresponding `patient_profile.X` duplicates it.

### 1.3 (reserved) What else comes from other sources?

- Oura: sleep/HRV/recovery — should be read-only in the dashboard
- Labs → `lab_results` — already a separate page, not the profile
- Genome → `genetic_variants` — read-only by design

## 2. Cascades on actions

User observations, 2026-05-15:

### 2.1 Confirm hypothesis → create a task from `payload.test` ✅ CLOSED

Implemented in Phase 3.3C-extend (commit 70e92ef, 2026-05-16):
- On confirm with a nonempty `payload.test` → INSERT into tasks
- source=dashboard_confirm, type=followup, priority=medium, status=open
- text trimmed to 500 characters
- audit through `_log_edit("tasks", task_id, "create_from_hypothesis_confirm", ...)`

Remaining in Wave 8: `payload.prediction` could also create a task with `type=check` — not implemented.

### 2.2 (reserved) Reject hypothesis → what should happen?

- Currently reject = active=0 + resolution_type=rejected
- Question: create anything downstream? Perhaps a patterns row saying “this relationship was not confirmed”? Or nothing, and that is normal?

### 2.3 (reserved) Complete experiment → update linked hypothesis

- If an experiment has `linked_hypothesis_id`, completing it with a `result` should append to `hypothesis.payload.experiment_results[]`
- This gives CBCR the context “hypothesis tested by experiment X, result Y”
- Currently entirely absent

### 2.4 (reserved) Retire protocol → what?

- Perhaps: add an entry to `patterns`, “protocol X had no effect over N days”
- Or: nothing; retire simply means “stopped the practice”

## 3. Fields from physician documents

User observations, 2026-05-15:

### 3.1 `consultations.key_findings` — extraction from source_file

- Currently edited manually through inline editing
- Correct behavior: parsed from `source_file` (physician's PDF) through an LLM pipeline or regex extraction
- Read-only in the dashboard, with a “source document” link
- Marked `key_findings_source='extracted'` vs. `'manual'`

### 3.2 (reserved) What other fields come from documents?

Need to understand what `source_file` usually contains:
- Diagnoses (ICD codes?)
- Prescriptions (drugs, doses)
- Examination results
- Next visit date

Not all are needed in the dashboard, but they could enter `patient_profile.medical.*` in structured form.

## 4. Schema extensions

Schema design questions (without a personal profile inventory):

### 4.1 `profile.identity.routine.*` — few substantive fields

- Independently invented discussion key: `routine.example_sketching` (a training example of regular sketching, not a profile record)
- What else should be there? Explore existing keys + brainstorm:
  - `routine.coffee` (how many cups, when)
  - `routine.exercise` (type, frequency)
  - `routine.sleep_window` (bedtime/wake time)
  - `routine.intermittent_fasting` (windows)
  - `routine.supplements` (current stack)
  - `routine.meditation` / `routine.breathwork`
  - `routine.travel_pattern` (how often, how long)
- Open question: routine vs. experiments. Where is the boundary? Routine = “ongoing practice,” experiment = “temporary test with metrics”?

### 4.2 (reserved) What other profile categories are underdeveloped?

Query `SELECT category, COUNT(*) FROM patient_profile GROUP BY category`. Compare against the target map.

## 5. Antipatterns found

Observations, 2026-05-15:

### 5.1 Inline editing for derived values = an error

- If a value comes from an external source, editing it through the UI creates **divergence**: the dashboard shows X, the source shows Y, and agents read one or the other
- Protection: a `value_source` column or a unique table `derived_profile_fields` listing keys for which editing is prohibited

### 5.2 (reserved)

## 6. Wishlist / Wild ideas

### 6.1 Trend visualization next to the value

- On the `patient_profile.identity.weight_kg = <N>` card, show a sparkline for 90 days
- Requires a JOIN with `daily_metrics`
- Cheap if the sparkline is ascii (▁▂▃▄▅▆▇), expensive if canvas/SVG

### 6.2 (reserved)

## Conversion into a plan

When Phase 3 closes:

1. Each section above → a separate Wave 8 stage (W8-A, W8-B, ...)
2. Refine the time estimate
3. Decide the sequence — what blocks what
4. Move to `docs/explanation/wave8_data_sources_plan.md`
5. ROADMAP §19

For now, a file in flight. Add to it as observations arise during Phase 3 operation.
