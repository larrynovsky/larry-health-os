<!-- translation-of: docs/explanation/wave7_anti_recurrence_plan.md sha256:6e358a258556 -->
**English** · [Русский](wave7_anti_recurrence_plan.md)

> Retired 2026-09-30: the experiment ran on 15.06; the service, script and template were removed. This document is the history of the plan.


# Wave 7 — Anti-recurrence (reason + decay + counter-evidence + UI)

Design doc. After implementation → `docs/how-to/manage_recurrence.md`.

Dependency: **Phase 3 must be closed** (requires `dashboard_edits` from 3A.3 and the reject status action). Can start 2–4 weeks after Phase 3 — real data on “how often the user rejects” is needed to calibrate decay timings.

## What is in scope / out of scope

In scope:
- Reason on rejection (4 categories + free text)
- Decay engine with per-reason strategies
- Counter-evidence escalation chip
- “Recently rejected” UI fold-down on `/hypotheses`
- Manual unblock for permanent reasons

Out of scope:
- Anti-recurrence for problems / protocols / experiments (only hypotheses in this wave)
- Automatic escalation after N repetitions (“the 3rd time = not noise”) — left as an idea for Wave 8
- ML detection of “semantically similar” hypotheses — only a canonical key based on `metric_focus`

## Context from previous waves

**Wave 5H-B (2026-05-14)** disabled `generate_hypothesis_from_drift` and `generate_hypothesis_from_correlation`. Since then, hypotheses are born **only** through `monthly_consilium.py` on the 1st at 04:00.

This simplifies Wave 7: anti-recurrence is needed **at one integration point** — `monthly_consilium.py`. No need to patch N writers.

**Wave 5G-1** introduced `metric_focus` normalization + grouping by canonical key. This is `_normalize_metric_focus()` — it already exists. Reuse without rewriting.

## Architectural decisions

### Reason → decay-strategy mapping

Stored in `system_config.rejection_decay_rules` (JSON):

```json
{
  "noise": {
    "strategy": "ttl",
    "days": 90
  },
  "superseded": {
    "strategy": "permanent"
  },
  "phase_mismatch": {
    "strategy": "while_period_active",
    "period_type_field": "period_type"
  },
  "methodological": {
    "strategy": "permanent_until_manual"
  }
}
```

The numbers (90 days for noise) are **arbitrary**. Parameterized in config; recalculate from actual rejection data 3 months after deployment.

### canonical_key for metric_focus

Reuse `_normalize_metric_focus(payload)` from Wave 5G-1. If it is private, extract it as a public helper in the new `recurrence_filter.py`.

The canonical key is a string serialization of the set `(domain, primary_metric, intervention)` after normalization (lowercase, sorted, no duplicates). Its exact form is determined by the existing code.

### Decay engine: `recurrence_filter.py`

One module, one function:

```python
def is_blocked(canonical_key: str, current_periods: list[dict], now: datetime) -> tuple[bool, str | None]:
    """Returns (blocked, reason) for the decision 'should a new hypothesis be created with this key'.
    
    Algorithm:
    1. SELECT all rejected hypotheses with this canonical_key across the whole history
    2. For each one, take payload.rejection_reason
    3. Apply the decay-strategy from system_config
    4. If there is at least one active block, return (True, reason)
    5. Otherwise, return (False, None)
    """
```

Returns a tuple, not a bool — the reason is needed for logging.

### Counter-evidence

If `is_blocked → False`, but history contains `rejection` entries with the same `canonical_key`:
- The new hypothesis is created with `payload.recurrence_count = len(prior_rejects) + 1`
- `payload.previously_rejected_ids = [id1, id2, ...]`
- Chip `возвращается · N-й раз` on the card

This **does not block**; it **highlights context**.

### Manual unblock

In the “recently rejected” UI fold-down, next to rejected cards with `reason ∈ {superseded, methodological}`, an “unblock” button. Click → a `dashboard_edits.action='unblock_recurrence'` record + `payload.rejection_unblocked_at=now`. `recurrence_filter` ignores rejections with unblocked_at IS NOT NULL.

## Implementation stages

### W7-A: Reason on rejection (~1.5 hours)

