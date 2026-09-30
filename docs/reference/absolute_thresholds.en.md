<!-- translation-of: docs/reference/absolute_thresholds.md sha256:586cbc21c7f1 -->
**English** · [Русский](absolute_thresholds.md)

# Reference: the `absolute_thresholds` table and reading thresholds

*Document type: reference. For consultation while working with code or the database.*
*Home of physiological norms under §9. The explanation of “why” is in `docs/explanation/section9_data_not_code.md`.*

---

## Purpose

`absolute_thresholds` is the sole runtime source of clinical thresholds for the body (readiness, blood pressure, sleep, variability and step multipliers). Code reads values from here, not from literals. Introduced in Sprint 2/Р-1 (2026-05-22); identity restructured on 2026-06-28 (workstream F §9).

## Schema

| Column | Type | Meaning |
|---|---|---|
| `id` | INTEGER PK | auto-increment |
| `metric` | TEXT | metric: `hrv`, `readiness`, `sleep_deep`, `bp_systolic`, ... |
| `direction` | TEXT | `floor` (triggers when value is below) or `ceiling` (above) |
| `value` | REAL | for `kind=absolute`, the threshold itself; for `kind=relative`, the multiplier |
| `reason_template` | TEXT | message template; `{val:.0f}` is substituted |
| `source` | TEXT | provenance: `threshold_analysis_*` (personal p10), `ESC_AHA_2023` (clinical), ... |
| `source_date` | TEXT | source date |
| `active` | INTEGER | 1 — taken into account |
| `kind` | TEXT | `absolute` (value is a threshold) or `relative` (value is a multiplier of `baseline`) |
| `baseline` | TEXT | for `relative`, the baseline: a metric (`avg7_hrv`, `avg30_hrv`) or **`ULN`/`LLN`** — the lab report reference for the `lab_results` row being assessed (CTCAE grades are expressed as multiples; since 2026-09-02) |
| `band_label` | TEXT | SEVERITY band: `very_low`/`low`/...; empty means a single threshold |
| `variant` | TEXT | PURPOSE: `food`/`deep`/`lifestyle`; empty means an unnamed threshold |
| `norm_kind` | TEXT | KIND of norm (CHECK): `reference_interval` · `decision_threshold` · `stratified_target` · `personal` · `unclassified` (no verdict issued). Introduced on 2026-09-02; explanation in `docs/explanation/norm_kinds.md` |
| `next_review` | TEXT | norm expiration date (ISO date). An active row with a past date fails the nightly check (`check_norm_review_overdue`). NULL means no date has been assigned |
| `stratum` | TEXT | for `stratified_target`: which profile fact selected the target (e.g., `onco_history`). NULL for the others |

**Identity:** `UNIQUE(metric, direction, band_label, variant)`. Multiple thresholds are allowed for one metric — with different severity (bands) and different purposes (variants). `source` is not part of the identity; it is only provenance.

## Three threshold forms

**Absolute** — a fixed value. `readiness floor 65`: triggers when readiness is below 65.

**Relative** — a baseline multiplier. `hrv floor, kind=relative, baseline=avg7_hrv, value=0.82`: triggers when variability is below 0.82 of the weekly average. Formula in code: `metric < baseline_value * value`.

**Bands** — multiple thresholds for one metric, distinguished by `band_label`. `sleep_deep floor very_low=30` and `low=50`: two severity bands.

## The seed → read loop (§9)

Code holds initial values only in `_seed_absolute_thresholds()` (health_db.py). On `init_db()`, they are written to the table (`INSERT OR IGNORE` on UNIQUE — idempotent). The production path reads from the table. There must be no threshold literal in production code.

## Reading (API in `rules_db.py`, re-exported through `health_db`)

```python
get_threshold(metric, direction, band_label="", variant="") -> float
```
Returns the active threshold's `value` by its full identity. Raises `KeyError` if the threshold has not been seeded — this is a failure of the seed loop, not a reason to silently substitute a default (§9: a loud failure > a silently incorrect threshold). For `relative`, it returns the multiplier; the baseline comes from the `baseline` column.

