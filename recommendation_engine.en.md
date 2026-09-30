<!-- translation-of: recommendation_engine.md sha256:3113b0af3d8f -->
**English** · [Русский](recommendation_engine.md)

# Recommendation Engine — evidence base and architecture

> The proactive recommendations module: it looks at yesterday's/today's data
> and decides whether an intervention is needed — and which one.

---

## Why this exists

Biometrics accumulate, but by themselves they do not say what to do.
The module's job is to turn a signal («there was a lot of stress yesterday») into
a concrete action («do a breathing practice today»), if there is
enough ground for it in the person's own history.

Key conditions for a notification:
1. There is a deviation from the personal norm (not an abstract one — yours, over 90 days)
2. There is a historical link: when it was like this before, what happened next
3. There are no contraindications (time of day, patient_constraints)

---

## Evidence base

The method relates a signal to a subsequent outcome using Spearman rank correlation.
Coefficients, sample sizes and observation periods stay in the tenant's data;
the public description defines the procedure, without an individual's results.

### How hypotheses are checked (method)

For each «signal today → outcome the same day or tomorrow» pair, compute the
Spearman coefficient, the number of pairs and the lag (0 or +1 day). Checking a separate
sub-period helps assess whether the association holds when the analysis window changes.

| Check condition | Interpretation rule |
|---|---|
| Too few observation pairs | Insufficient data to admit the signal |
| Association strength below the threshold | The pair fails the admission criterion |
| Association changes sign between windows | Stability needs a separate check |
| Pair meets the criteria | The association permits further analysis but does not prove causation |

### How a signal gets into a domain

Link-strength threshold: «strong» — |r| ≥ 0.35, «medium» — 0.10–0.35, with n ≥ 100 pairs. Pairs that
pass the threshold are taken into the domain; their r and n are stored in `system_config.domain_signals.*`
(see the auto-block GEN:DOMAIN_SIGNALS in ARCH_SNAPSHOT.md).

### When a signal is excluded

A signal that fails admission criteria or a stability check must not serve as the
basis for a recommendation. Possible confounding and reverse causation need
separate checks; the direction of an association alone establishes neither.

---

## Architecture

```
data yesterday/today
        ↓
get_metric_percentiles(90)     ← personal 90-day baseline
        ↓
Layer 1: DOMAIN_SIGNALS        ← percentile signals (r-calibrated)
        ↓
Layer 2: ABSOLUTE_FLOORS       ← p10 over the tenant's multi-year series (domain-independent)
        ↓
_signal_reason(metric, ...)    ← human-readable explanation
        ↓
patient_constraints            ← blocks (time of day)
        ↓
Telegram notification
```

### Files

| File | Function |
|---|---|
| `health_db.py` | `get_metric_percentiles(baseline_days=90)` |
| `telegram_bot.py` | `_signal_reason()`, `evaluate_domain_need(domain)`, `check_recommendations_scheduled()` |
| `health.db` → `patient_constraints` | constraints by protocol_id |
| `health.db` → `protocols` | domain and active protocol |

### Urgency

- `recommended` — 1 signal, percentile 10–30%
- `required` — 2+ signals **or** percentile < 10%

### Why blood pressure has two numbers (owner's decision 26.09)

An illustrative example (the numbers are made up): the monitor recorded two readings in a day — 146/96 in the morning, 110/80 in the afternoon.
The daily average — 128/88 — is below both thresholds, and the morning peak would vanish. But the peak cannot be made «the day's pressure» either: hypertension is
diagnosed from averages of repeated readings, and a single high reading can come from coffee or rushing.
So the two numbers answer two questions. The `bp_systolic`/`bp_diastolic` column is the **average**:
«what is this person's blood pressure», the link search runs on it. The day key `bp_systolic_max`/
`bp_diastolic_max` (raw only, no column) is the **peak**: «was there a moment today worth a look»,
the absolute ceiling of 140/90 sits on it.

The second thing that had to change: the ceiling took its value from percentiles, and the percentile skips
a metric with less than 14 days of history. Blood pressure is measured rarely — the ceiling was dead for any data.
Now a key that has no column is read by the ceiling directly from yesterday. Column metrics
are not read this way: `sleep_total`/`sleep_awake` have their own reader in `lifestyle_agents`, and the extension
would give double alarms. Boundary: the 140 threshold is taken from guidelines for averages; how noisy it is
on a single peak, the first live triggers will show.

### Important caveat

Correlations mean «after what things get better/worse», not «because of what». There is no tracking of interventions.
Hypotheses are of the form: «on such days the state usually worsens → an intervention is justified».

---

## Domains, thresholds and protocols

> The DOMAIN_SIGNALS reference, absolute floors, protocol and constraint tables:
> [ARCH_SNAPSHOT.md — §АЛЕРТ-ЛОГИКА](ARCH_SNAPSHOT.md).

> How to add a new domain (steps 1–7):
> [docs/how-to/add_domain.md](docs/how-to/add_domain.md).

---

