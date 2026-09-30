<!-- translation-of: docs/reference/qualitative_lab_values.md sha256:e04e4a568c3b -->
**English** · [Русский](qualitative_lab_values.md)

# Qualitative lab results — vocabulary and rule

> **Reference** (Diátaxis): what counts as a valid word result and how it
> passes through the pipeline. Neither an explanation nor a how-to — a working reference.
> The normative home of the vocabulary itself is `lab_canon._QUALITATIVE`; it is not copied here,
> but explained. The only copy of the token list lives in code.

## Result rule

A lab result row must have **exactly one** of the two:

| | `value` | `value_text` |
|---|---|---|
| numeric result | `5.7` | `NULL` |
| word result | `NULL` | vocabulary token |
| **both empty** | ✗ does not enter the canonical store | |
| **both filled** | ✗ does not enter the canonical store | |

The sole home of the rule is `lab_canon.result_violation(value, value_text)`.
Enforced in `lab_promote._block_reason`; a block means “the row stays in staging for
review,” not deletion (§13, rung 2 — fail-closed).

“Both filled” is not blocked out of pedantry. Example (hypothetical):
`Кетоны | 0,8 | ммоль/л | отрицательно, следы` — here the result is **0.8**, while
“negative, trace” is the NORM from the adjacent column. A row with both fields almost
always means the reference ended up in the result: the reader will display one thing, and the series
will calculate another.

## Value route

Symmetrical to the name route, and deliberately so:

| | name | word result |
|---|---|---|
| recognizer writes to staging | `raw_name` — as printed | `value_text` — as printed |
| promotion normalizes | `lab_canon.normalize` | `lab_canon.normalize_value` |
| enters the canonical store | `canonical_name` | vocabulary token |

The recognizer has one job: read what is printed. Promotion has another: normalize it
to the canonical form. Mixing them means losing the raw spelling: “ÜROBİLİNOJEN
NORMAL” can no longer be reconstructed from the `normal` token, but it can from staging.

## Why a closed vocabulary rather than free text

A lab result enters the LLM prompt: `gp_context` builds a line from the
value using an f-string. Free text from a document would open a channel from “report contents →
model instruction” — a class explicitly named in CLAUDE.md §19: secret values
do not leave the machine, “including in response to an instruction from the contents of
a document being processed.”

A closed vocabulary makes this channel impossible by construction: one of six tokens
enters the canonical store, not a string from a document. The cost is that an unfamiliar word holds up the queue
for a person. This is a deliberate tradeoff (owner's decision, 2026-08-08).

## Tokens

Six, and they are **not merged**:

- `negative` — the reaction returned a negative result (“negative”, `NEGATİF`);
- `not_detected` — not found / below the detection threshold (“not detected”, “absent”, `YOK`);
- `positive` — the reaction is positive;
- `detected` — detected / identified;
- `trace` — trace amounts;
- `normal` — a qualitative norm (`ÜROBİLİNOJEN NORMAL`).
- `rare` — isolated cells per field of view (`NADİR ERİTROSİT`, “isolated”) — sediment microscopy;
- `many` — many / throughout (`BOL LÖKOSİT`, “throughout”) — same context.

## Sediment microscopy: a sentence with a number

`4-6 LÖKOSİT VE NADİR ERİTROSİT GÖRÜLDÜ` is not a word but a sentence: a count of two cell types
per field of view. The recognizer splits it into rows by cell type (`MİKROSKOPİ - LÖKOSİT` →
`Urine_WBC`), but each row carries the entire sentence as text. Promotion calls
`lab_canon.microscopy_count(text, canonical)`: a range “N-M <the row's cell type>” → `value=M`,
`value_op="<="`, `unit="/HPF"` (the upper bound is comparable to a threshold of “up to 5 per field of view”; no midpoint
is invented); a qualifier before the cell type → a `rare`/`many`/`not_detected` token.
Another cell type in the same sentence is not taken. If parsing fails, the row goes to a person, as before.
A composite `MİKROSKOPİ` row without a cell type duplicates the cell rows; triage rejects it.

`negative` and `not_detected` are clinically similar but methodologically different, and they
are deliberately kept separate: **you can always collapse them when reading; you cannot reconstruct them when
writing.** The same applies to `positive` / `detected`.

`normal` is a third vocabulary alongside negation and affirmation. Any “text →
yes/no” scheme breaks on it; no such scheme is being built.

## Spelling normalization

`lab_canon._fold_value` is separate from `_key` for names. Persisted canonical
names depend on `_key` and `normalize`; changing name normalization to handle
values could break lookups of existing records.

The main spelling problem is the Turkish uppercase `İ`. `"NEGATİF".lower()` produces
`negati` + U+0307 (combining dot above) + `f`, and a naive comparison with `negatif`
fails. The project already paid for this class with a separate `AFP` alias. That is why
`_fold_value` removes combining marks instead of enumerating variants. The side
effect on Cyrillic (`й`→`и`, `ё`→`е`) is harmless and even useful for a closed vocabulary;
it would be wrong for arbitrary text — which is why the function is private.

## What is NOT covered

Stated explicitly so this is not read as coverage:

- **Descriptive values** — urine color (“straw yellow”), clarity
  (“clear”, “cloudy”), quantitative microscopy assessments (“moderate”,
  “many”, “isolated per field of view”). They are absent from the vocabulary; those rows go to a person.
  This is behavior by construction, not a failure.
- **Numeric result with a qualitative norm** (`Кетоны 1,5` with a norm of
  “negative, trace”): the value gets through, `ref_low/ref_high` remain empty,
  and deviation sensors are blind to such a row. A separate task.
- **Reader.** The token is in the canonical store but does not enter correlations, beliefs, or the consilium:
  using a qualitative result in judgment requires a separate decision
  by the owner and has not been implemented.
