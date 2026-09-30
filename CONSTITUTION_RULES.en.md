<!-- translation-of: CONSTITUTION_RULES.md sha256:8d5b3591e5e6 -->
**English** · [Русский](CONSTITUTION_RULES.md)

# Rules for writing health constitutions

Constitutions are narrative documents about how the body IS BUILT (an «operating manual»),
written through the SWOT framework. They are rebuilt only when there are new inputs (see «Update triggers»).

## Horizon (owner's decision 2026-09-25)

A constitution includes only what has a **significant consequence that lasts at least a year** (the horizon is
`system_config.constitutions.horizon_days`, 365 by default). What is measured is the duration and significance
of the consequence, not the duration of the event: an operation is a one-day event, but its trace in sleep lasts
for years, so it is included as a cause. Not included: individual nights and weeks, trips, current
protocols and supplements, fresh lab values, near-term plans and deadlines ahead — that is
the operational layer, and its home is the monthly report and the consilium.

In practice:
- a date younger than the horizon in a constitution's text is a violation (such an event could not yet have had
  a year-long consequence); the check is `generate_constitutions.recent_mentions`; on a violation there is one
  attempt to fix it, after which the constitution is not saved;
- recommendations are only those that follow from patterns at least as long as the horizon.

> How to run the generation: [docs/how-to/update_constitutions.md](docs/how-to/update_constitutions.md).

## Five domains

| File | Domain | Key genes |
|------|-------|---------------|
| `constitutions/sleep.md` | Sleep | PER3, CLOCK, MTNR1B, CRY1, ARNTL |
| `constitutions/nutrition.md` | Nutrition | MTHFR, FUT2, VDR, BCMO1, APOE, TCF7L2 |
| `constitutions/stress.md` | Stress | COMT, MAOA, SLC6A4, FKBP5, BDNF, NR3C1 |
| `constitutions/nervous_system.md` | Nervous system | APOE, BDNF, COMT, DTNBP1, NRG1, SLC6A4 |
| `constitutions/movement.md` | Movement | ACTN3, ACE, PPARGC1A, IL6, ADRB2, MCT1 |

---

## Structure of each document

### Header
```
# Constitution: [Domain]
**Updated:** YYYY-MM-DD
**Data sources:** genetic_variants [N SNP], longitudinal analysis [N years]
```

### SWOT

**S — Strengths**
Genetic advantages (Good repute, magnitude ≥ 1.5), protective patterns
in the historical data. Write them as facts that work «by default».

**W — Weaknesses**
Unfavourable variants (Bad repute, magnitude ≥ 2.0), chronic deficits.
Write without catastrophizing — this is not a diagnosis, it is a starting position.

**O — Opportunities**
Modifiable factors where genetics gives leverage: what exactly is worth doing,
given the specific SNP profile. Concretely, not in general.

**T — Threats**
Risks of magnitude ≥ 3.0, triggers of worsening, cumulative interactions.
Only what requires monitoring or preventive action.

### Narrative
Two or three paragraphs of free text that tie the SWOT into one picture.
Written in the first person («my chronotype...», «structurally, I...»).
This is not a medical report — it is self-understanding.

### Baseline recommendations (MANDATORY SECTION)
These are practical rules that follow from the SWOT + the correlation analysis.
Not «in general» advice — concrete thresholds and operating rules derived from
patterns at least as long as the horizon (not from the last weeks and not from the current protocol).

Structure of each item:
```
- **[Rule]:** [specific action or threshold]
  *Why:* [mechanism from data/genetics, one sentence]*
```

