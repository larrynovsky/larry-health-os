<!-- translation-of: docs/explanation/causal_refinement.md sha256:a1f6ad11e3c2 -->
**English** · [Русский](causal_refinement.md)

# Causal refinement of A levers (Group 2 fdr-online)

Diátaxis: explanation. Load-bearing → git. Thread: `fdr-online`. Code: `correlation_gate.py`
(`_causal_verdict`, `_fast_residual`, `causal_label`); parameters: `signal_family.yaml`
(`causal_*`, version 4). Reference contract at the end of the file.

## Why this is needed when family A already exists

Family A (the stratified gate) answers the question "does the association hold WITHIN a stable
regimen, or are both series responding to a regimen change?" It removes the **epoch**
confound — the steps between therapeutic periods. But "a stable association within a regimen" does not yet mean "a lever
you can pull." Within one epoch, deep sleep and HRV may rise together because
a quiet evening raises both — a common cause on the same night. A lets such a pair through as
a lever, even though there is nothing to pull.

Causal refinement sits **on top of A levers** and answers the second question: is this a controllable
association or a coincidence? It reopens nothing in A — it only adds one of three labels
to the A levers already selected.

## Three verdicts and why these three

We compute on the **fast residual**: within each epoch, we remove the day-of-week mean and local
drift over ±7 days. Only daily fluctuation remains — the slow and weekly common background is removed.

- **coincidence** — the association disappears entirely on the fast residual. This means it lived in the slow
  common background (disease drift, weekly rhythm), not in daily mechanics. Not a lever.
- **lever** — the fast residual shows **directional asymmetry over time**: the predictor→target association
  one day later is `causal_asym_ratio` times stronger than the reverse and is significant. Temporal precedence is
  the only observational signature of controllability available for one person without intervention.
- **do_not_know** — the association holds, but only **simultaneously** (the same night), without asymmetry. At N=1,
  a lever cannot be distinguished from a common cause on the same night. An honest abstention, not a stretch.

## Asymmetric losses and why "do not know" is normal

A false "lever" costs more than a false "coincidence": the former leads to behavior changes and chasing a phantom;
the latter is simply not shown. The bar for "lever" is therefore high — directional asymmetry is required,
not merely significance. At N=1, the subtests are underpowered, so **the modal verdict at first is
"do_not_know," and that is correct**, not a defect. If the layer starts churning out "lever" verdicts, that signals
that the thresholds are too lenient, not that it has become smarter.

## What this layer does NOT do (boundaries of honesty)

This is a **descriptive annotation, not an error-rate inference**. It is outside the false discovery budget of families
A/D (`q_A`/`q_D`) and claims no controlled error rate — otherwise this would be post-selection
on already selected A levers (double dipping). The thresholds are "clarity cutoffs," not a guarantee. A verdict is
a hypothesis for a person, an invitation to check, **never a directive or a basis for automatic action**.
The owner is the oracle for threshold values. At N=1, a person is the oracle of truth: only
replication/intervention at the action layer (§12.1 action-gate) can truly test a lever, not this layer.

## Reference — verdict contract

The source of truth is the code and `signal_family.yaml` (not a retelling, to prevent drift). This is a map.

- **Columns** (owner-only, `enable_stratified=True`): `verdict_causal` ∈ {`рычаг`, `совпадение`,
  `не_знаю`, `""`} (empty = not an A lever/tenant/old snapshot), `causal_p0` (p of the simultaneous subtest).
- **Wording** — the sole source is `correlation_gate.causal_label()`; read by `generate_constitutions`
  and `gp_context`, inserted once in `build_ai_summary`. Field absent → render as before (backward compatibility).
- **Provenance** (in the JSON element): `p0`, `p_fwd`, `p_rev`, `r0`, `r_fwd`, `r_rev`, `n_epochs`, `why`.
- **Parameters** (`signal_family.yaml`, version 4): `causal_alpha` (subtest cutoff = uncertainty
  floor), `causal_b_perm` (MC pool), `causal_resid_window` (±days for the fast residual),
  `causal_lag_days` (offset), `causal_asym_ratio` (how many times stronger the leading lag is than the reverse).
- **Ledger invariant**: each A lever receives exactly one of the three verdicts → the sum of counters
  `meta.stratified.causal` == `meta.stratified.a_levers`. Guard: `tests/unit/test_gate_causal.py`.
- **Owner-only**: the layer is computed only under `is_owner()` (epochs = the owner's timeline); tenants have no columns.
