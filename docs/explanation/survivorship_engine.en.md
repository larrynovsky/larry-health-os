<!-- translation-of: docs/explanation/survivorship_engine.md sha256:6969bec5c1fb -->
**English** · [Русский](survivorship_engine.md)

# Survivorship extension — architecture

> **Type:** Explanation (Diataxis).
> **Created:** 2026-05-18 (alongside phase 9 of plan v4).
> **Context:** explains “how it works and why” for the survivorship extension. The work plan and delta log are in `survivorship_extension_plan.md`.

## Why

An extension of the existing Health OS architecture that:

1. **Monitors recent literature** on recovery after cancer, late effects of treatment, and anatomy after surgery.
2. **Regularly collects subjective symptoms** through validated questionnaires (ISI, MFSI-SF, a site-specific EORTC QLQ module) on a 90-day cadence.
3. **Combines**, every two weeks, objective metrics + PRO + literature + context into structured findings.
4. **Escalates** each finding to one of five destinations: hypothesis, problem review, task, note, or constitution conflict.
5. **Sends reminders itself** if any proactive runs are overdue.

User commands are not a required channel.

## Included modules

```
~/health_scripts/
  pubmed_searcher.py            # one job: PubMed sweep over 19 topics, no LLM
  publication_reader.py         # one job: cheap-triage Haiku + analyze Haiku
  literature_curator.py         # one job: 4-way escalation finding → memory|tasks|proposals
  hypothesis_semantic_check.py  # one job: Haiku dedup before save_hypothesis
  survivorship_analyzer.py      # one job: shadow-diff + metric drift + context
  survivorship_curator.py       # one job: 5-way escalation (incl. constitution_conflict)

  migrations/2026_05_18_schema_for_survivorship.py  # ALTER alerts.source + CREATE assessment_sessions
  migrations/2026_05_18_survivorship_alerts.py      # 5 rules in alerts

  data/instruments/<module>.json
  data/instruments/isi.json
  data/instruments/mfsi_sf.json
  data/survivorship_config.yaml
  data/survivorship_topics.yaml
```

Extensions to existing modules:
- `health_db.py` — `get_recent_labs(exclude_pro=True)`, `save_alert`/`get_active_alerts`, `save/get/update_assessment_session`; `get_active_constraints` now reads alerts.
- `hai_hypotheses.py` — three new triggers in the source mapping.
- `integrity_tests.py` — 6 new watchdog checks (assessments, literature, analyzer, proposals, hypotheses, constitution_conflicts).

## What remains (phase 4)

`assessment_scheduler.py` + `assessment_dialog.py` + `assessment_importer.py` + three new callback handlers in `telegram_bot.py`. This is a **UX blocker** — it requires a decision between “Mini App or inline chat dialogue.”

## Semantic data channels

### Survivorship rules — `alerts`

5 rules live in `alerts` with the marker `source='survivorship_literature'` or `source='genetic_constraint'`. The `source` column was added by ALTER in phase 1.8. The marker is mandatory — it separates survivorship rules from patient allergies.

`get_active_constraints()` now reads `alerts` (the legacy signature is preserved). This closed the longstanding `no such table: patient_constraints` bug.

### PRO data — `lab_results` with `source='instrument:<id>'`

PRO subscales are written as ordinary `lab_results` (test_name = `isi_q2`, `mfsi_sf_general`, etc.), but with the marker `source='instrument:isi'`. This allows filtering.

`get_recent_labs(exclude_pro=True)` **excludes** PRO by default — so the existing `safety_net` does not interpret a questionnaire score as a biochemical measurement.

PRO are **integrated into the `events` graph** (SX-15, 2026-05-18). `assessment_importer.import_file()`, after creating records in `lab_results`, calls `db.save_event(event_type='self_observation', performer='self', performer_role='self', recorded_by='patient', diagnostic={type: 'functional_test', modality: <instrument_id>, raw_values_ref: [lab_results.id ...], interpreted_report: <строка subscale-скоров>})`. This lets long-term agents traverse PRO as full FHIR-like events — not just numerical series in `lab_results`.

### Hypotheses — `memory` with a trigger in the JSON payload

All three hypothesis sources (`monthly_consilium`, `literature_curator`, `survivorship_curator`) write through `save_hypothesis(...)` to `memory(category='hypothesis')`. The trigger distinguishes the source.

Before `save_hypothesis`, a call to `hypothesis_semantic_check.check(observation)` through Haiku is mandatory. If there is a semantic duplicate, the write is skipped and an audit record goes to `memory(category='dedup_skipped', active=0)`.

For a rich payload (problem_representation, illness_script, evidence_for/against, structural_confidence, patient_view), use the `hypotheses_cbcr` table through `save_cbcr_payload(memory_id, ...)`.

### Constitution conflicts — `memory(category='constitution_conflict')`

