<!-- translation-of: docs/explanation/epistemic_discipline.md sha256:b3798e5923df -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](epistemic_discipline.md)

# Epistemic Discipline of the LLM Narrative

> **Document type:** Explanation (Diataxis) — why this exists and how it works.
> How to use: [docs/how-to/update_constitutions.md](../how-to/update_constitutions.md).
> Constitution rules: [CONSTITUTION_RULES.md](../../CONSTITUTION_RULES.md).

## The Problem

The LLM layer of the system (constitutions on Opus, consilium coordinator synthesis on Sonnet)
is prone to overconfidence: it makes trend claims on 2–4 data points,
trusts anomalously high correlations (`r≈0.99` on a short series — almost always
an artifact of small N), attributes agency ("the organism learned…"), and uses
medical terms as diagnoses. The deterministic alert layer already had
anti-noise discipline (baseline 30d / streak≥3 / threshold 15%), but the
narrative layer had none.

## What Was Done

The pattern was ported from a neighboring project. The module
`epistemic_skill/` was introduced — a compact set of calibration rules injected into the system
prompt of generators:

- **N → confidence ceiling** (N≤2 → no higher than "low"; trend-claim only at N≥3).
- **"No direction detected — Δ is within noise range at N=N"** instead of a forced arc.
- **Anti-patterns** AP-1 (categorical indicative), AP-2 (rationalization of regression
  via "however"), AP-7 ("for the first time" without a caveat), AP-9 (term as diagnosis →
  "compatible with X, physician confirms").
- **|r|≥0.95 on a short series** is flagged as a probable artifact.
- **"Disputed: …, physician decides"** instead of silently dropping the finding.

The discipline does NOT touch numeric thresholds in "Basic Recommendations" — those values
remain hard.

## How It Works

- Texts: `epistemic_skill/discipline.txt` (constitutions), `epistemic_skill/coordinator.txt`
  (consilium coordinator, core rules without constitution-specific content).
- `epistemic_skill/loader.py`: `load_skill()` / `load_part(name)` → `.text` + `.version`
  (`"epi_"+sha256[:12]`, changes when any file is edited; suitable for a provenance stamp).
  Fail-fast: missing file → `RuntimeError`.
- Injection points (behind the `EPISTEMIC_DISCIPLINE` flag, **default ON**, revert with `=off`):
  - `generate_constitutions._load_epistemic()` → `_build_prompt`;
  - `wellally_consult._load_epistemic_coord()` → coordinator system prompt in `_call_coordinator_async`.

### Why Default-ON in Code, Not env in launchd

The plists for constitution generation and the bot are not in git (the launchd job for constitution
generation has no repository entry; the bot plist lives only in the Studio runtime). An env-in-plist
would cover only part of the execution paths and would not be reproducible. Default-ON in code is a
single git change, covers all paths (manual run, bot, `monthly_consilium`, any scheduler),
and is reverted with `EPISTEMIC_DISCIPLINE=off`.

## How It Was Tested

Shadow A/B on real data (zero-touch, no writes to the DB): constitutions
`stress`/`sleep`/`nutrition` and one consilium session — two variants on the same data,
baseline vs disciplined. Disciplined versions tie N and confidence to trends,
remove/flag spurious `r` values, hedge agency, while preserving numeric thresholds.
Comparison artifacts are in iCloud `health/shadow_ab/`.

## What Is NOT Covered (as of 2026-06-20)

- **Semantic guardrail tests** on generated text (AP-grep + golden-set
  with a numeric threshold) — only deterministic wiring tests exist
  (`tests/unit/test_epistemic_skill.py`, `check_contracts`).
- Surfaces `_build_diff_prompt` (the "what changed" section), `_run_alert_review`,
  individual consilium specialists — still on baseline prompts.
- Hypotheses (`cbcr_hypothesis`) are intentionally NOT touched: they already have their own
  discipline (`compute_structural_confidence`, `CRITIQUE_SYSTEM`, the CBCR manifest).

## Number in the Report: Axis of Judgment Is Provenance, Not Membership in Belief (2026-09-08)

UC-B-09 was built as a FABRICATION detector: "the model printed a coefficient that
nobody computed." But the absence of a number from accepted belief does not by itself answer
whether it was computed or whether the surrounding narrative is justified.

**Two error classes, without a history of personal reports.**

- The number was computed, but the **context is fabricated**: the report attributes to it confirmation
  on a longer joint series than the one that was actually used in the calculation.
- The number was computed by a **second producer outside the gate**: for example, Spearman without
  permutations and FDR. `hai_analysis.detect_correlation_drift` is a technical example
  of such a path; it has been removed from the consilium input.

This is not a literature citation and not number generation. The question here is **provenance**:
what computed the number and whether anyone judged it.

**Why the net may fire — and why this cannot be relied upon.** The sensor predicate is
"this r is not in ACCEPTED belief." If accepted belief is empty, the predicate is true
for ANY r and the net catches everything indiscriminately. This is a property of state, not quality:
with non-empty belief, a number from an unfit producer that happens to match an accepted one
may pass in silence. An empty set does not by itself prove the absence of associations.

Terminologically, this is the pitfall that Rapid Software Testing names directly: **comparison
is presented as an authoritative oracle**. "Is r in belief" is a comparison. The authority on
the question "is the number justified" is its origin.

**What follows from this, and why there are now two nets.** They differ not in subject but in
oracle strength, and therefore must differ in signal level.

| | question | oracle | level |
|---|---|---|---|
| **leak** (`check_correlations_grounded`) | did the number reach the report — is it justified? | non-authoritative: a citation cannot be distinguished from a leak by text alone | WARN, and cannot be otherwise |
| **birth** (`check_correlation_producers_declared`) | who within the perimeter computes correlations at all, and are they declared? | authoritative: the set is computable, "declared" is binary | WARN on burn-in |

The birth net would have named `detect_correlation_drift` on the day it appeared. It would have
found it not because it is smarter, but because it asks earlier: not "did the number reach its
owner" but "does this module have the right to compute it." Honest caveat: the justification for
the net is a sample of ONE case; it was built cheaply (a clone of
`check_llm_tracts_guarded`), and treating a single incident as statistics is not admissible.

**What remains open, stated plainly.** The class "fabricated context around a computed number"
is caught by neither net: both look at the number itself. The machinery for this class already
exists in the project (claims-check of numbers in the `weekly-digest` thread), but it has not
been reused here — deferred with a trigger: a second case of fabricated context. And the birth
detector knows the NAMES of calls, so a correlation computed manually via covariance is invisible
to it: "what is named is covered," not "everything is covered."

**There is no longer a calendar trigger for promotion to FAIL.** It was declared on 08.08
("one week of observation → decision") and closed by the owner's verdict on 08.09: do not promote;
condition for review — birth net is built AND belief has ceased to be empty. The second is
an event in the `validation_gate` subsystem, not a date, and that is the difference between
a decision and a third deferral.
