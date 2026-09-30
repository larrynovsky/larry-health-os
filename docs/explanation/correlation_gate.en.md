<!-- translation-of: docs/explanation/correlation_gate.md sha256:b8b53d8057dc -->
**English** · [Русский](correlation_gate.md)

# Statistical correlation gate (correlation_gate)

> **Type (Diátaxis):** explanation — why it exists and how it fits in, not a step-by-step how-to.
> **Audience:** an engineer developing longitudinal analysis or constitution generation.
> **Introduced:** 2026-06-22, commit `20cb8da`.

## Problem

`longitudinal_analysis.py` computes Spearman for all pairs of daily metrics
(`correlation_matrix`) and lab↔daily (`lab_metric_correlations`), marks
`significant = p_value < 0.05`, and `build_ai_summary` sends the top results to `agent_reports`,
from which `generate_constitutions._get_longitudinal_context` puts them into the
constitution generator's prompt.

Raw Spearman `p` on daily series is **catastrophically miscalibrated**: the series
are strongly autocorrelated, and two independently "wandering" plots produce enormous
false significance (the Dean & Dunsmuir trap). The `hrv↔sleep_rem` pair at lag 1
is an example pair with a strongly understated raw p: a shift test assesses
significance while accounting for serial dependence. Two more problems: **tautologies** (sleep_score
is computed from sleep stages; readiness from hrv/resting_hr) and **trend confounds** in
labs (two lab indicators, each drifting through disease phases, produce a false correlation with each other). All of this
flowed into the generator unmarked as "key correlations."

## Solution

The `correlation_gate.py` module sits between correlation computation and `build_ai_summary`.
One public entry point — `gate_correlations(daily_df, labs_df, corr_all, lab_corr)` —
returns the same tables with columns `gate_pass`/`p_perm`/`derived`/`coverage`
(daily) and `gate_pass`/`p_perm`/`r_detrended` (lab). Only
`gate_pass` goes into constitutions.

Method (see the specification
`docs/explanation/signal_validation_lifecycle.md`):

- **daily↔daily** — masked circular-shift permutation null **without imputation**
  (each comparison includes only days actually measured) + BH-FDR across the family;
  plus a `derived` filter (the metric is predicted by the others with R²≥0.9 OR belongs
  to the list of Oura composites) and a coverage threshold. Imputation was tried (faster through
  FFT) and **rejected**: on incomplete series, it distorts the null and kills real associations.
- **lab↔daily** — linear detrending of both series over time (removes the shared disease
  trend), then permutation + an n threshold.

## Integration

- `longitudinal_analysis._apply_gate()` — a wrapper extracted from `run()` for
  testability; calls the gate and **degrades loudly** on failure (`gate_applied=False`,
  the job does not crash, correlations fall back to ungated results).
- `run()` calls `_apply_gate` immediately after computing correlations, before Excel and the summary.
- `build_ai_summary` selects by `gate_pass` (falling back to legacy `strong&significant`
  if gate columns are absent) and writes `summary["gate"]` with metadata.
- The Excel sheet "Correlations" and stdout mark the gate's verdict (passed / phantom / derived /
  trend), so the human view does not diverge from what the model sees.

## Hygiene (why it works this way)

- **Degradation sensor.** `integrity_tests.check_longitudinal_gate_applied`
  reads `summary["gate"].gate_applied` and warns if the gate did not run — otherwise
  this would be "detection without delivery" (the flag exists, nobody reads it).
- **Single source for the window.** `LAB_WINDOW_DAYS` lives in `longitudinal_analysis` and
  is injected into the gate as a parameter — to keep lab↔daily pairing from diverging between
  analysis and the gate (split-brain).
- Complements `epistemic_discipline` from the other side: that disciplines the LLM's *language*;
  the gate disciplines its *input* (prevents feeding statistical garbage to the generator).

## Activation

The gate takes effect when `longitudinal_analysis` runs on the new code: launchd
`com.larry.health.longitudinal` (Sun 03:00) or manually via
`python3.11 longitudinal_analysis.py`. Until the first run, the latest summary lacks the
`gate` field and the sensor warns — this is expected.

## Tests

- `tests/unit/test_correlation_gate.py` — a planted real association passes,
  two independent AR(1) series (a phantom) fail, and derived is excluded.
- `tests/integration/test_longitudinal_gate.py` — only `gate_pass` in the summary,
  the legacy branch, loud degradation, a single source for the window.

## Limitations and follow-ups

- Lab detrending is **linear** — nonlinear phase effects are not removed.
- The `derived` filter using R² is **conservative**: with strong collinearity in the panel,
  it may exclude too much (the safe direction — it discards, not invents).
- The gate applies to longitudinal correlations; the broader intent
  (the lifecycle of an arbitrary signal: fishing → critic → confirmation on
  future data) is described in `docs/explanation/signal_validation_lifecycle.md` and
  has not yet been implemented.

## File map

| What | Where |
|---|---|
| Gate (one public entry point) | `correlation_gate.py::gate_correlations` |
| Wrapper + degradation | `longitudinal_analysis.py::_apply_gate` |
| Selection for the summary | `longitudinal_analysis.py::build_ai_summary` |
| Sensor | `integrity_tests.py::check_longitudinal_gate_applied` |
| Lab window source | `longitudinal_analysis.py::LAB_WINDOW_DAYS` |
