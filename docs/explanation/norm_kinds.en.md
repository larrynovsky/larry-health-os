<!-- translation-of: docs/explanation/norm_kinds.md sha256:152b2477114a -->
**English** · [Русский](norm_kinds.md)

# Four kinds of norm and the shelf life of a number

*Document type: explanation. Why a row in `absolute_thresholds` has `norm_kind` and `next_review`, and why “within normal limits” without a kind is not a statement.*
*Schema: `docs/reference/absolute_thresholds.md`; recipe: `docs/how-to/add_clinical_threshold.md`. Thread `norm-provenance`, 2026-09-02.*

---

## Task framing — a paraphrase of the owner's request

The owner is not a medical professional, and that is precisely why the system is needed. The task is to find a methodology or oracle that says what is good for a patient of their type: understand where medicine gets a norm and how it calculates it, record it, and — importantly — update it regularly.

Measurement on 2026-09-02 on canonical: 81 rows in `absolute_thresholds`, not a single review date; the only clinical source in the table was `ESC_AHA_2023` (2 blood pressure rows); 64 rows had `source='safety_net_migration_2026-07-18'` — a receipt for moving them out of code, not a source for the number. `clinical_kb`: `review_date` 0/75. Provenance columns had been built everywhere; content was present nowhere.

## Medicine stores not one number but four different objects

The project flattened them into one `value` column. The difference is not cosmetic: they have different oracles, different update methods, and different answers to the owner's question.

**1. Reference interval** (`reference_interval`). The central 95% of the reference population's distribution; under CLSI EP28-A3c, at least 120 healthy subjects, nonparametric 2.5–97.5 percentiles, partitioning by sex and age. A laboratory must establish its own interval or verify a transferred one for its method and instrument — so it **differs across laboratories** and is printed on every report. It answers “is this unusual for a healthy person?” **It does not answer “is this good for you?”**

**2. Decision threshold** (`decision_threshold`). A number derived from outcomes, not a distribution: glucose ≥ 126 mg/dL is an ADA criterion for diabetes, not the upper reference limit (70–100). A committee sets it and publishes it as a named document with a version and review cycle; a new version explicitly supersedes the previous one. This is where “normal according to the laboratory” and “concerning according to the guideline” can legitimately differ.

**3. Target stratified by patient type** (`stratified_target`). The same analyte has different targets depending on risk category, stage, and treatment history. This is the layer that answers “for a patient like me.” It is not derived from data — it is selected based on a profile fact declared by a person (`stratum`); a physician assigns the category. A wholly invented example: a target for `Example_A` applies to `Example_Stratum`. In the system, the stratum is matched against the tenant's `clinical_kb` data, and the target number must come from a physician's document (`data/norm_docs/stratified_targets.json`). For a tenant without the corresponding stratum, the row is disabled.

**4. Personal norm** (`personal`). Reference Change Value: analytical CV + within-individual biological variation (EFLM publishes CV_I/CV_G by analyte). It provides two things: when a shift in a series exceeds noise, and which analytes have population intervals that are useless (creatinine, TSH, ferritin — a personal baseline beats the population interval). The project's `p10_personal`/`p90_personal` is a naive predecessor: a percentile over 90 days does not separate biology from analytical noise.

The status, stated honestly: items 1 and 4 were verified against sources when the thread was put together (CLSI EP28-A3c; PMC10197385; CCLM 2018-0059). Items 2 and 3 are established knowledge; exact guideline review cadences must be rechecked during implementation, not copied from here as facts.

## Why “kind” is a safety predicate, not a tag

Comparison of thresholds with the interval printed by the laboratory (2026-09-02): HGB warn 12.0 versus 13.5–17.5; CRP 10 versus 0–5; LDH 250 versus 135–225; Glucose 126 versus 70–100. The Glucose discrepancy is **legitimate** — it is a decision threshold. For HGB/CRP/LDH, **the kind is not recorded, so a machine cannot distinguish a legitimate difference from an error.** A discrepancy sensor could not be built because there was nothing to compare: there is no reference against which to check a number without knowing what kind of object it is.

