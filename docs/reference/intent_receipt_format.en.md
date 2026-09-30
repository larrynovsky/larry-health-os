<!-- translation-of: docs/reference/intent_receipt_format.md sha256:817ec31d0d52 -->
**English** · [Русский](intent_receipt_format.md)

# Intent receipt format — normative home

> The ONLY normative source of the format (reference, Diátaxis). Ritual skills
> (letitbe, the-end), the registry, sidecars, and gate messages REFER here and do not
> restate the schema. Introduced following external review on 2026-08-07 (F-02: the previous link led
> to a skill outside the repository). Machine guard: `tests/unit/test_intent_receipt_format_doc.py`
> executes this file's examples through the live judge `intentgate.evaluate_files`.

The receipt lives in the `## Замысел` section of the closing artifact (a letitbe plan in `plans/PLAN_*.md`,
a handoff snapshot in `docs/handoff/**`) as a yaml block. Judge: `project_context/intentgate.py`
(pre-commit, exit 7). Rules: R1 section and parsing · R2 id exists · R3 status == the live
registry from the index · R4 `read_at` is an ISO date · R5 `none` requires reason · R6 completeness — the snapshot
carries ALL invariants of the entry (introduced following F-01: an empty dictionary and “one convenient holds” are not
evidence of reading).

## Full form (plans and handoff snapshots)

Exactly all invariants of each affected entry, statuses at the time of reading, and the date:

<!-- example: full-valid -->
```yaml
intent:
  - id: alpha
    invariants: {a_holds: holds, a_open: open}
  - id: beta
    invariants: {b1: holds}
read_at: 2026-08-07
```

## The task does not affect any entry

<!-- example: none-valid -->
```yaml
intent: none
reason: edits ritual documentation only, does not touch subsystem code
```

## Review form (`*.review.md`)

Only ids — the reviewer must read the live registry themselves; the author's statuses would
leak the author's assumptions to the reviewer. Completeness (R6) is not required here:

<!-- example: review-valid -->
```yaml
intent:
  - id: alpha
```

## Failing examples (exactly what is rejected)

A subset is not a snapshot (R6):

<!-- example: full-subset-invalid -->
```yaml
intent:
  - id: alpha
    invariants: {a_holds: holds}
read_at: 2026-08-07
```

An empty dictionary provides zero evidence of reading (R6):

<!-- example: full-empty-invalid -->
```yaml
intent:
  - id: alpha
    invariants: {}
read_at: 2026-08-07
```

A stale or forged status (R3), `none` without reason (R5), and missing `read_at` (R4)
are rejected similarly; see the gate messages for the wording.

## Boundaries (pointers, not a restatement)

The scope of evaluation, index snapshot inheritance, and explicit limits (starting work is not guarded; reading ≠
understanding) are in the intent registry entry `intent_receipts` (`subsystem_intent.yaml`). Outside health:
the letitbe/the-end skills carry this same format as a template; a project with its own registry
creates its own home for the format based on this template.
