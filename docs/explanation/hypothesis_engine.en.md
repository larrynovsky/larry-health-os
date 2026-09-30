<!-- translation-of: docs/explanation/hypothesis_engine.md sha256:db6be1ab4f18 -->
**English** · [Русский](hypothesis_engine.md)

# Hypothesis engine: generation, lifecycle, convergence

> **Document type:** Explanation (Diataxis) — intent, not a recipe.
> Controls: dashboard (hypothesis cards, `confirm`/`reject`/`eval` buttons),
> Telegram (`/hypotheses`, `/confirm`, `/hreject`, `/hyp <id>`).
> Storage schema (`memory` category=`hypothesis`, `hypotheses_cbcr`): [ARCH_SNAPSHOT.md](../../ARCH_SNAPSHOT.md).

**Idea.** The system formulates hypotheses about a patient's health **from their data** (metrics,
labs, correlations, drift) and brings them to a conclusion through repeated consilium evaluation.
This replaced the old "experiments" mechanism (see the tombstone below).

---

## Generation

Hypothesis = `{observation, mechanism, prediction, test}` in `memory`
(`save_hypothesis`, `status="open"` by default). Sources (`trigger`):

- **`monthly_consilium`** (primary, 1st of the month) — a panel of 11 agents
  (9 physicians + 4 lifestyle) reads the data and produces consensus hypotheses. The full
  analysis is in `hypotheses_cbcr`.
- Secondary: `drift` (metric streak ≥3), `correlation_drift`, `specialist_review`,
  `literature`, `survivorship`, manual entry.

Correlations in consilium input come ONLY from accepted belief (`belief_contract`, through the same
reader `gp_context._build_longitudinal_correlations_block` as the GP). Before 2026-09-03, the input
printed `hai_analysis.detect_correlation_drift` (Spearman 90/90 without permutations or FDR,
including derived pairs): the consilium received `r` drift for steps↔active kcal —
a pair the gate's `derived_filter` excludes by construction — and the Movement Coach inferred
a connection to a lab indicator and a gene. This is a second producer of `r` bypassing the gate; UC-B-09 caught exactly
this case. The intent had already classified daily correlation drift as noise (`drift_birth_defunct`).
Empty belief is supplied as an explicit prohibition, not silence. Oracle:
`tests/unit/test_consilium_correlations_from_belief.py` (negative control: the drift
detector returns a finding, but it is absent from the input).

**Deduplication is semantic** (`hypothesis_semantic_check`, Haiku): before saving,
the candidate is compared by MEANING with open/recently rejected hypotheses. Duplicate → skip
(audit in `dedup_skipped`, `active=0`). Prevents the consilium from creating copies of a hypothesis
already being tracked.

---

## Lifecycle and TWO meanings of "confirmed"

Key point: the dashboard has **two different** confirmation buttons — do not confuse them.

**1. `confirm` (quick mark, `api_status_actions`).**
"I accept this hypothesis for testing" → `status="confirmed"`, `resolved_as`, `active=1`,
+ creates a follow-up **task** from the `test` field (get lab tests / take action).
**NO protocol is created. NO round runs.** This is the owner's personal triage.

**2. `eval` (consilium → `resolve_hypothesis`).**
Runs the full MDT consilium (`evaluate_hypothesis_via_consilium`: 2 rounds ×13
agents + coordinator + arbiter) against lab history → verdict:
- **partial** → current state into `history[]`, apply `revised_hypothesis`, `version+1`,
  `status="open"` → the hypothesis proceeds **to the next round**;
- **confirmed** → a protocol (`generate_protocol_from_hypothesis`), **but only
  if a lab test was taken after the hypothesis's `created_date`** (fresh-evidence guard,
  G3). No out-of-sample test → protocol withheld (`protocol_withheld`),
  the hypothesis stays `open` and awaits the next round; a task from `test` is still
  created (it prompts the required lab test);
- **rejected** → closed (+ sometimes a refined successor).

