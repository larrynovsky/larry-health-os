<!-- translation-of: docs/reference/specialized_lab_results.md sha256:1dd450e34af2 -->
**English** · [Русский](specialized_lab_results.md)

# Reference: specialized_lab_results

Specialized panels beyond blood biochemistry (BL-LAB-CANON-2, 2026-07-04). One long-format
table with a `panel_type` discriminator for panels that do not fit the
`lab_results` model (blood): microbiome, autoantibodies, metabolomics, fatty acids,
trace elements, amino acids, electrophoresis, stool markers.

## Schema

| column | type | meaning |
|---|---|---|
| `id` | INTEGER PK | |
| `date` | TEXT | collection date (ISO) |
| `source` | TEXT | provenance `doc:<file>` |
| `panel_type` | TEXT | discriminator (see below) |
| `specimen` | TEXT | blood / stool / urine / null |
| `analyte_raw` | TEXT | name as recognized |
| `analyte_canonical` | TEXT | canonical name (nullable) |
| `value` | REAL | numeric value |
| `value_text` | TEXT | nonnumeric (pos/neg/titer) |
| `unit`, `ref_low`, `ref_high`, `flag` | | unit, reference, flag |
| `method` | TEXT | GC-MS / ELISA / flow cytometry / … |

Indexes: `date`, `panel_type`, UNIQUE(`date`,`analyte_raw`,`source`,`panel_type`).

## panel_type

`microbiome`, `autoantibodies`, `metabolomics`, `fatty_acids`, `trace_elements`,
`amino_acids`, `electrophoresis`, `stool_markers`, `gastro_markers`, `immunophenotype`,
`urinalysis_sediment`, `other`.

## Single writer (Primary-Based Protocol)

ONLY `lab_specialized.promote_specialized(run_id, execute)` writes to the table
(Tanenbaum §7.5, like `lab_promote` for `lab_results`). The classifier
`lab_specialized._classify(raw_name, panel, unit)` routes a staging row:
`blood` → `lab_results` (skipped here), otherwise → specialized with `panel_type`.
Idempotency: DELETE by `source` before insertion (reclassification does not create duplicates);
rows without a value are skipped.

## Sensor

`integrity_tests.check_specialized_lab_health` — every row must have
`panel_type` and either `value` or `value_text` (otherwise it is empty junk). triage→Telegram.

## Consumers

`labs_db.build_specialized_context()` returns a SUMMARY (counts by `panel_type`
and results outside reference intervals) with an EXPLICIT age label.
Hypothetical risk: without a date, an old result may be interpreted as current.
The summary feeds `monthly_consilium` and `generate_constitutions`; summarization
keeps the context bounded even when the result set is large.

## Related

- Blood model: `lab_results`, writer `lab_promote` (intent: `subsystem_intent.yaml::lab_recognizer`).
- Low-confidence staging triage: `lab_triage.triage` (rejected/review/gold).
- Value-conflict review: `health/lab_conflicts_review_2026-07-05.md` (iCloud).
