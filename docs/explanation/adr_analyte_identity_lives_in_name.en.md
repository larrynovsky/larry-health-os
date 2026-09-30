<!-- translation-of: docs/explanation/adr_analyte_identity_lives_in_name.md sha256:a4037962c919 -->
**English** · [Русский](adr_analyte_identity_lives_in_name.md)

# ADR — analyte identity lives in the NAME (name + specimen + dimension)

> Status: **accepted by the owner on 2026-08-12.** Explanation (Diátaxis), not a rule in the rule set.
> Enforcement: **built the same day** — the `lab_canon.identity_name` mechanism
> (one home: sensor + promotion guard + boundary migration predicates) and the §16
> identity clause in the rule set; the clause's Enforcement points to live tests. The previous
> version of this header ("not yet") was removed when the mechanism was built — the rule and mechanism
> arrived in one commit, as promised here.
> Related: thread `data-ingestion@2026-08-12-600be72`, `loinc-name-home@2026-07-31-e9978f6`.

## Context

FU2 brings rows awaiting the canonical store from the specialized layer into `lab_results`. Canonicalization must account for this:
**a measurement is defined by three attributes — name + specimen + dimension, not by name.**
The following are independently invented examples using fictional indicators, not content from anyone's forms:

- **Example_A** appears in `%` and in volume units. Dimension distinguishes them.
- **Example_B** denotes both the concentration of a fictional substance and a relative antibody signal against it. The measured object and dimension distinguish them.
- **Example_C** is measured in two different specimens under the same name. Specimen distinguishes them.

A computed `dimension_key` is fragile when a distinction is undeclared: `%` and concentration
may receive the same key. The canonical name must preserve this distinction.

## Decision

**Identity lives in the NAME (data), combined with a derivation rule.**

- The canonical name CARRIES the distinguishing attribute where needed: `RDW` vs `RDW_SD`,
  `WBC` vs `Urine_WBC`, the hormone `Insulin` vs the autoantibody `AntiInsulin` (immunoreactivity class).
- The distinguishing suffix **is derived by a rule** from the unit/specimen where the rule is unambiguous
  (`фл` → `RDW_SD`; urine specimen → `Urine_` prefix). The rule's structure is in code (`lab_canon`).
- Where the machine cannot decide (what does an analyte in an unexpected unit, e.g. `%`, mean? — the lab form answers): **owner gate**;
  the decision and the resulting name belong in a versioned data home, not in code.

This is exactly the §9 division: the structure of judgment in code, values (names) in data with an oracle.

## Options considered

**A. Identity in the name (accepted).** Name = identity; the trend is keyed by name, with the distinction built in.
Advantage: forgetting the specimen/dimension is IMPOSSIBLE — they do not exist separately from the name; any reader keyed
by name is automatically correct. Disadvantage: longer names; bare names with an implicit specimen (`WBC` = blood
"by default") require an explicit migration; more naming decisions up front — mitigated by the derivation rule.

**B. Identity in the key (code), bare name — REJECTED.** Names stay clean, but EVERY reader must
remember the three-part key. A forgetful reader (§16/§17) may merge different measurements.
An undeclared dimension distinction makes the computed key unreliable.
This option permanently makes an implicit convention part of the architecture — the same second logical home
the project has already paid for.

## Consequences

- **Positive:** different dimensions, specimens and measured objects receive
  different canonical names. This prevents a relative signal from being merged with
  a substance concentration, or measurements from different specimens sharing one name.
- **Negative (stated honestly):** migration of existing bare names with implicit specimens (the cost has NOT
  been measured — the resolver's first step is to MEASURE the impact before applying it); more owner gates for
  ambiguous names (mitigated by the derivation rule for unambiguous ones: `фл`→SD, urine→Urine_).
- **Known pitfall:** `dimension_key` is blind to `%`/concentration in undeclared pairs — the resolver must
  carry a guard (using `is_dimensionless`, like the `check_specialized_canon_waiting` sensor), not rely on it.

## Enforcement (built 2026-08-12, in the same commit as this revision)

1. **Mechanism:** `lab_canon.identity_name(name, unit)` — normalization + dimension
   suffix + bare `%` guard; None = "cannot decide," the safe side. Callers:
   `check_specialized_canon_waiting` (sensor), `lab_specialized._in_canon` (promotion
   guard; the key retains date+source — R3), predicates B/B2 of the
   `canon_domain_leak_20260801` migration (cross-source B2 — only when values are exactly equal).
2. **Clause in the rule set:** CLAUDE.md §16 "Analyte identity clause," with Enforcement
   pointing to live tests (Insulin %/RDW/urine-WBC pitfalls; guard mutation executed: 4 failures).
3. The derivation rule for the remaining naming debt (`фл`→`RDW_SD`, urine→`Urine_`) has NOT
   been built yet: this is work for the naming stage, a separate pass of the thread.

## Addendum 2026-08-31: urine domain naming convention — `Urine_*`

Owner's decision: the `Urine_*` prefix. An independently invented failure example:
`Urine_Example_A` in one unit and `Example_A` in another with `specimen='urine'`
may denote one measurement arriving through two promotion paths. The prefix must
survive unit conversion; no actual panel or record is described here.

This does NOT supersede this ADR. The `specimen` column remains mandatory and
authoritative for the specimen; the prefix is part of the urine analyte's NAME: a substance in urine is
a different measurement with different reference ranges, not a conversion of the serum measurement (the same argument
that has separated `Urine_WBC` from `WBC` since 2026-08-08). The removed `Methylmalonic_acid_serum`
does not return: blood has no prefix; blood is the unmarked case.

Carriers: `migrations/urine_name_convention_20260831.py` (one-time consolidation of
stored records), `integrity_tests.check_urine_name_convention` (ratchet against a return to
two conventions), registration of `Urine_*` canonical names in `lab_canon._SYNONYMS`.