With `norm_kind`, a predicate becomes possible: a norm of kind `reference_interval` that differs from what the laboratory prints in ≥3 documents is red (`check_norm_vs_lab_reference`). For `unclassified`, the same calculation is printed as a proposal: matches the laboratory → candidate reference interval; differs → decision threshold or personal norm. This turns 60+ human verdicts into reviewing proposals rather than working from scratch.

## How a norm is updated — and why not automatically

In medicine, a norm is updated not by continuous computation but by a **named document with a date and next review, plus a person whose job is to check it.** Hence `next_review`: not “when the system will recalculate,” but “when a person must recheck the document.” Overdue means red for the owner; this is “update regularly” translated into a mechanism.

Automatic updates from the literature were rejected. The path exists and works (`literature_search`, `literature_curator`, `publication_reading`, freshness sensor ≤10 days) — and none of them has ever moved a single threshold (measurement of `agent_reports`). Extracting a number from a guideline using a model is a class of task that produces confident nonsense. The pattern: the machine notices an overdue review and prints a proposal; **a person confirms the number.** In consistency terms, the norm is a replica of an external document, and the only applicable axis is staleness; a staleness bound requires a pull model, while pushing from an untrusted source is impossible by construction.

A single `next_review` is deliberately not assigned to all 67 rows during migration: 67 red alerts on one day mean banner blindness (§13), and the sensor would die on the day it was born. A person assigns the deadline together with the kind.

## What changed after researching sources (thread norm-from-documents)

The April thresholds turned out to be half-remembered CTCAE: HGB 12/10/8 were anemia grades, PLT 100/50/20 instead of 75/50/25, ALT “3x/7x” instead of 3x/5x/20x. The right document exists, is open and versioned, and expresses grades **as multiples of ULN/LLN** — composing with the report's reference interval rather than replacing it. Now `safety_net` rows are derived from the CTCAE xlsx (`norm_documents`), the reference interval comes from the report row being assessed, and an analyte without a document is assessed using the report itself (kind 1). For tumor markers absent from CTCAE, the manually entered urgent/critical thresholds are gone — their changes are assessed using RCV from EFLM meta-estimates (CV_I CEA 6.8%, CA 19-9 4.3%; RCV ≈ 18% and 11%), rather than 50% from memory. The only human in the loop is the doctor reading the brief; there is no person between the document and the database: retelling was precisely the step where accuracy was lost.

## Explicitly stated boundaries

- The witness is itself derived (§17): `ref_low`/`ref_high` from PDF parsing. The modal interval across ≥3 documents mitigates this but does not eliminate it; as of 02.09, two rows in the canonical store had `ref_low ≥ ref_high`. The witness's reliability has not been measured separately.
- A machine cannot catch false agreement between the witness and an incorrect human label: the sensor does not check a reference interval labeled as a decision threshold.
- `norm_kind` lives in the tenant's database: a second tenant inherits `unclassified`; the owner's verdicts do not travel to it. Norms as shared objects across tenants are a separate question (`BL-TARGETS-1`).
- The fifth home of kind 1 norms was closed (thread lab_refs, 02.09): the unsourced `LAB_REFS_CANONICAL` literal “for a male” was removed; `system_config.lab_refs` is now **a cache of report modes** (`labs_db.compute_bank_refs`, ≥`norm.witness_min_docs` distinct documents, recalculated on every `init_db`). An analyte without a mode does not enter the cache; readers (gp_context, wellally_consult, /report) take the reference interval from the report row, then from the cache, otherwise printing “reference interval not established.” The sensor `check_threshold_source_is_document` turns red on a cache entry without source `bank_modal` or with n_docs < min_docs — that is, on the former literal. Residual limitation: the mode is the interval from the most frequent laboratory, not the “correct” one; switching laboratories will shift it along with the data.
- Layer 3: every target requires a physician’s decision recorded in a document; the machine does not choose the target.

## The sixth object — not a norm but a “no norm” verdict (2026-09-03)

The four kinds of norm describe which number to use for assessment. But the absence of a number
also requires a considered verdict. A wholly invented report example: the calculated ratio
`Example_A_B_ratio` is printed without a reference interval, while its components `Example_A`
and `Example_B` have their own intervals. Checking the report itself distinguishes a missing
reference interval from a recognition loss; norm documents must be checked separately.
If no applicable norm exists or a decision is deferred until a consultation, the verdict needs
its own home and review date. Otherwise, the coverage sensor will keep repeating the same question as new (§13).