UI:
- The `reject` button on a hypothesis card does not act immediately; it expands an inline form:
  - 4 radio buttons: `шум` / `замещена` / `не подходит фазе` / `методологически неверно`
  - Optional textarea `детали`
  - If `замещена` → select dropdown with active hypotheses for `superseded_by`
  - If `не подходит фазе` → select dropdown with active `periods.type` values for `period_type`
  - The `подтвердить отказ` button sends a POST

Endpoint:
```python
@app.post("/api/hypotheses/{id}/reject")
def hypothesis_reject(id: int, reason: str = Form(...), detail: str = Form(""),
                      superseded_by: int = Form(None), period_type: str = Form(None)):
    ...
    payload["resolution_type"] = "rejected"
    payload["rejection_reason"] = reason
    payload["rejection_reason_detail"] = detail
    if superseded_by: payload["superseded_by"] = superseded_by
    if period_type: payload["rejection_period_type"] = period_type
    ...
```

The confirm button stays one-click — no reason is needed there.

### W7-B: Decay engine `recurrence_filter.py` (~2 hours)

New file `~/health_scripts/recurrence_filter.py`:

```python
def get_decay_rules() -> dict:
    row = _q("SELECT value FROM system_config WHERE key='rejection_decay_rules'")
    return json.loads(row[0]["value"]) if row else DEFAULT_RULES

def is_blocked(canonical_key, current_period_types, now=None):
    now = now or datetime.now()
    rules = get_decay_rules()
    # SELECT the rejected ones
    rejects = _q("""SELECT id, value, updated_at FROM memory
                    WHERE category='hypothesis' AND active=0
                      AND value LIKE ? -- substring match canonical_key""", (f'%{canonical_key}%',))
    # filter by rejection_unblocked_at
    rejects = [r for r in rejects if not json.loads(r["value"]).get("rejection_unblocked_at")]
    
    for r in rejects:
        payload = json.loads(r["value"])
        reason = payload.get("rejection_reason", "noise")
        strategy = rules.get(reason, {}).get("strategy", "ttl")
        if strategy == "permanent" or strategy == "permanent_until_manual":
            return (True, reason)
        if strategy == "ttl":
            age_days = (now - datetime.fromisoformat(r["updated_at"])).days
            if age_days < rules[reason]["days"]:
                return (True, reason)
        if strategy == "while_period_active":
            blocked_period_type = payload.get("rejection_period_type")
            if blocked_period_type and blocked_period_type in current_period_types:
                return (True, reason)
    return (False, None)

def previous_reject_ids(canonical_key) -> list[int]:
    """All ids of past rejects, for counter-evidence."""
    ...
```

Unit tests for all 4 strategies (W7-G).

### W7-C: Canonical-key extraction (~1 hour)

Find the existing `_normalize_metric_focus` in `correlation_analysis.py` (Wave 5G-1). Extract it as the public helper `canonical_metric_key(payload) -> str` in `recurrence_filter.py`.

If the function is already public, just import it.

⚠️ **Check before starting**: `grep -n "normalize_metric_focus\\|canonical" ~/health_scripts/*.py`. If canonicalization is done inline in the pipeline, extract it.

### W7-D: Integration into `monthly_consilium.py` (~1.5 hours)

In `_build_consilium_input` or `_run_pass2_cbcr` (where the hypothesis is created), **before** INSERT:

```python
from recurrence_filter import is_blocked, previous_reject_ids, canonical_metric_key

for candidate in candidates:
    key = canonical_metric_key(candidate)
    current_period_types = [p["type"] for p in _q("SELECT type FROM periods WHERE active=1")]
    blocked, reason = is_blocked(key, current_period_types)
    
    if blocked:
        _log_edit("consilium", "0", "hypothesis_blocked_by_recurrence",
                  field=key, old_value=reason, new_value=None)
        # actor='consilium': extend the _log_edit signature for it
        continue
    
    # Counter-evidence
    prior = previous_reject_ids(key)
    if prior:
        candidate["payload"]["recurrence_count"] = len(prior) + 1
        candidate["payload"]["previously_rejected_ids"] = prior
    
    # INSERT INTO memory ...
```

