<!-- translation-of: docs/how-to/run_analysis.md sha256:008df595b0fd -->
**English** · [Русский](run_analysis.md)

# How to run longitudinal analysis and read the results

> **Document type:** How-to (Diataxis).
> An explanation of longitudinal analysis: [docs/explanation/data_flow.md](../explanation/data_flow.md).
> Key findings (correlations): [ARCH_SNAPSHOT.md](../../ARCH_SNAPSHOT.md).

---

## When to run it

- Every 1–3 months or after a significant data change (new labs, a change in treatment phase)
- Before generating constitutions — they depend on fresh longitudinal analysis
- When integrity_tests warns that the analysis is outdated

---

## Run the analysis

The analysis takes 5–10 minutes; run it with nohup:

```bash
ssh <studio_ssh> \
  "nohup /opt/homebrew/bin/python3.11 ~/health_scripts/longitudinal_analysis.py \
   > /tmp/longitudinal.log 2>&1 &"

# Watch progress:
ssh <studio_ssh> "tail -f /tmp/longitudinal.log"
```

---

## What is created

| Artifact | Path | Contents |
|----------|------|------------|
| Excel | `outputs/longitudinal_analysis.xlsx` | 4 sheets (see below) |
| agent_report | `health.db → agent_reports` | `agent_type='longitudinal_analysis'`, the `raw_output` field |

**Excel sheets:**
1. Correlation matrix — Spearman r for 78 metric pairs (min_pairs=30)
2. Lagged correlations — predictors vs. targets, lags of 1/2/3 days
3. Labs vs. metrics — using a ±7d window
4. Recovery trajectory — the current 90d vs. the pre-illness baseline (% and slope)

Sync the Excel file to MacBook after the run:
```bash
rsync -av <studio_ssh>:~/health_scripts/outputs/longitudinal_analysis.xlsx \
      ~/health_scripts/outputs/
```

---

## How to read the correlation matrix

| r | Interpretation |
|---|---------------|
| ≥ 0.5 | Strong association — warrants an alert / protocol |
| 0.15–0.5 | Moderate — use with caution |
| < 0.15 | Weak — do not use in DOMAIN_SIGNALS |

Pay attention to the **direction**: if “more of the metric = a worse outcome”, it is INVERTED.

**Current findings** are in the latest analysis report (`agent_reports`,
`agent_type='longitudinal_analysis'`). The numbers belong in the tenant's data; they are not copied
into the documentation.

---

## How results get into the constitutions

After you run `longitudinal_analysis.py`, a record appears in `agent_reports`:
- `agent_type='longitudinal_analysis'`
- `raw_output` — a text block of trends and phases

`generate_constitutions.py` reads this `raw_output` through `_get_longitudinal_context()`.
So regenerate the constitutions after each new longitudinal analysis.

---

## If the analysis fails

```bash
# Check the log:
ssh <studio_ssh> "cat /tmp/longitudinal.log | grep -i error"

# Common cause: no data for the period
ssh <studio_ssh> \
  "python3.11 -c \"import health_db; db=health_db.HealthDB(); print(db.get_stats(90))\""
```
