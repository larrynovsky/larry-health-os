<!-- translation-of: docs/explanation/signal_validation_lifecycle.md sha256:3f3aa4836a1b -->
**English** · [Русский](signal_validation_lifecycle.md)

# A signal's lifecycle: validation gate specification

> **Type (Diátaxis):** spec/explanation — subsystem intent, not a step-by-step how-to.
> **Audience:** an engineer who will extend the hypothesis subsystem with a signal `kind`.
> **Status:** design (2026-06-22). Some decisions are OPEN — see §6.
> **Note:** each item is marked `[факт]` (grounded in code/docs) or `[выбор]` (our design decision, not yet built).

---

## 0. Why

Sensors detect **arousal** of the autonomic nervous system (“stress”), but not its sign or other axes (mood, cognitive load, fatigue, rumination, withdrawal, circadian disruption). The idea is to extract orthogonal signals from digital exhaust (Telegram timings, speech from calls) and admit them into the system **with discipline**.

The key danger is that our data feeds a generative layer (constitutions/consilium). A weak proxy is not an extra column but a false premise from which an LLM will compose a confident narrative. `longitudinal_analysis.py` already runs Spearman across all pairs with raw `p<0.05` and passes the top results to `build_ai_summary` → constitutions **without correcting for autocorrelation or multiple comparisons** `[факт]`. The gate is needed so that what the system *believes* is earned, not found by chance.

---

## 1. Supporting principles

- **Birth ≠ belief** `[выбор]`. A declaration merely registers a “feature↔target” pair and starts the clock. Belief is allowed only after confirmation on future data. So input noise is not a problem — the gate is downstream.
- **The verdict is deterministic** `[выбор]`. Code (a statistical harness) judges, not an LLM or a person. Precedent: `compute_structural_confidence` is deterministic, code-owned, and not rewritten by the model `[факт]`.
- **Confirmation strictly on post-declaration data** `[выбор]`. This neutralizes the look-elsewhere effect: a historical chance correlation will not recur in fresh data that did not exist at selection time.
- **Rechecking is mandatory** `[выбор]`. A verdict is perishable. This aligns with medical hypotheses: `confirmed` does not close them but returns them to the cycle with a new version `[факт]`.
- **Detection with delivery**. A loud sensor (alert) for every silent failure. Check the path to Telegram, not whether a function exists.

---

## 2. Lifecycle (end-to-end example: “nighttime Telegram ↔ next-day readiness, expected −, lag +1”)

0. **Raw material (before a signal).** Timings/features are continuously written to cold storage. There is no signal.
1. **Birth — automatic, as data accumulates.** A cron script fishes for correlations in accumulated history and declares candidates. NOT automatic spawning from daily drift (buried in W5H-B) `[факт]` — birth comes from *accumulation*, not a blip. `save_hypothesis(kind='signal')`, with declaration fields (feature/target/lag/expected direction/provenance) **frozen by code**. `declared_at` = clock start. Deduplication uses the structural key feature+target+lag, not Haiku semantics `[выбор]`.
2. **How you find out.** One notification at birth (Telegram/dashboard): “under review: X↔Y, expecting Z, verdict in ~N.” Then **silence**, no live statistics (anti-peeking). Dashboard section: (i) under review — no effect size; (ii) admitted — with effect and CI; (iii) rejected/demoted log.
3. **Accumulation (Action — observational).** `experiment(intervention='observe')`. Evaluation is triggered not by a lab import (that is the medical path `hypothesis_lab_linker`) `[факт]`, but by a new accumulation job. Below the threshold — `insufficient`, not a bogus verdict.
4. **Verdict — a deterministic harness** (see §4), not the consilium. The confirmation window is only `t > declared_at`.
5. **Resolution** through `resolve_hypothesis`, branches for `kind='signal'`:
   - `confirmed` → `admitted`; does **NOT** call `generate_protocol` (otherwise it would leak into `get_active_protocols`→constitutions); moves to rechecking with an expiration date.
   - `rejected` → `active=0`. A strong reverse effect → a new declaration (clock starts over), not a silent reversal.
   - `insufficient` → remains `testing`.
6. **Admission/consumption** — see §6; the decision is open.
7. **Rechecking** on a schedule against fresh data; failure → demotion + alert. **With hysteresis**: do not demote after one bad window (2 consecutive windows / CI excludes the original effect ≥ N) — otherwise flapping and spam.

---

## 3. Grounding in the hypothesis subsystem