⚠️ **Before starting**: locate the exact INSERT point in the current `monthly_consilium.py`. There may be more than one (the CBCR pipeline is complex). `grep -n "INSERT INTO memory\\|INSERT INTO hypotheses_cbcr"`.

### W7-E: Counter-evidence chip in the UI (~0.5 hours)

In `hypotheses.html`:

```html
{% if h.payload.recurrence_count and h.payload.recurrence_count > 1 %}
  <span class="chip chip--warning">возвращается · {{ h.payload.recurrence_count }}-й раз</span>
{% endif %}
```

In the rejected card template (inside the fold-down), a link to this repeated hypothesis for context.

### W7-F: “Recently rejected” UI fold-down (~1 hour)

In `hypotheses.html` after the archive section:

```html
<details class="recently-rejected">
  <summary>недавно отвергнуто (30 дней) — {{ recently_rejected | length }}</summary>
  {% for r in recently_rejected %}
    <article class="card">
      <div class="card-head">
        <div class="card-meta">№ {{ r.id }} · {{ r.updated_at | age_days }}d · {{ r.payload.rejection_reason }}</div>
        <div class="card-chips">
          {% if r.payload.rejection_reason in ['superseded', 'methodological'] %}
            <button class="action-pill"
                    hx-post="/api/hypotheses/{{ r.id }}/unblock"
                    hx-confirm="Снять блок?"
                    hx-target="closest article"
                    hx-swap="outerHTML">снять блок</button>
          {% endif %}
        </div>
      </div>
      <h3 class="card-title">{{ r.payload.observation | short(140) }}</h3>
      {% if r.payload.rejection_reason_detail %}
        <p class="card-description">{{ r.payload.rejection_reason_detail }}</p>
      {% endif %}
    </article>
  {% endfor %}
</details>
```

In the `dashboard.py` route `/hypotheses`, add a query for “rejected in the past 30 days.”

CSS: `details summary { font-family: var(--font-serif); font-style: italic; color: var(--color-text-hint); cursor: pointer; }`.

### W7-G: Tests (~1.5 hours)

`tests/integration/test_recurrence_filter.py`:

```python
def test_noise_decay_90_days():
    """reject 89 days ago with reason=noise → blocked. 91 days → unblocked."""

def test_superseded_permanent():
    """reject with reason=superseded → always blocked."""

def test_phase_mismatch_active():
    """reject with rejection_period_type='treatment' → blocked while an active period type=treatment exists.
       After the period's end_date → unblocked."""

def test_methodological_unblock():
    """reject methodological → blocked. After rejection_unblocked_at → unblocked."""

def test_counter_evidence():
    """reject → decay has passed → recreate → recurrence_count=2, previously_rejected_ids=[prev_id]."""

def test_multiple_rejects_same_key():
    """3 rejects with different reasons → the strictest one blocks."""
```

### W7-H: Documentation (~0.5 hours)

- `docs/how-to/manage_recurrence.md` — how to edit decay rules and unblock manually
- `docs/explanation/anti_recurrence.md` — why this architecture, which alternatives were considered

## Time estimate

Total: **~9 hours**, possible in 2 sessions:
- Session 1 (5 h): W7-A + W7-B + W7-C + W7-G partial
- Session 2 (4 h): W7-D + W7-E + W7-F + W7-G complete + W7-H

## Definition of done

- [ ] On rejection, selecting a reason (4 categories + free text) is mandatory
- [ ] `system_config.rejection_decay_rules` initialized with defaults
- [ ] `recurrence_filter.is_blocked()` works correctly for all 4 strategies
- [ ] `canonical_metric_key()` is deterministic (same payload → same key)
- [ ] `monthly_consilium` filters through `is_blocked` before INSERT
- [ ] Counter-evidence: a new hypothesis with prior rejections receives `recurrence_count` + `previously_rejected_ids`
- [ ] “Recently rejected (30 days)” UI fold-down shows a reason chip
- [ ] Manual unblock works for `superseded` and `methodological`
- [ ] 6+ tests PASS in `test_recurrence_filter.py`
- [ ] Documentation: how-to + explanation files


## Scheduled data check 2026-06-15 (added 2026-05-16)

