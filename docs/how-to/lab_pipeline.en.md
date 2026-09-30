<!-- translation-of: docs/how-to/lab_pipeline.md sha256:8edb68d9d2ea -->
**English** · [Русский](lab_pipeline.md)

# How-to: lab recognition pipeline

Operational runbook. Run all commands on Studio through `/opt/homebrew/bin/python3.11`
(default python3=3.9 without dependencies). Canonical DB: `~/health/data/health.db`.
Key: `~/.health_secrets/anthropic_key`.

Pipeline: `lab_recognizer` (Opus+Sonnet, page by page) → `lab_oracles` (verification)
→ `lab_backfill` (writes to `lab_results_staging`) → `lab_review_sheet`/`lab_staging_summary`
→ `lab_promote` (atomic replacement of the canonical data). Canonical `lab_results` is NOT touched until promotion.

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
