<!-- translation-of: docs/reference/loinc_attribution.md sha256:b845772d95c0 -->
**English** · [Русский](loinc_attribution.md)

# LOINC attribution

The system uses **LOINC®** (Logical Observation Identifiers Names and Codes)
as the sole home of canonical lab analyte names.

- **Release version:** 2.82
- **Loaded:** the Universal Lab Orders subset (orderable lab
  tests), with fields from LoincTableCore. The full catalog is deliberately not loaded —
  see `loinc_loader.py`, the “GRANULARITY” section.
- **Rights holder:** LOINC is a registered trademark of
  Regenstrief Institute, Inc. LOINC and its associated content are published by
  Regenstrief Institute and the LOINC Committee.
- **Terms of use** were personally reviewed by the system owner on 2026-07-29.

## Why this file exists

Attribution is an obligation, and an obligation without an artifact disappears at the first
refactoring. The presence of the file and the word LOINC in it is guarded by
`tests/unit/test_loinc_loader.py::test_attribution_document_exists`: deleting
the document fails the run rather than going unnoticed.

## What is NOT written here

The full license text is not restated: a restatement of a legal document
is a second home that silently diverges from the original. The original is
the license file inside the LOINC release archive.
