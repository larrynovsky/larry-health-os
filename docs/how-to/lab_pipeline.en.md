<!-- translation-of: docs/how-to/lab_pipeline.md sha256:10b2700946b3 -->
**English** · [Русский](lab_pipeline.md)

# How-to: lab recognition pipeline

Operational runbook. Run all commands on Studio through `/opt/homebrew/bin/python3.11`
(default python3=3.9 without dependencies). Canonical DB: `~/health/data/health.db`.
Key: `~/.health_secrets/anthropic_key`.

Pipeline: `lab_recognizer` (Opus+Sonnet, page by page) → `lab_oracles` (verification)
→ `lab_backfill` (writes to `lab_results_staging`) → `lab_review_sheet`/`lab_staging_summary`
→ `lab_promote` (atomic replacement of the canonical data). Canonical `lab_results` is NOT touched until promotion.

During automatic intake, `lab_intake_watcher._settle_outcomes` decides the outcome
after both readers finish. Zero rows alone is not a fault: a doctor's letter may
legitimately enter the medical record without lab values. The narrow discrepancy
is a `.norows` marker together with `doc_outcome(status="imported", kind="lab_result")`.
Only this creates the new `notify.fault` and a `type=question` task asking for values
as text or a closer photo. The date comes from `doc["date"]`; an unknown date is
explicitly identified. The outcome says that values are missing and a separate
question will follow; the existing bot queue delivers it subject to its limit.
`imported/encounter` does not trigger this escalation; `failed` together with
`.norows` retains the existing fault without a new question.
Oracle: `test_zero_rows_queues_question_and_fault_once_without_db`.

Repeated passes and restarts use the existing `outcome_told`; there is no extra
fingerprint list. Explicit resubmission clears that receipt under the existing
contract. The receipt is saved before external actions: a crash after saving can
leave the outcome or question undelivered. This is an existing mechanism limitation,
not an exactly-once delivery guarantee. Already recorded outcomes are not reprocessed
automatically.

## 1. Rerun recognition on the corpus

```bash
# all lab documents from the JSON layer
python3.11 lab_backfill.py --run-id <run> --max 25
# a single document outside the layer (clinic-named)
python3.11 lab_backfill.py --run-id <run> --doc-path "<folder>/<document>.pdf" --date YYYY-MM-DD
```
Takes a while (page by page, a 70-page document takes ~30 minutes). Run in the background: `nohup … &`.
Idempotent by (run_id, source_file).

## 2. View the result

```bash
python3.11 lab_staging_summary.py --run-id <run> [--doc "full panel"]
# dates × types, dedup, date_source (read/inherited/fallback)
python3.11 lab_backfill_report.py --run-id <run>          # quality report
python3.11 lab_review_sheet.py --run-id <run> --out <path>.html  # manual review sheet
```
In the review sheet: 🔴 model disagreement, 🟡 oracle, 🔵 single pass. Mark errors
with the reject checkbox → “Export reject list” → save the JSON.

## 3. Promote to canonical data (with gates)

```bash
# DRY-RUN (default, writes nothing): shows the diff and Gate-2 (completeness)
python3.11 lab_promote.py --run-id <run> [--reject-file rej.json]
# REAL write (snapshot + atomic transaction)
python3.11 lab_promote.py --run-id <run> --reject-file rej.json --execute
```
Rule: REPLACE canonical lab rows, DELETE clinic duplicates, DO NOT TOUCH
`instrument:*`. Blood → `lab_results`; immunoreactivity/microbiome do not.
`--execute` REFUSES if Gate-2 finds old (date, analyte) pairs not covered by the new data
(potential loss): first reconcile the names in `lab_canon.py` or capture the missing document data.

## 4. Rollback

`--execute` creates a snapshot, `health.db.presnap_<ts>.db`, next to the DB. Rollback:
```bash
launchctl stop com.larry.health.bot   # stop the readers
cp ~/health/data/health.db.presnap_<ts>.db ~/health/data/health.db
launchctl start com.larry.health.bot
```
Check integrity: `python3.11 -c "import health_db,sqlite3; sqlite3.connect(health_db.DB_PATH).execute('PRAGMA integrity_check')"`.

## 5. After promotion

The canonical data changed (corrected values, new analytes) → regenerate constitutions
and revalidate correlations/alerts against the new data (old conclusions based on the previous incorrect data
are invalid). See the plan `iCloud/health/lab_pipeline_workplan_2026-06-30.md`, phase E.
