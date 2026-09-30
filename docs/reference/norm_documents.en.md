<!-- translation-of: docs/reference/norm_documents.md sha256:22322707dc23 -->
**English** · [Русский](norm_documents.md)

# Reference: norm document registry and snapshots (`norm_documents`)

*Document type: reference. Thread `norm-from-documents`, 2026-09-02. Explanation — `docs/explanation/norm_kinds.md`; source map — `docs/reference/norm_sources.md`.*

---

## `norm_documents` registry (table) and `norm_documents.DOCUMENTS` (locations)

| Column | Meaning |
|---|---|
| `id` | `CTCAE_v6.0` (**current**, `norm_documents.CTCAE_CURRENT`), `CTCAE_v5.0` (previous, for the diff), `EFLM_BV`; tenant documents (surveillance guidelines for their episodes, their doctor's decisions) are in `data/norm_docs/documents_tenant.json` (private area), appended to the registry at import |
| `version`, `issued`, `url` | version, issue date, location |
| `local` | local copy in `data/norm_docs/` (for CTCAE xlsx and the EFLM snapshot); `sha256` is its checksum |
| `kind` | which kind of norm it provides: `decision_threshold` · `personal` (RCV) · `schedule` (surveillance cadence) |
| `applies_to` | `oncology` · `*` · tenant episode class (from `schedules.json`) — matched against episodes (`episodes_of_care`) |
| `next_check_days`, `next_check`, `last_check`, `remote_state` | version check cadence; sensor `check_norm_documents_fresh` |

## Snapshots in the repo

`data/norm_docs/ctcae_v6.0_2026-01-26.xlsx` (NCIt export, long layout: one row per “Grade N Term”) and `ctcae_v5.0_2017-11-27.xlsx` (“Clean Copy”, wide layout) are documents; `norm_documents._wide_table` normalizes both layouts into one. `ctcae_lab_terms_v6.json` / `ctcae_lab_terms.json` are term maps by version (data): term → canonical name, direction, family (`multiple` — multiples of ULN/LLN; `absolute` — numbers in `unit_pick`, `scale` to canonical units). `ctcae_lab_v6.0.json` is the **current snapshot**, read by the seed `health_db._seed_safety_lab_thresholds` and fallback `safety_net._FALLBACK_LAB`; `ctcae_lab_v5.0.json` is the previous one, for `diff_ctcae`. Version change: new file + map + entry in `CTCAE_FILES`/`DOCUMENTS` + `CTCAE_CURRENT` + both snapshots + diff in the commit; the seed moves rows from the previous version to `variant='superseded:<id>'`, `active=0`.

v5→v6 diff (2026-09-02): neutrophils — all grades one step lower (urgent 1.5→1.0, critical 1.0→0.5 ×10³/µL, warn became an absolute 1.5); platelets — term `Thrombocytopenia`, G3 <50–10; lipase critical 2→3×ULN; hyperglycemia became numeric (fasting: warn >ULN, urgent >160, critical >250 mg/dL); sodium urgent 129→130; ALP (one step “>Baseline and ULN”), CPK (term disappeared), and lymphopenia (“Present”) were removed from the map — ALP and lymphocytes are assessed as kind 1. `eflm_bv.json` is an API snapshot: all EFLM measurands with a CV_I meta-estimate whose display_name the canonical vocabulary (`lab_canon`) normalizes to a canonical name (a rule instead of the manual `eflm_terms.json` map, owner's decision on 2026-09-25; British spellings — `lab_canon._REFERENCE_SPELLINGS`; two measurands mapping to one canonical name — neither is taken, warning) (`cvi_median/lower/upper/n`, `updated_at`).

## CTCAE derivation rules

grade 1 → `warn`, grade 2 → `urgent`, grade 3 → `critical`; grade 4 is not seeded. The first numeric token in the grade is the boundary; `LLN`/`ULN` → `kind='relative', value=1.0`. A symptom-dependent grade with the same number (Hypokalemia G2) is not a threshold. The “if baseline was abnormal” branch is not supported (“baseline normal” is used). One unit system — conventional, as on the report. Not from CTCAE: Amylase (the owner's personal threshold), tumor markers (absent from CTCAE); in v6, also ALP, CPK, and lymphocytes (see the diff).

## RCV

`rcv_pct(metric, cva_pct=None)` = 1.65·√2·√(CV_A² + CV_I²), where CV_I is the EFLM median; default CV_A is 0.5·CV_I (desirable APS). The `system_config` key `norm.cva_source` is reserved for the laboratory's CV_A. As of 2026-09-02: CEA 17.8%, CA19-9 11.3%, HGB 7.0%, MCV 2.0%, Albumin 6.5%.

## Rule for confirming a rise (`schedules.json::rules`, EGTM 2014)

`norm_documents.confirmation_metrics()` reads `data/norm_docs/schedules.json::rules[id=confirm_rise_before_action]` — a list of canonical names (CEA, CA19-9) with a verbatim source quote ([DOI 10.1002/ijc.28384](https://doi.org/10.1002/ijc.28384)): *“Any increase in levels must be confirmed with a second sample prior to undertaking further investigations”*. The reader is `safety_net.check_lab_trends`: the latest pair exceeding RCV produces the threshold's severity (urgent) only if both the previous and the latest points exceed the threshold relative to the point before those two (the rise persists across two samples); otherwise, warn with “confirm with a repeat sample.” Metrics outside the list are assessed using one pair. If the rule cannot be read → assess using one pair and send a fallback alert (`lab_trend_confirm_rule`). Why this instead of a magnitude floor: `docs/explanation/norm_kinds.md`.

## Magnitude floor for an RCV trend (`lab_trend_thresholds.near_boundary_share`)

The owner's word on 2026-09-03, not a document: `near_boundary_share=0.2`, `near_boundary_source='owner_word:2026-09-03'`
(the seed `health_db._seed_lab_trend_thresholds` fills only NULL; fallback `health_db.LAB_TREND_NEAR_BOUNDARY_SHARE`).
`safety_net.check_lab_trends` assesses RCV only if the latest point ≥ (1 − share)·`ref_high` from its report — and only
when `ref_low` is 0/empty (tumor markers); two-sided intervals and points without a reference remain as before. To set another number
or remove the floor for an analyte: `UPDATE lab_trend_thresholds SET near_boundary_share=?, near_boundary_source='owner_word:<дата>' WHERE metric=?`.

## Kind 1 reference — a lab report, not a catalog (`lab_refs`)

The kind 1 document is the lab report itself (`lab_results.ref_low/ref_high`); the reference belongs to the method. `system_config.lab_refs` is a **cache**, not a seed: `health_db._refresh_lab_refs()`, on every `init_db` (after `lab_results` migrations), writes `labs_db.compute_bank_refs(min_docs)` — the mode of the printed interval across ≥`norm.witness_min_docs` distinct documents, format `{canon: [lo, hi, unit, n_docs]}`, `source='bank_modal'`; `lab_refs_meta` is the date and n. `get_lab_refs()` returns `(lo, hi, unit)` or `{}`; there is no literal fallback. Readers' order: row reference → mode → “reference not established.” `check_threshold_source_is_document` fails on a cache entry with another source or n_docs < min_docs. Limitation: the mode is the interval from the most frequent lab; with multiple labs, this is a majority, not truth.

## Updating

`python3 -c "import norm_documents as nd; nd.snapshot_ctcae(); nd.fetch_eflm()"` on a machine with network access → snapshot diff in the commit → `init_db` on Studio rewrites rows (`INSERT OR IGNORE` + trend UPDATE). Coherence: `test_snapshot_equals_import_of_document`, `test_fallback_lab_equals_seed`.