A new category. It appears when `survivorship_curator` sees a discrepancy between PRO/metrics and the premise of a specific recommendation in `constitutions/*.md`. The value structure (JSON):

```json
{
  "constitution_file": "constitutions/sleep.md",
  "finding": {...},
  "curator_summary": "...",
  "curator_rationale": "...",
  "mechanism_type": "immediate|cumulative|mixed|unknown",
  "status": "open|resolved_keep|resolved_retract|resolved_restructure"
}
```

On creation, it enters the morning GP context automatically through `get_memory()` without a category filter.

### Questionnaire sessions — `assessment_sessions`

A new table (the extension's only new one). Contract: `id, instrument_id, wording_version_hash, chat_id, task_id, started_at, completed_at, answers_json, status`. Progressive save (UPDATE answers_json) on every answer. Interrupt/resume by `(chat_id, instrument_id, status='in_progress')`.

Separate from `consultation_sessions` (reserved for `/consult` dialogues). Routing for a third mode will need to be added in `telegram_bot.handle_text` (phase 4).

## Lifecycle of one literature finding

```
voskresenie 04:00
  pubmed_searcher.run()
    → 19 topics × up to 5 candidates = up to 95 PMIDs
    → dedup by PMID (60-day window of past searches)
    → agent_reports(agent_type='literature_search', findings=[{pmid, title, abstract,...}])

ezhednevno 04:30
  publication_reader.run(max=10)
    → takes unprocessed candidates from the latest literature_search
    → cheap-triage Haiku: "relevant to post-oncology care?"
        → no → record with status='dismissed_at_triage'
        → yes → analyze Haiku: structured finding {claim, population, evidence_level,
                                                   applicability_to_me, relevance_assessment}
    → agent_reports(agent_type='publication_reading', findings=[{kept|dismissed}])

  literature_curator.run(max=10)
    → takes unprocessed kept findings from publication_reading
    → for each: decision Haiku → action ∈ {hypothesis, problem_proposal, task, note}
    → hypothesis: semantic_check → CBCR (Sonnet 152s) → save_hypothesis(trigger='literature')
    → problem_proposal: save_problem_proposal(source='literature_curator')
    → task: save_task(source='literature_curator', fingerprint='literature:PMID:...')
    → note: save_memory(category='literature_note')
    → agent_reports(agent_type='literature_curator', findings=[audit])
```

The next morning, the GP agent sees through `_build_gp_context`:
- Recent literature findings (past 7 days).
- Active hypotheses (including those from literature).
- Active proposals in `problem_list_proposals`.
- Active `survivorship_literature` rules in `alerts`.

## Lifecycle of survivorship analysis

```
Monday 03:30 (every two weeks)
  survivorship_analyzer.run()
    → for each instrument: latest PRO per subscale + shadow-diff vs proxy metrics
    → global: metric trends (HRV, sleep_deep, readiness, RHR — 7d vs 30d)
    → global: fresh literature from the last 14d (high relevance)
    → global: active survivorship alerts (context)
    → agent_reports(agent_type='survivorship_analysis', findings={per_instrument, global})

ponedelnik 04:00
  survivorship_curator.run(max=10)
    → takes actionable findings from the latest survivorship_analysis
    → skips recent_literature_relevant, active_survivorship_rules, pro_missing_for_rule
    → for each: decision Haiku → action ∈ {hypothesis, problem_proposal, task, note,
                                              constitution_conflict}
    → constitution_conflict: save_memory(category='constitution_conflict', value=JSON)
    → the rest — as in literature_curator
    → agent_reports(agent_type='survivorship_curator', findings=[audit])
```

## Watchdog

`integrity_tests.py` runs 6 new checks daily at 07:50. All call `warn()` (not `fail()`); the result enters `morning_test_summary` as an INFO section:

```
SURVIVORSHIP-WATCHDOG
─────────────────────────
⚠️  assessments overdue: mfsi_sf: 120d since <date> (threshold 104d); isi: never filled in
⚠️  literature_search has not run for 12d (threshold 10)
```

This is what “if something does not run, the system comes to me with a list” means.

If nobody calls `build_assessment_task_keyboard`, a reminder gives the person
no completion path, while the task retains `sent_at=NULL`. An overdue assessment stays silent
while its open questionnaire task has been DELIVERED
to the bot and its deadline has not passed (the person receives the question through one channel, §13), while an undelivered
task older than 2 days produces a separate WARN, “outbox is not being read” (§14, liveness). The completion path
and dashboard lock are in `docs/explanation/task_reminders_flow.md`.

## Curator limits

Initially, `survivorship_max_hypotheses_per_run: null` and `literature_max_hypotheses_per_run: null` in `survivorship_config.yaml`. No hard limits. Calibration follows phase 10 (test charters).

If charter 1 (a month of normal use) shows that hypothesis volume is noisy, add limits as numbers in the same file without changing code.

## Semantic deduplication

`hypothesis_semantic_check.check(observation)`:
1. Takes open + (optionally) rejected hypotheses over `window_days` (default 90).
2. Haiku call: “is this semantically equivalent to any of them?” Returns `(False, None, '')` or `(True, existing_id, reason)`.
3. If True, skip `save_hypothesis` and write an audit record to `memory(category='dedup_skipped', active=0)`.

This step is mandatory for **all** three hypothesis sources to avoid duplicates across `monthly_consilium`, `literature_curator`, and `survivorship_curator`. A structural fingerprint (the first 50 characters of observation) is insufficient: different wordings of the same mechanism will get through.

## What we reuse unchanged

- `cbcr_hypothesis.generate_hypothesis_with_critique()` + `flatten_cbcr_payload()` — the hypothesis engine.
- `_notify_patient_view()` — Telegram format “noticed / possible causes / what to do / when to see a doctor.”
- `save_problem_proposal()`, `save_task()`, `save_memory()` — escalation channels.
- `pubmed_client.search_pubmed()` — NCBI E-utilities client.
- fswatch + tesseract OCR pipeline (for future phase 4 — PRO JSON import).
- `gp_agent.generate_daily_report()` — extended with context blocks, not rewritten.
- `monthly_consilium` — remains unchanged; the correct trigger was added to the mapping.

## Technical debt

- **SX-15** (closed 2026-05-18): PRO integration into the `events` layer. `assessment_importer` now, after `lab_results`, creates `events(type='self_observation')` + `diagnostic_events(type='functional_test')` with `raw_values_ref` pointing to the created lab_results. Test: `tests/unit/test_assessment_importer.py::test_importer_creates_self_observation_event_with_diagnostic`.
- **SX-16**: migration of oncology visits from `consultations` to `encounters`. The team has already done part of this — survivorship-curator reads both.
- **`monthly_api_report.py`** monitors only test API calls. The cost of the new modules is not tracked anywhere. Until a separate phase, monitor through Anthropic Console (charter 3 in the plan).

## Where to find details

These three documents were written using the owner's data and are in the private part of the project:
- Full plan: `docs/explanation/survivorship_extension_plan.md` (in the private part of the project).
- Pre-flight findings: `docs/explanation/survivorship_preflight.md` (in the private part of the project).
- Canonical database schema snapshot as of 2026-05-18: `docs/explanation/studio_db_schema_2026-05.md` (in the private part of the project).
- Extension configuration: `~/health/data/survivorship_config.yaml`.
- PubMed topics: `~/health/data/survivorship_topics.yaml`.
- Instrument catalog: `~/health/data/instruments/*.json`.

## Test coverage (SX-17)

All 10 new modules + 6 watchdog functions in `integrity_tests.py` are covered by unit tests:

```
tests/unit/test_health_db_alerts_contract.py     ─ signature + round-trip + source filter (3)
tests/unit/test_hypothesis_semantic_check.py     ─ empty DB + Haiku duplicate (2)
tests/unit/test_assessment_scheduler.py          ─ task creation + idempotency (2)
tests/unit/test_pubmed_searcher.py               ─ save_agent_report + dedup by PMID (2)
tests/unit/test_assessment_dialog.py             ─ full happy-path + resume by chat_id (2)
tests/unit/test_assessment_importer.py           ─ parsing + warning on wording_hash mismatch (2)
tests/unit/test_survivorship_analyzer.py         ─ no_pro_yet finding + metric_drift (2)
tests/unit/test_integrity_tests_survivorship.py  ─ 3 of 6 watchdogs (the rest are similar) (3)
tests/unit/test_publication_reader.py            ─ triage routing (dismiss + kept) (2)
tests/unit/test_literature_curator.py            ─ note + task action types (2)
tests/unit/test_survivorship_curator.py          ─ note + constitution_conflict (2)
```

**Total: 24 tests, 505 PASS in the full unit suite (after fixing the UC-I-02 regression).**

**Test schema:** `tests/fixtures/health_schema.sql` was extended with the `alerts` and `assessment_sessions` tables (SX-17). The existing `db` fixture picks them up automatically.

**What is not covered:**
- Integration tests (the full searcher→reader→curator pipeline end to end) — future work `SX-19`.
- Snapshot tests for survivorship_analyzer — future work if needed.
- Extended edge cases (error paths, malformed inputs) — `SX-18` if needed.

**Security regression closed:**
- `cb_router_with_owner_check` in `telegram_bot.py` — a wrapper with an inline OWNER_CHAT_ID check (UC-I-02 fail-closed pattern, modeled on `callback_doc_review`).
- `abh.cb_router` has its own internal double protection through the OWNER_CHAT_ID import.
