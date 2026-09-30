<!-- translation-of: docs/how-to/add_instrument.md sha256:e458ec203774 -->
**English** · [Русский](add_instrument.md)

# How to add a new questionnaire

> **Type:** How-to (Diataxis).
> Architecture: [docs/explanation/survivorship_engine.md](../explanation/survivorship_engine.md).
> Catalog: `~/health/data/instruments/*.json`.

## When to use

When you need another validated questionnaire (for example, PHQ-9 for depression or GAD-7 for anxiety) in addition to those already connected.

## Prerequisite

- Validated item wording as a PDF / DOI / published table.
- Scoring rules (formula, range).
- Completion cadence (90 / 30 / 14 days).

## Steps

### 1. Create `~/health/data/instruments/<id>.json`

The structure matches `isi.json`/`mfsi_sf.json`:

```json
{
  "id": "phq9",
  "name": "PHQ-9 — Patient Health Questionnaire",
  "version": "v1.0_validated_ru",
  "source": "Kroenke & Spitzer 2001 — sourced PDF (DOI: 10.1046/j.1525-1497.2001.016009606.x)",
  "validation_status": "validated_ru",
  "cadence_days": 30,
  "cadence_offset_days": 15,
  "recall_period": "past_2_weeks",
  "response_scale": {"min": 0, "max": 3,
    "labels": {"0": "not at all", "1": "several days", "2": "more than half the days", "3": "nearly every day"}},
  "items": [
    {"id": "q1", "text": "Little interest or pleasure in doing things", "subscale": "total"},
    ...
  ],
  "subscales": [{"id": "total", "items": ["q1", "q2", ...], "direction": "symptom_higher_worse"}],
  "scoring_formula": "total = sum(items); 0-4 minimal, 5-9 mild, 10-14 moderate, 15-19 moderately severe, 20-27 severe",
  "thresholds": {"mild": 5, "moderate": 10, "severe": 20},
  "shadow_rules": [
    {"item_or_subscale": "total",
     "proxies": ["stress_high_min", "sleep_score"],
     "min_window_days": 7, "persistence_days": 3,
     "divergence_threshold": 0.5,
     "note": "depression vs stress/sleep proxies"}
  ],
  "wording_version_hash": "phq9_v1_2026-05-18"
}
```

### 2. Run a sanity check through Python

```bash
ssh <studio_ssh> "cd ~/health_scripts && /opt/homebrew/bin/python3.11 -c '
import assessment_dialog as ad
ins = ad._load_instrument_by_source_id(\"phq9\")
print(\"loaded:\", ins[\"name\"], len(ins[\"items\"]), \"items\")
'"
```

### 3. Let assessment_scheduler pick it up

The next cron run (daily at 03:30) will create the `assessment:phq9` task with a deadline of `today + cadence_offset_days`. Or run it manually for an immediate smoke check:

```bash
ssh <studio_ssh> "cd ~/health_scripts && /opt/homebrew/bin/python3.11 assessment_scheduler.py"
```

### 4. Complete it through Telegram

You will receive a message with inline buttons — click “📋 Fill out.” The dialog will go through the items automatically.

### 5. Verify that the data arrived

```sql
SELECT date, test_name, value FROM lab_results
WHERE source = 'instrument:phq9'
ORDER BY date DESC;
```

Also verify that the guard `check_assessments_freshness()` now knows about phq9 (it automatically iterates over the catalog).

## What you do NOT need to do

- Do not patch `assessment_scheduler.py`, `assessment_dialog.py`, or `assessment_importer.py` — they iterate over the catalog themselves.
- Do not patch `survivorship_analyzer.py` — it picks up `shadow_rules` from the JSON.
- Do not patch `integrity_tests.py` — `check_assessments_freshness()` iterates over the catalog.

## Antipatterns

- **Paraphrasing items without specifying the status** → trend integrity breaks after 90 days. Always specify `validation_status` and `wording_version_hash`.
- **shadow_rules with a single proxy and threshold=0** → false positives. At minimum, use `min_window_days: 7` and `persistence_days: 3`.
- **Not updating wording_version_hash when editing the text** → `assessment_importer` silently accepts old responses as valid.