**How the `eval` result reaches the person (2026-08-29).** The consilium takes 2–3 minutes,
and the dashboard handler **does not wait**: it sets a lock in the payload (`status="testing"`,
`eval_started_at`, TTL `dashboard_filters.EVAL_TTL_MIN`) and launches **a separate process**
`hypothesis_resolution.py --eval <id>` (`start_new_session`, log `logs/consilium_eval.log`).
Not a task in the event loop — that was killed by the post-commit hook restarting the dashboard on every commit
(08-30: two consiliums died in Round B). The card returns immediately and polls `GET /api/hypotheses/{id}/card`
every 5 seconds while the lock is alive; a second click with a live lock does not launch another consilium.
The conclusion is written to `hypothesis_outcomes` with `sent_at=NULL`, and the bot's outbox
delivers it to Telegram (`jobs.scheduled.deliver_unsent_outcomes`, at startup and every
5 minutes) — not the HTTP response. The day's lesson: nine consiliums ran into the void because
both channels (the response to a 170-second request and an outbox that ran only at bot startup)
depended on who was still alive when the result became ready. Monday's
`check_hypothesis_evaluations` picks up `testing` without an outcome **for the current round** (an outcome
older than `eval_started_at` does not count) — owner's decision, 08-30.

---

## Convergence model and fresh-evidence guard

A hypothesis is **not regenerated** every month — the same `memory` row
survives rounds, accumulating `version`/`history`. Convergence is emergent:
several `eval` rounds (after each arrival of lab results) eliminate alternatives
until one remains. So a young hypothesis in `partial`/`open` is normal, not
stagnation; the anomaly is "stuck in partial after ≥N rounds" (a sensor for this class is
an open task, replacing the removed "hypotheses without experiments" check).

**What gates escalation is fresh data, not a round counter (G3, 2026-07-13).**
Previously, `confirmed` from one `eval` immediately created a protocol — even if the verdict
rested on existing lab history (in-sample fitting to data available when the hypothesis
was born). Now `resolve_hypothesis` allows a protocol only
with **out-of-sample** confirmation: there must be a lab test dated after
`created_date` (`_has_out_of_sample_lab`; reads the same `get_lab_history` as
`eval`). Invariant: block only when the absence of fresh data is proven;
any ambiguity (missing `created_date`, failed lab read) is interpreted in favor of
the panel's verdict (fail-open). The guard is **temporal-only**: it checks that the lab test was taken
later, not that it relates to the prediction — relevance is left to the consilium
(deliberately).

**Operator in the loop — deliberately.** A person launches `eval` (a button), not cron:
the owner monitors each hypothesis's status so it does not change on its own.

**Known gap (BL, "physician" step):** `_build_eval_data_package` feeds the consilium
only the hypothesis + lab history (2 years). A physician's opinion does not flow in
automatically — it appears only if manually written into the hypothesis formulation.

---

## Tombstone: "experiments" (retired 2026-07-10, BL-EXP-1)

There used to be an **N-of-1 experiment** pipeline: hypothesis → intervention →
baseline/target → check_days → attribution report → protocol. The owner rejected
it: experiments came "out of thin air," not from data. The methodology moved to
consilium hypotheses born from data, and convergence moved to versioning the hypothesis itself
(above), not experiments.

Retired in stages (snapshot `health.presnap_expretire_*`):
- Inflow stopped (`hypothesis_lab_linker` → no-op; experiment-check removed from the
  scheduled job; the integration check "hypotheses without experiments" removed);
- `resolve_hypothesis._close_experiment` → no-op (decoupled from `complete_experiment`);
- `experiments_db` — all functions are no-ops with their previous signatures (health_db
  re-export + `check_contracts` intact; consumers return empty results themselves);
- Dashboard endpoint `/experiments/{id}/{action}` removed;
- Tables `experiments`/`experiment_log` **removed** from the canonical store (`db_regression`
  excludes them).

**Remainder (intentional):** functions `experiments_db` and `gp_agent.run_experiment_checks`/
`generate_attribution_report` remain as documented no-op stubs — complete
removal affects the pinned `check_contracts` contract + ~6 consumers + their tests
(high risk for little value). The `protocols.linked_experiment_id` column
remains (nullable, an orphan is safe; protocols are NOT retired — they are valid
output from confirmed-through-eval).