The home is the `analyte_norm_verdicts` table (owner's decision: a table in health.db next to the norm,
in the R1 backup, per tenant — not `parked_decisions` JSON, which stores night-cycle gates rather
than domain decisions). Two kinds, and **both have a review date**: `deferred` — awaiting an oracle;
`no_norm` — no norm by decision; the analyte is assessed through its components. The date is mandatory for
the second kind too: the laboratory may start printing a reference interval, and the verdict must not outlive its subject
(§18 — a statement about something outside its carrier must have an invalidation mechanism). The reader
`labs_db.analyte_norm_verdicts` does not return an expired verdict, and the sensor sees the analyte again — this is the invalidation mechanism,
not a defect. How to record one: `docs/how-to/record_analyte_norm_verdict.md`.

## RCV without a magnitude floor: confirmation instead of a threshold (2026-09-03)

Hypothetical example (numbers invented, the form of the case is real): a tumor marker rose from 1 to 2 U/mL — +100% with
an RCV threshold around 11% — and the trend returned “urgent” on two values at the very bottom of the scale (a few percent of ULN), where analytical variability is several times greater than the assumed 0.5·CV_I
(Trapé 2010, CCLM 48:1799: CV_A 19% at 0.65 versus ~7% higher up; Petersen 2005, PMC1320174: RCV
assumes homogeneous variance across the scale). The obvious fix — a “magnitude floor” — has no
open document behind it: LoQ/functional sensitivity belongs to the laboratory's analyzer,
which we do not have; NACB 2008 is behind a paywall, and we do not cite it from memory.

The document we do have solves the problem differently: EGTM 2014 (Duffy, DOI 10.1002/ijc.28384) —
*“Any increase in levels must be confirmed with a second sample prior to undertaking further
investigations”*. A rise between two points is a reason for repeat sampling, not action.
A random jump at the measurement floor will not repeat; a real rise will. The rule lives
in `schedules.json::rules` with a quotation and a metric list (CEA, CA19-9); the reader is
`safety_net.check_lab_trends`: threshold severity only for a rise sustained across the two
latest points relative to the point before them; a single sample gives a “confirm” warn.
This tightening applies to both tenants and changes the safety_net golden tests — as it should:
a golden test assesses a snapshot of the document, and the document has changed.

Explicit boundaries: the EGTM rule is formulated for CEA in colorectal cancer; it is extended to CA19-9 based on the same
document (CA19-9 is “emerging” there for colorectal cancer surveillance) and the general logic of RCV. The rule does not apply to HGB/MCV/
Albumin — their RCV trend from a single pair remains as before (warn severity).
There is still no numerical floor; if a laboratory document with LoQ becomes available, it will be a second,
independent protection, not a replacement for this one.

### Amendment that same evening: a magnitude floor from the owner's word, not a document

The EGTM rule alone still left a “confirm by repeat sampling” warn for such a case. The owner
stated a criterion (paraphrase, 03.09.2026): fluctuations near the normal boundary — within 20% of it — must
reach a person; if the lower bound is zero, only the upper bound matters. This is a decision threshold with the owner as oracle
(§9, the first home is the thresholds table), not literature: the column `lab_trend_thresholds.near_boundary_share`
(0.2) with `near_boundary_source='owner_word:2026-09-03'`; the seed fills only empty values. The reader is
`safety_net.check_lab_trends`: RCV is assessed when the latest point ≥ (1 − share)·ULN from its report.
Interpretation (a), confirmed by the owner: the rule applies only to intervals with a zero/missing
lower bound (tumor markers); two-sided intervals (HGB, Albumin) are untouched — “20% from the boundary” would
cover almost the entire reference interval for them. Without a reference interval for the point, there is no floor — loud, as before.

The cost of the rule (hypothetical example, numbers invented): with ULN 40, a tumor marker rise of 10 → 20 (+100%) will not arrive as a trend;
the first signal will be entry into the ≥ 32 (0.8·ULN) zone, followed by EGTM confirmation. Crossing the boundary itself
is caught by the kind 1 sensor. The two filters are independent: the floor determines where to assess; confirmation determines when to act.
