<!-- translation-of: docs/explanation/lag_directed_validation.md sha256:f7fbae2ec139 -->
**English** · [Русский](lag_directed_validation.md)

# Validation of the directed lag null (Group 2 part 2 fdr-online)

Diátaxis: explanation. Load-bearing → git. Thread: `fdr-online`. Code: `correlation_gate.py`
(`_lag_within_epochs`, `_strat_pvalue_lagged` — DORMANT); twin: `methodology/validation_gate/twin_lag_directed.py`.
Rationale for the choice of null (why not prewhitening/state-space) — in the specification `null_gate_spec.md` §3.3.2 (not duplicated here).

## What was validated and why

Leading indicators `predictor@T → target@T+lag` (sleep today → HRV tomorrow, etc.) are declared in
`signal_family.yaml` as a first-class hypothesis category, but with status `pending_harness_validation`: naive
`lagged_correlations` computes them with Spearman and `p<0.05`, and that p is **invalid** under serial dependence
(both series are autocorrelated; Romano–Tirlea — permutation of independent but serially dependent series is not level α).
Lag therefore remains **outside belief** until an honest null for a directed delay is proven. That was the question.

## How it was validated

The same principle as family A: **stratified circular shift within epochs** (strat_hi), but on
a directed pair — `predictor@T` against `target@T+lag`. The key subtlety: the target shift is performed **within
the epoch** (the last `lag` days of the epoch → NaN), otherwise the shared regimen level of the neighboring epoch would leak into the test
across the boundary. Prewhitening and state-space were rejected in advance for confirmatory use (specification §3.3.2: at φ≈0.97
they fail in both directions — power→0 for a slow signal ∨ anticonservative under an incorrect AR order).

The twin (`twin_lag_directed.py`) runs the **production** function `_strat_pvalue_lagged` (not a surrogate — otherwise
we would repeat the `exp_family_a_mechanism` trap, where the wrong statistic was validated) on synthetic data with the real
epoch grid: condition H0 (no lag, x⊥y + shared epoch drift η) gives type I error for each lag; condition H1
(`y[t] ← β·x[t-lag]`) gives power.

## Result (2026-07-23) — verdict SEPARATELY for each lag

All three lags passed: **type-I@0.05 stays at nominal within the ±2·MCSE band across the φ×η grid for lags 1, 2, and 3**
(observed range 0.013–0.073). What matters: the φ=0 / η=0.8 corner, where family A's contemporaneous null
drifted slightly (type-I≈0.10), is **clean** for directed lag: lag within the epoch + NaN at the boundary remove that leakage through
the global rank. Power against a planted lag (β=0.4, φ=0.6, η=0.8) = 1.00 for all lags.

Conclusion: the directed stratified shift null is **valid for delays of 1–3 days** in these data regimes.
No structural exclusion of lags was needed (unlike family A's slow×slow).

## Boundaries of honesty (what this step does NOT do)

The **tool** (null) was validated, not the "truth" of directed causality — there is no truth oracle at N=1.
The `_strat_pvalue_lagged` engine is **DORMANT**: NOT wired into `gate_correlations`; lag does not enter belief/the brief.
Promotion requires a separate decision — the **multiplicity budget m** (+~51 directed hypotheses changes the
false discovery threshold): a new family `q_lag` or a directed subfamily of A. This is the owner's oracle, and exactly what
`pending_harness_validation` still awaits. Naive `lagged_correlations` remains fenced out of belief.

## Reference — what lives where

- **Statistic** (dormant): `correlation_gate._strat_pvalue_lagged(ra, rb, epi, tau, c_star, B, rng, lag)` —
  strat_hi on `ra@T ↔ rb@T+lag`, lag through `_lag_within_epochs` (within the epoch).
- **Family**: `signal_family.yaml → lagged` (predictors×targets×lags, `harness_validated: 2026-07-23`).
- **Calibration**: `twin_lag_directed.py` (H0 type I error + H1 power, φ×η grid, MCSE).
- **Test guard**: `tests/unit/test_lag_directed.py` (helper correctness, seed controls, dormant fence).
- **Promotion gate**: budget m (owner) → then wiring into `gate_correlations` + BY on the lag family.