```python
get_absolute_thresholds(direction=None) -> list[dict]
```
All active thresholds (optionally filtered by `direction`). Row fields include `kind`, `baseline`, `band_label`, and `variant`. Used by the constitution generator and recommendation engine.

## Identity migration (2026-06-28)

The former `UNIQUE(metric, direction, source)` allowed only one threshold per metric. Rebuilt as `UNIQUE(metric, direction, band_label, variant)` through a rebuild (create a new table → `INSERT SELECT` → drop → rename), idempotent based on the presence of the old UNIQUE, preserving rows. The `kind`/`baseline`/`band_label`/`variant` columns were added with `ALTER` (old rows receive `kind=absolute`, empty band/variant).

## Norms from documents (2026-09-02, thread norm-from-documents)

Rows with `variant='safety_net'` for lab analytes are **derived** from the document snapshot `data/norm_docs/ctcae_lab_v5.0.json` (NCI CTCAE v5.0 xlsx → `norm_documents.import_ctcae`): grade 1 → `warn` (1.0 times the lab report reference), grade 2 → `urgent`, grade 3 → `critical`; grade 4 is not seeded. The former 64 manually entered rows are `variant='safety_net_legacy', active=0` (archived, not deleted). `safety_net._resolve_thresholds` combines the rows with the report's `ref_low/ref_high`; without a reference on the report, it uses the modal interval across ≥`norm.witness_min_docs` documents; without that, it raises a `norm_unresolved` alert rather than guessing. An analyte without a decision threshold is assessed as kind 1: outside the printed reference → `warn`. Tumor markers are absent from CTCAE → they have no manually entered urgent/critical thresholds; changes are assessed by RCV (`lab_trend_thresholds`, `pct_change` = one-sided RCV from the EFLM snapshot `data/norm_docs/eflm_bv.json`, `n_readings=2`). The document registry is the `norm_documents` table (id, version, issued, url, sha256, next_check).

## Norm provenance (2026-09-02)

`source` + `source_date` is the named, dated document from which the number comes; `norm_kind` specifies which of the four medical objects it represents; `next_review` specifies when to recheck it. The machine sets `norm_kind` only from unambiguous provenance (`health_db._NORM_KIND_BY_SOURCE`: `p10/p90_personal`, `owner_personal`, `threshold_analysis_*` → `personal`; `ESC_AHA_*` → `decision_threshold`); everything else remains `unclassified` and is counted nightly (`check_norm_kind_unclassified`). The machine never sets `next_review`.

Sensors (`integrity_tests`): `check_norm_review_overdue` (failure reported to the owner) · `check_norm_vs_lab_reference` — a norm of kind `reference_interval` against the modal interval printed by the lab in ≥`norm.witness_min_docs` documents; deviation >`norm.witness_max_dev` → failure; for `unclassified`, prints a suggested kind · `check_norm_coverage` — an analyte with ≥`norm.coverage_min_n` measurements and no norm in any table by `metric`+`direction`, nor in `lab_refs` → WARN · `check_threshold_names_reach_data` — the threshold name is canonical and matches data · `check_threshold_source_is_document` — each active `safety_net` row is derived from a document snapshot (`norm_documents`), and every entry in the `system_config.lab_refs` cache has `source='bank_modal'` and `n_docs ≥ norm.witness_min_docs` (a number without a document → failure). Special case: Glucose ceilings (CTCAE v6 — grades based on fasting glucose) are active only when `patient_profile.routine.fasting_labs` ≠ false. Configuration is in `system_config` (`norm.*`, mirrored in `integrity_tests._NORM_FALLBACK`).

## Related

- `docs/explanation/section9_data_not_code.md` — why norms are not in code.
- `docs/how-to/add_clinical_threshold.md` — a recipe for adding a threshold.
- `docs/explanation/norm_kinds.md` — four kinds of norms and why a number has an expiration date.
- `lab_monitoring_schedule` — a related home (lab freshness, the same source-priority pattern).