Examples of good ones (only the FORM is shown; N, M, K are placeholders — in the constitution put numbers from the tenant's data):
- **Activity threshold:** no more than N steps without an unloading day afterwards
- **Recovery KPI:** recovery_high_min above M min/day — a priority over steps
- **Regularity:** a bedtime window of ±K min 7 days a week — if the tenant's multi-year data show
  that variability of the phase costs more than short sleep

An example of a bad one (operational, not structural): «a 4-hour night last week → keep
the current supplement protocol until the end of the year».

5–8 items. Concreteness matters more than completeness — 5 clear rules are better than 10 general ones.

### Recommendations for specialists
Three to five short points for a lifestyle coach / specialist / GP.
Format: «Given [genetic factor], the priority is [concrete action]».

---

## Tone and style rules

- **Narrative, not a table.** Data only as context, not as content.
- **No medical diagnoses.** A constitution describes predispositions, not diseases.
- **Concreteness.** Every statement is tied to a specific gene or data pattern.
- **Living language.** It reads like a well-written essay, not like a clinical protocol.
- **First person.** «I have», «my», «to me» — not «the patient has».
- **No doom.** Bad variants are levers for action, not a sentence.

---

## Data sources for generation

The generator (`generate_constitutions.py`) aggregates four sources:

1. **Genome** — the `genetic_variants` table, filtered by the domain's genes (with SNP=0 generation stops)

   Variants go through **carrier-status filtering** (`_get_snp_data`, 2026-06-26):
   - `resolved` / `palindromic_het_resolved`: a variant is included in bad/good only if `effect_allele` is part of `genotype` (the person carries the allele). Non-carriers are excluded.
   - `palindromic` / `multiallelic_ambiguous`: carrier status cannot be determined → placed in unknown.
   - `no_call` / `no_data` / `source_conflict`: no genotype or a data conflict → skipped.

2. **Longitudinal analysis** — the belief `belief_contract.read_belief()` (10 years of correlations after the gate; if absent — generation on the genome with a note). What is passed: the data range, phases within the horizon (`phase_in_horizon`: without trips and without phases shorter than the horizon that touch the last year), the yearly trend without the current incomplete year, links. `recovery_vs_baseline` is NOT passed — its «current» is the last 90 days.
3. **Clinical history** — the `periods` table, filtered by medical types (surgery, treatment, chemoradiation, immunotherapy, recovery, relapse, remission, etc.)
4. **Lab series at least as long as the horizon** — `labs_db.build_lab_history_context(horizon_days=…)`: ≥3 points, from the first to the last at least the horizon; without a «current» status.

> ⚠️ `promethease_variants` is an empty table (0 rows), not used.
> NOT passed (since 2026-09-25): chat notes (`patient_context.reasoning_block`), «tested once», special panels, current lab values — the operational layer.
> The previous constitution is read from the DB (`health_db.get_constitution()`) and passed only to a separate diff call (`_build_diff_prompt()`), not to the main prompt; the diff is not made if the earlier version violates the horizon.

---

## Update triggers

Weekly (launchd, Sun) `generate_constitutions.py --if-changed` compares the fingerprint of the inputs
(`inputs_snapshot`: links in the belief, phases within the horizon, lab series at least as long as the horizon, complete
years of the trend, medical periods, genome size) with the previous one; if it matches, there is no rebuild. Short
metric changes and new individual lab tests do NOT wake a rebuild (owner's decision 2026-09-25).

Manual command: `python3 generate_constitutions.py [domain]` or `all` for all five.

---

## Versioning

The files are kept in `health_scripts/constitutions/`.
Git tracks the history — previous versions are available via `git log`.
The update date is recorded in the header of each file.


---

## Epistemic discipline (auto-injection)

Since 2026-06-20 a confidence-calibration block is automatically added to the generation prompt
(`epistemic_skill/discipline.txt`) behind the `EPISTEMIC_DISCIPLINE` flag (default ON).
It governs the wording of interpretations and trends (N→confidence, «noise zone»,
anti-patterns AP-1/2/7/9, |r|≥0.95=artifact), but NOT the numeric thresholds of the section
«Baseline recommendations» — there the values stay hard.
More: [docs/explanation/epistemic_discipline.md](docs/explanation/epistemic_discipline.md).
To switch off for a run: `EPISTEMIC_DISCIPLINE=off`.