- A hypothesis is a record in `memory(category='hypothesis')` with JSON + a duplicate payload in `hypotheses_cbcr` `[факт]`. So `kind='signal'` is **a JSON field, zero migrations** `[факт→выбор]`.
- Two formats already coexist in one table (CBCR illness_script vs. consilium patient_view), distinguished by `generated_by` `[факт]`. A signal is a third kind following the same pattern, nothing new.
- Differences for the signal kind: verdict **bypasses** `evaluate_hypothesis_via_consilium`; **no** illness_script; structural deduplication; the `confirmed` branch does not create a protocol.
- **Admission point (chokepoint)** `[факт]`: the only pipe from “confirmed → LLM” is `generate_constitutions.py:593` → `_db.get_active_protocols()` → the “Active protocols” block in the prompt. `patient_context.build_patient_brief` is clean. Signal enforcement: `get_active_protocols()` never returns signal rows + a contract test.
- Loop asymmetry `[выбор]`: signals have a **fully automatic loop** (automatic `open→testing`, automatic accumulation trigger); the medical loop is deliberately broken at the human `[факт]`. This is intentional: a signal's verdict is deterministic, so there is nothing for a person to judge.

---

## 4. Statistical harness (verdict)

The unit is not “days,” but the **effective number of independent observations after accounting for autocorrelation**, for the required effect size.

- **Accounting for autocorrelation:** circular-shift / block permutation test (preserves each series' autocorrelation, breaks cross-dependence) → a valid null. Cheap, only numpy/scipy. Alternative/complement: prewhitening (AR residuals).
- **Multiple comparisons:** FDR (Benjamini–Yekutieli under dependence) **across the family/registry over time**, not per run. With a generous critic (§5), this becomes structural — the main residual risk.
- **Held-out:** learn on an early post-declaration window, confirm on a later one; direction and magnitude must survive.
- **Idiographic:** only the person's own series; population norms are at most a weak prior.
- **Data cadence** `[выбор, требует симуляции]`: a floor of ~6–8 weeks (earlier, prewhitening consumes the power); adaptive — `insufficient` until permutation on held-out data clears the FDR threshold; timeout ~6–9 months → automatic rejection. Calculate exact numbers through power simulation with real autocorrelation, rather than assigning them.

A recursive guardrail for the harness itself: synthetic control — a planted null **must** fail, a planted signal **must** pass. Otherwise the validator has silently gone stale.

---

## 5. Position: fishing + a generous critic

- **Birth is fishing** (exploratory) `[выбор]`: structure the chaos, look for the personal and nonobvious. Safe because confirmation uses future data.
- **The critic is generous (for now)** `[выбор]`: reuse `critique_hypothesis` (the “argument against” requirement) as discipline *at birth* (generation-time), not as the verdict judge. An “argument against” for a signal = named confounders + a falsification condition + “could this be autocorrelation/coincidence?” The deterministic part of the critic is already in the harness (prewhitening). Generous = let almost everything with a plausible mechanism through; future confirmation is the main filter.
- **The cost of generosity and its sensor** `[выбор]`: generosity places all protection on confirmation + registry-wide FDR. A tripwire metric is mandatory: the `confirmations/declarations` ratio; if it slips toward what is expected **by chance** at the threshold, confirmations = noise, and it is time to tighten the critic. The critic has one configuration knob (strictness), so “tighten later” costs one edit.

Synthesis: fishing produces abundance → the critic cuts it down to a few disciplined declarations → a clock on future data → a deterministic verdict. Volume is controlled by the critic + registry-wide FDR, not belief.

---

## 6. OPEN decisions

1. **What an admitted signal affects:** (a) only deterministic consumers (alerts/cross-checks/convergence for valence/dashboard), never constitutions; (b) + a separate pipe `get_admitted_signals_summary()` with frozen wording into the generator; (c) raw input to the LLM — rejected. Recommendation: (a), then (b) later.
2. **Trust feed:** shared with medical hypotheses (requires normalized cross-kind trust across three incomparable scales: 0–6 / 0–11 / statistics) vs. a separate signal feed.

The 0→5 path **does not depend** on these decisions — it can be built immediately.

---

## 7. First implementation: retrospective longitudinal validation

`longitudinal_analysis.py` is the “first run of the standing gate.” Read-only:
1. Take its current `correlation_matrix` / `lagged_correlations` (raw Spearman, `strong&significant` flags).
2. Run the same pairs through the §4 harness (circular permutation + FDR + temporal held-out).
3. Report: which historical correlations **survive**, and which were artifacts of autocorrelation/multiple comparisons (including those currently flowing into constitutions through `build_ai_summary`).

No production changes. The result calibrates thresholds and proves the approach on your data.
