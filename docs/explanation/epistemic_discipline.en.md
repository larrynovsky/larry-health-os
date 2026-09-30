<!-- translation-of: docs/explanation/epistemic_discipline.md sha256:2118294fb691 -->
**English** · [Русский](epistemic_discipline.md)

# Epistemic discipline for LLM narratives

> **Document type:** Explanation (Diataxis) — why it exists and how it works.
> How to use it: [docs/how-to/update_constitutions.md](../how-to/update_constitutions.md).
> Constitution rules: [CONSTITUTION_RULES.md](../../CONSTITUTION_RULES.md).

## Problem

The system's LLM layer (constitutions on Opus, consilium coordinator synthesis on Sonnet)
tends to overstate confidence: it makes trend claims from 2–4 points,
trusts abnormally high correlations (`r≈0.99` on a short series is almost always
a small-N artifact), attributes agency ("the body has learned…"), and uses
medical terms as diagnoses. The deterministic alert layer already had
noise-control discipline (30-day baseline / streak≥3 / 15% threshold), but the
narrative layer did not.

## What was done

The pattern was transferred from the neighbour project (`narrator_skill`). The
`epistemic_skill/` module was introduced — a compact set of calibration rules injected into generators'
system prompts:

- **N → confidence ceiling** (N≤2 → no higher than "low"; trend claim only at N≥3).
- **"I am not assigning a direction — Δ is in the noise range at N=N"** instead of a forced narrative arc.
- **Antipatterns** AP-1 (categorical indicative), AP-2 (rationalizing regression
  through "however"), AP-7 ("for the first time" without qualification), AP-9 (term as diagnosis →
  "consistent with X, to be confirmed by a physician").
- **|r|≥0.95 on a short series** is marked as a likely artifact.
- **"Disputed: …, the physician decides"** instead of silently removing the conclusion.

The discipline does NOT affect numerical thresholds in "Basic recommendations" — those values
remain fixed.

## How it works

- Texts: `epistemic_skill/discipline.txt` (constitutions), `epistemic_skill/coordinator.txt`
  (consilium coordinator, core rules without constitution-specific details).
- `epistemic_skill/loader.py`: `load_skill()` / `load_part(name)` → `.text` + `.version`
  (`"epi_"+sha256[:12]`, changes when any file is edited; suitable for a provenance stamp).
  Fail-fast: a missing file → `RuntimeError`.
- Injection points (behind the `EPISTEMIC_DISCIPLINE` flag, **default ON**, revert with `=off`):
  - `generate_constitutions._load_epistemic()` → `_build_prompt`;
  - `wellally_consult._load_epistemic_coord()` → the coordinator's system prompt in `_call_coordinator_async`.

### Why default ON in code, not env in launchd

The constitution generation and bot plists are not in git (the constitution generation launchd job
is absent from the repository; the bot plist lives only in Studio's runtime). Env in a plist would cover
only some paths and would not be reproducible. Default ON in code is one git change,
covers all paths (manual runs, the bot, `monthly_consilium`, any scheduler),
and can be reverted with `EPISTEMIC_DISCIPLINE=off`.

## How it was checked

Shadow A/B on real data (zero-touch, no database writes): constitutions
`stress`/`sleep`/`nutrition` and one consilium session — two variants on the same data,
baseline vs disciplined. The disciplined versions tie N and confidence to trends,
remove/mark spurious `r`, hedge agency, and retain the numerical
thresholds. Comparison artifacts are in iCloud `health/shadow_ab/`.

## What is NOT covered (as of 2026-06-20)

- **Semantic guardrail tests** on generated text (AP-grep + golden set
  with a numerical threshold) — only deterministic wiring tests exist
  (`tests/unit/test_epistemic_skill.py`, `check_contracts`).
- The `_build_diff_prompt` surfaces ("what changed" section), `_run_alert_review`,
  and individual consilium specialists still use the baseline prompt.
- Hypotheses (`cbcr_hypothesis`) are deliberately NOT touched: they already have their own
  discipline (`compute_structural_confidence`, `CRITIQUE_SYSTEM`, the CBCR manifesto).

## A number in a report: the axis of judgment is provenance, not membership in belief (2026-09-08)

UC-B-09 was built as a FABRICATION detector: "the model printed a coefficient that
nobody computed." But absence from accepted belief does not establish whether a
number was computed or whether the surrounding narrative is justified.

**Two failure classes, without personal report history.**

- A number is computed, but **its context is invented**: the report attributes
  confirmation to a longer joint series than the one used in the calculation.
- A number is computed by **a second producer bypassing the gate**: for example,
  Spearman without permutations or FDR. `hai_analysis.detect_correlation_drift`
  is a technical example of such a path; it was removed from the consilium input.

Neither is a literature quotation or an invented number. The question is **provenance**:
what computed the number and whether anyone judged it.

**Why the net may work — and why this cannot be relied on.** The sensor's predicate
is "this r is absent from ACCEPTED belief." If accepted belief is empty, the predicate
is true for ANY r and the net catches everything. This is a property of state, not quality:
with nonempty belief, a number from an invalid producer that happens to match an
accepted one may pass silently. An empty set alone does not prove an absence of associations.

In terminology, this is the pitfall Rapid Software Testing names
directly: **a comparison is presented as an authoritative oracle**. "Is r in belief?" is
a comparison. The authority on "is this number justified?" is its origin.

**What follows, and why there are now two nets.** They differ not in topic but in
oracle strength, and therefore must differ in signal level.

| | Question | Oracle | Level |
|---|---|---|---|
| **Leakage** (`check_correlations_grounded`) | A number reached the report — is it justified? | Nonauthoritative: text alone cannot distinguish a quotation from a leak | WARN, and it cannot be otherwise |
| **Creation** (`check_correlation_producers_declared`) | Who in the perimeter computes correlations at all, and are they declared? | Authoritative: the set is computable; "declared" is binary | WARN during burn-in |

The creation net would have named `detect_correlation_drift` on the day it appeared. It
would have found it not by being smarter but by asking earlier: not "did the
number reach the owner," but "is this module entitled to compute it?" An honest qualification:
the justification for the net is a sample of ONE case; it was built cheaply (a clone of
`check_llm_tracts_guarded`), and one incident cannot be presented as statistics.

**What remains uncovered, stated aloud.** The "invented context around a computed
number" class is caught by neither net: both look at the number itself.
The project already has machinery for this class (claims-check for numbers in the
`weekly-digest` thread), but it has not been reused here — deferred with a trigger: a second
case of invented context. Also, the creation detector knows call NAMES, so a
correlation computed manually through covariance is invisible to it: this is "what is
named is covered," not "everything is covered."

**There is no longer a calendar trigger for promotion to FAIL.** It was declared on 08-08
("one week of observation → decision") and closed by the owner's verdict on 09-08: do not promote;
the condition for reconsideration is that the creation net is built AND belief is no longer empty. The second is
an event in the `validation_gate` subsystem, not a date — the difference between a decision and
a third postponement.