To base the “do we need Wave 7?” decision on actual operational data rather than guesses, automatic collection through launchd has been configured.

**Files:**
- `scripts/wave7_recurrence_check.py` — analyzer
- `launchd/com.larry.health.wave7-check.plist` — trigger at 2026-06-15 09:00 (StartCalendarInterval with an exact date = once)

**What the script will collect:**
1. `dashboard_edits` for the month: how many rejections, how many confirmations
2. `memory` for the period: how many new hypotheses `monthly_consilium` created on June 1
3. Rough comparison of observations: are there duplicates (rejected ↔ new)?
4. Writes a report to `~/health/reports/wave7_data_2026-06-15.md`
5. Creates a task in the dashboard's `/tasks` with the text “Wave 7 review”

**Automatic recommendation (REC-1..REC-4):**

| Condition | Recommendation |
|---|---|
| <3 rejections in a month | REC-1: close Wave 7 as an imagined need |
| rejections exist, no duplicates | REC-2: not needed — `monthly_consilium` already filters through Wave 5G dedup |
| 1–2 duplicates | REC-3: lightweight version (~3 h), 1 reject button + 30–90-day decay, without 4 categories |
| 3+ duplicates | REC-4: as described in this plan (~9 h) |

**What the script does not do automatically:**
- Does not make the decision — gives a recommendation; the final decision is the user's
- Does not edit ROADMAP — that is a subsequent step after reading the report
- Does not implement Wave 7 — that is a separate session

**Verification:**
```bash
ssh <studio_ssh> "launchctl list | grep wave7"
# Expected: -  0  com.larry.health.wave7-check
# Status=0 means loaded and waiting for the trigger
```

After 2026-06-15 09:00, the script will run once; the plist remains loaded but will not trigger again (the date is specific).

**Failure modes:**
- Studio is off at trigger time → launchd runs it at the next startup (standard behavior)
- Script crashes → log in `~/health_scripts/logs/wave7_check.err.log`; the task is NOT created
- Database locked by another agent → busy_timeout=5000, usually sufficient

## Open questions

1. **Decay timings (90 days for noise)** — arbitrary. Recalculate from actual rejection frequency 3 months after deployment. Alternative: make timing configurable per domain (sleep vs. oncology may need different timings).

2. **Multiple active periods** — the user rejects with `phase_mismatch period_type='travel'`, but there is also active `treatment`. After 30 days, travel ends while treatment continues. The hypothesis is unblocked. Is that correct? I think so, because the rejection was tied specifically to **travel**.

3. **`canonical_metric_key` stability** — the function was written for within-run deduplication. In 6 months, `payload` may have a different structure (CBCR evolves). Key stability is an open question. Solution: store `payload.canonical_key_v1` alongside it, writing it on INSERT. Matching then uses the stored key rather than recalculation.

4. **`actor` field in `dashboard_edits`** — the Phase 3 plan defaults to `actor='dashboard'`. Wave 7 extends the values to `consilium` / `correlation_analysis`. Does this require a migration or simply writing a new value? I think just writing it — a text field without constraints.

5. **Automatic escalation after N repetitions** — `recurrence_count >= 3` means “keeps returning, may be important.” Left as an idea for Wave 8. For now, only a chip highlights it.

## What can break (lessons learned from Wave 5H)

In Wave 5H, we disabled automatic generation because the hypotheses being created were noisy (600–607). Anti-recurrence targets a **different** class of noise: “what has already been rejected.” But there is a risk of **over-blocking** — a hypothesis was rejected as `noise`, and half a year later the person is in a different phase with different biochemistry — same canonical key, block still active.

Protection against over-blocking:
1. **Phase_mismatch reason** — transitioning between phases **immediately removes the block**. This works only if the user selects the correct reason. If they chose `noise`, the block lasts 90 days regardless of phase.
2. **Manual unblock** — for `superseded` and `methodological`. If the user sees “this needs reconsideration,” they remove the block.
3. **Counter-evidence chip** — if a hypothesis makes it through decay and has `recurrence_count=2`, this is a **signal**. A reminder, not a block.

This is a compromise, not a silver bullet.
