<!-- translation-of: docs/how-to/add_domain.md sha256:8e70052114db -->
**English** · [Русский](add_domain.md)

# How to add a new recommendation domain

> **Document type:** How-to (Diataxis) — concrete steps for a real task.
> Recommendation system architecture: [recommendation_engine.md](../../recommendation_engine.md).
> DOMAIN_SIGNALS reference + thresholds: [ARCH_SNAPSHOT.md §ALERT LOGIC](../../ARCH_SNAPSHOT.md).

---

## Prerequisite

Before adding a domain, you need statistically supported correlations.
Requirements: Spearman |r| ≥ 0.15, n ≥ 50 pairs.

---

## Step 1 — Find correlations

Run on Studio:

```bash
ssh <studio_ssh>
cd ~/health_scripts
HEALTH_DATA_DIR=<tenant directory> /opt/homebrew/bin/python3.11 longitudinal_analysis.py
```

Or use the existing template `/tmp/stress_corr.py` (available on Studio).
Select signals with |r| ≥ 0.15 and n ≥ 50. Filter out noise.

**What to check:**
- Direction of the relationship: does a higher metric mean better or worse?
- If “higher = worse” → the metric goes into INVERTED (see step 3)
- Lag: does the signal affect the outcome today or tomorrow?

---

## Step 2 — Create a protocol in the database

```bash
ssh <studio_ssh>
cd ~/health_scripts
/opt/homebrew/bin/python3.11 -c "
import health_db
db = health_db.HealthDB()
db.save_protocol(
    title='Protocol name',
    domain='domain_name',
    behavior='Description of the key behaviour',
    status='active'
)
"
```

Keep the new protocol's `id` (needed for constraints).

---

## Step 3 — Add to DOMAIN_SIGNALS

On Studio, write the signals to `system_config` under the `domain_signals.domain_name` key using `health_db.upsert_config` (values from step 1):

```python
health_db.upsert_config("domain_signals.domain_name", value_json=[
    {"metric": "metric_name", "label": "outcome in Russian", "r": r_value, "threshold_pct": threshold_pct},
    # threshold_pct: trigger percentile (usually 25–30)
])
```

If the metric means “higher = worse,” on MacBook add it to the `INVERTED` set inside `get_metric_percentiles()` in `metrics_db.py`:

```python
INVERTED.add("metric_name")  # high = bad; keep the existing metrics
```

---

## Step 4 — Add _signal_reason

On MacBook, in `services/recommendations.py`, find the `_signal_reason()` function and add a branch:

```python
elif metric == "metric_name":
    return f"[explanation in Russian: what this deviation means]"
```

---

## Step 5 — Update ARCH_SNAPSHOT

In `ARCH_SNAPSHOT.md`, in the `## АЛЕРТ-ЛОГИКА` section, add the new domain to the DOMAIN_SIGNALS table and the active protocols table.

---

## Step 6 — Deploy to Studio

Commit code and documentation changes on MacBook. Work spanning more than one commit belongs in a tree created with `scripts/thread_start.sh <slug>`: [workflow](thread_worktree.md).

```bash
# After commits in the thread's tree, close it from the main MacBook copy:
cd ~/health_scripts
scripts/thread_finish.sh <slug>
```

Closing the thread runs the full suite on Studio, merges the branch, and invokes post-commit for `git push` and a restart. For a one-off edit directly in `main`, the commit itself triggers post-commit.

---

## Step 7 — Verify

```bash
# First make sure the commit under test is present on Studio (§12):
git fetch studio
git log studio/main
ssh <studio_ssh> "cd ~/health_scripts && /opt/homebrew/bin/python3.11 integrity_tests.py 2>&1 | tail -10"
```

Send a test `/report` in Telegram and verify that the new domain triggers.

---

## Add a constraint (optional)

If the domain cannot be used at a certain time of day, use
the `alerts` table (see `docs/explanation/survivorship_engine.md` —
`patient_constraints` was removed from the canonical Studio database in 2026-05):

```python
db.save_alert(
    type_="medication_interaction",  # or allergy|DNR|other
    message="Reason for the restriction (e.g.: vagal_activation prohibit evening)",
    severity="medium",
    source="manual",
)
```

`evaluate_domain_need(domain)` reads active alerts through
`get_active_constraints()` (a legacy signature that reads alerts with
backward-compatible aliases).
