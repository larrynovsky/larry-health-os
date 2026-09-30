<!-- translation-of: docs/reference/lab_staging_statuses.md sha256:1a35b9fcca6b -->
**English** · [Русский](lab_staging_statuses.md)

# `lab_results_staging` statuses: who advances them and who guards them

> **Reference** (Diátaxis: cognition + application — consulted WHILE working, not while learning
> the system). The explanation of why promotion is manual is in `docs/explanation/`; what to do
> manually is in `docs/how-to/lab_review_queue.md`.
>
> ⚠️ **The coverage map has no second home.** The machine-readable source is
> `integrity_tests._STAGING_STATUS_WATCHERS`; the table below is its human-readable form,
> and they must not diverge. The `check_staging_status_coverage` ratchet fails
> if a status appears in the database that is absent from the map.

## A row's path

```
recognition (lab_backfill._route)
        │
        ├─ passes fully agree + green oracles ──────────→ auto
        └─ otherwise ─────────────────────────────────→ pending
                     │
                     └─ lab_triage.triage(--execute) ──→ rejected | review | gold
                                                              │        │
                                        human in the dashboard ┘        │
                                                                        ▼
                                            lab_promote (button) ──→ promoted → canon
```

## Table

| status | who sets it | who advances it | waiting sensor |
|---|---|---|---|
| `pending` | `lab_backfill._route` (no full agreement, or an oracle flagged it) | `lab_triage.triage(execute=True)` — **manual run, no scheduler** | `check_staging_status_coverage` |
| `auto` | `lab_backfill._route` (agreement + passing oracles) | a person, using the promotion button in the dashboard (`api_lab_review`) | `check_promotion_backlog_stale` |
| `gold` | `lab_triage._classify` (agree + canonical + new + read) | same | `check_promotion_backlog_stale` |
| `review` | `lab_triage._classify` (unmapped analyte, model disagreement, date not read) | a person confirms it in the dashboard | `check_lab_review_queue_movement` |
| `rejected` | `lab_triage._classify` / `lab_promote._apply_rejects` | no one — the decision has been made | none, and that is correct |
| `promoted` | `lab_promote` after writing to the canonical store | no one — the row has moved | none, and that is correct |

## Why promotion has no daemon

Automatic promotion into the clinical canonical store is exactly the danger that staging protects against
(`lab_recognizer::staging_then_human_gate`). The decision to “let these values into the canonical store”
is medical; the oracle is a person (CLAUDE.md §13, the top rung of the ladder).
That is why the guard here watches for “the queue is stalled,” not “the process died.”

## Why status coverage needs a ratchet

Consider a hypothetical queue: a writer assigns `pending`, while
`check_lab_review_queue_movement` queries `review_status='review'` and
`check_promotion_backlog_stale` queries `('auto','gold')`. Neither query covers
`pending`, so a stalled row can remain invisible to both sensors.

The freshness sensor reads the CANONICAL STORE (`MAX(date) FROM lab_results`).
A stale canonical store does not establish that no recent input exists: input
may still be waiting in staging. Interpret freshness together with queue state.

The failure is a mismatch between the key used to query a row and its stored
status. A coverage ratchet following the `producer_registry` pattern catches
uncovered statuses; another manually maintained status list would repeat the risk.
