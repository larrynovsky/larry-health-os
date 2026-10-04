<!-- translation-of: TESTING_CONTRACTS.md sha256:623dc50453b2 -->
**English** · [Русский](TESTING_CONTRACTS.md)
# TESTING_CONTRACTS.md — What the system must do correctly

⚠️ **Status (2026-07-02, three-lens review):** this file declares thresholds and
criteria; it is NOT the single source of truth: it lags behind the code (it does not cover
check_lab_canon_health, check_treatment_history_extracted, model_health_check,
oura_freshness_check or the 2026-06-30 lab pipeline). Current thresholds are in
`system_config` (DB) and the constants in `integrity_tests.py`. Plan: generated
sections following the gen_blueprint pattern (Workstream 5 of plan_fix_three_lenses_2026-07-02);
until then, trust the code/DB when they differ, and correct this file.

Last substantive update: 2026-04-24

---

## 1. DATA — Freshness and validity

### Daily sources (conit C1)
| Source           | Reminder    | Alarm    | Action on alarm                   |
|------------------|-------------|----------|-----------------------------------|
| oura             | —           | > 26h    | Telegram alert before sending the report |
| apple_health     | —           | > 26h    | Same                              |
| withings (BP)    | —           | > 6h     | Alert if a measurement was taken  |
| oncology_pdf     | —           | > 12h    | Alert + flag in the conclusions   |

### Periodic sources
| Source                | Reminder    | Alarm       | Note                                    |
|-----------------------|-------------|-------------|-----------------------------------------|
| lab_results           | 6 months    | 9 months    | By the date of the latest lab test in the DB |
| genome_update_agent   | —           | > 30 days   | By the date of the last run             |
| GP weekly report      | —           | > 8 days    | By the date of the latest agent_report  |
| GP monthly report     | —           | > 35 days   | By the date of the latest monthly report |

### Clinical periods (conit C3)
- Source: **only a document from the doctor** after a consultation
- Validity: until the `next_appointment` date
- Alarm: if `next_appointment` has passed and `periods` have not been updated
- Trips (calendar_sync): updated hourly; alarm if no sync for > 2h while a trip is active

### Schemas requiring implementation
- `problem_list`: needs a `review_date TEXT` field — required when creating a record
- `protocols`: needs a `review_date TEXT` field — required when creating a record
- Alarm: `review_date` has passed, status unchanged

---

## 2. CONCLUSIONS — Quality and verification

### Oracle rule for agent reports
| Report type                             | PubMed verification | Threshold |
|-----------------------------------------|--------------------|-------|
| `agent_type IN ('specialist', 'gp')`    | Required           | Every PMID exists in PubMed |
| `has_findings = 1`                      | Required           | Same |
| Morning report                          | Not required       | Excluded |
| Checkin                                 | Not required       | Excluded |

**What “verified” means:**
- The PMID is queried through the PubMed API (pubmed_client.py)
- The article exists and returns data
- The article is peer-reviewed
- A report with no PMID when `has_findings=1` is a failure

### Physiological ranges (internal consistency)
| Metric       | Allowed range       | Action outside the range |
|--------------|---------------------|---------------------|
| hrv          | 5–120 ms            | Warning             |
| resting_hr   | 30–120 bpm          | Warning             |
| bp_systolic  | 60–200 mmHg         | Warning             |
| bp_diastolic | 40–130 mmHg         | Warning             |
| sleep_total  | 0–14 h              | Warning             |
| spo2_avg     | 70–100 %            | Warning             |

---

## 3. HYPOTHESES — Generation and follow-through

A hypothesis with no progress = junk in the database.

| Condition                                            | Status   |
|------------------------------------------------------|----------|
| Hypothesis with no linked experiment                | Alarm    |
| Experiment with no `experiment_log` in the last 14 days | Warning |
| Experiment with `status='active'` older than 60 days and no logs | Alarm |

---

## 4. CONTEXT — Completeness of agent inputs

An agent cannot draw correct conclusions if its context is incomplete.

| Agent             | Required contents                                     |
|-------------------|-------------------------------------------------------|
| checkin_agent     | active periods, upcoming periods (14 days)            |
| GP weekly report  | latest labs, problem_list, active protocols, genome    |
| morning_report    | metrics for 7 days, BP if available                   |
| lifestyle agents  | domain-specific genome block, percentile metrics      |

Test: parse the context string (or mock call) and look for the required sections.

**Multidisciplinary case review (lab section) — an EXCLUSION contract, not an inclusion contract.** `build_specialized_context`
does NOT serve rows from another domain under a clinical heading (home=canonical store, awaiting
name normalization to the canonical form): otherwise blood biochemistry reaches the case review labelled “outside blood biochemistry”
(the reverse side of §16; a live defect on 2026-08-01, caught by visual inspection). The oracle is
`tests/unit/test_build_specialized_context.py::test_full_output_golden_master`
(a golden master of the entire output + an executed negative control: removing the `cbc` verdict
breaks the pinned-output assertion, and Ferritin leaks through). The boundary is explicit: a detector of output DRIFT, not clinical
correctness (§20, RST check≠test). The logic lives in the test; this is only a pointer.

---

> **A sensor fired, but you never received the alert** — troubleshooting guide: [docs/how-to/diagnose_silent_check.md](docs/how-to/diagnose_silent_check.md).
> A passing delivery unit test checks that the code CALLS the send operation, not that Telegram delivered it.

<!-- BEGIN AUTOGEN: integrity-sensors (gen_testing_contracts.py) -->
<!-- Generated by gen_testing_contracts.py; descriptions are machine-translated from the Russian source. -->

### Integrity sensor registry (185 sensors, generated)

> Source: `integrity_tests.py`. Built from the AST — edit the code, not this table. launchd 07:50 → triage 08:00.

| Sensor (label) | Function | What it checks |
|---|---|---|
| latest HRV data not older than 48h | `check_data_freshness` | — |
| <dynamic> | `check_data_window` | — |
| steps from flat daily_metrics are accessible via get_day() | `check_steps_visible_via_get_day` | Steps consistency: if steps exist in flat daily_metrics.steps — |
| key metrics (HRV + sleep) are present | `check_core_metrics` | — |
| weekly statistics are not empty | `check_stats_non_empty` | — |
| GP report exists and is not older than 8 days | `check_gp_reports` | — |
| GP report content: key sections are present | `check_report_content` | — |
| <dynamic> | `check_problem_list` | — |
| open issues are described in plain language | `check_problem_plain_summary` | An open issue carries a plain-language description (27.09, wave 4). Medical text — |
| protocols are readable | `check_protocols` | — |
| tasks are readable (open + overdue) | `check_tasks_pipeline` | — |
| task activity over 14 days | `check_recent_task_activity` | — |
| question→answer channel is consistent | `check_question_answer_integrity` | A question closed without an answer; an undelivered question; an answer with no trace in memory. |
| question queue flushes within its deadline | `check_question_queue_drains` | The question queue MUST FLUSH within the deadline it sets for itself. |
| suggestions on the problem list are delivered | `check_proposals_delivered` | A GP suggestion on the problem list is awaiting a human decision but never reached them. |
| question candidates are not silently discarded | `check_question_candidates_not_discarded` | A candidate fell outside the window without EVER reaching the judge. |
| model response is parsed | `check_llm_answer_parsed` | A model response that could not be parsed AT ALL must be visible. |
| delivered texts are judged against the canon | `check_reco_repeats_fresh_lab` | A text advises running a test that has already been run within the monitoring window. |
| absence surface is either guarded or named | `check_absence_surface_guarded` | A tract with a SURFACE for an absence claim is either guarded or named. |
| patient responses reach the GP context | `check_question_answers_reach_doctor` | Receipt at the consumption point: a fresh response IS VISIBLE in the assembled GP context. |
| monitored issues reach the weekly review | `check_watched_problems_reach_review` | Every issue with monitoring status is visible in the GP weekly review context. |
| build_context(yesterday) contains data | `check_build_context` | — |
| canon lab_results: single writer (no biochemical-json) | `check_lab_single_writer` | Single-writer invariant of the canon (BL-LAB-CANON-1, Primary-Based |
| PGS reference DB is in place and populated | `check_pgs_reference` | Reference DB of polygenic weights (pgs_catalog+pgs_weights) is extracted from the canon |
| genome_update_agent freshness | `check_genome_update` | genome_update_agent is not older than GENOME_UPDATE_ALERT_DAYS. |
| daemon liveness (KeepAlive) | `check_daemons_alive` | KeepAlive daemons must have a live PID. Covers the class 'service died silently'. |
| stderr sensor blindness | `check_stderr_watch_blindness` | Whether the stderr sensor itself is blind. MUST be placed BEFORE check_stderr_errors. |
| new errors in stderr of jobs | `check_stderr_errors` | Errors APPENDED to stderr logs of jobs since the last check. |
| restart completeness after deploy (§12) | `check_deploy_restart_completeness` | Ratchet of restart completeness: set of long-lived jobs == set that |
| tenant counter under the silence decision (§18) | `check_tenant_count_vs_silence_decision` | Counter under the decision 'human silence is not treated as a signal' (§18). |
| env consistency of launchd plists (multitenant) | `check_plist_env_consistency` | WARN: owner job has HEALTH_DATA_DIR in plist but WITHOUT it in the loaded |
| launchd: job inventory vs repository | `check_launchd_inventory` | WARN: plist copy in repository has diverged from live, or jobs without a copy have increased. |
| memory arbiter liveness | `check_arbiter_liveness` | WARN: memory extractor (arbiter) has fallen behind FRESH messages. Class 'silent |
| consolidation/disagree delivery liveness | `check_consolidation_delivery_liveness` | WARN: delivering job disagree/supersede (run_nightly_consolidation, daily) may have silently |
| question elevator liveness | `check_promote_questions_liveness` | WARN: the candidate-to-question elevator may have silently died. |
| uncommitted_watchdog liveness (§14) | `check_watchdog_liveness` | WARN: §14 terminus — uncommitted_watchdog itself may have silently died (launchd |
| uncommitted work on MacBook (from snapshot) | `check_macbook_uncommitted` | UNCOMMITTED WORK ON MACBOOK — judged from here, from snapshot, not by polling the laptop. |
| MacBook code has reached Studio (from snapshot) | `check_macbook_head_deployed` | CODE FROM MacBook HAS NOT REACHED STUDIO — and nobody reported it (27.09, owner's decision). |
| thread work copy count (not single instance) | `check_thread_work_has_a_second_copy` | THREAD WORK IN A SINGLE INSTANCE — question for the AFFECTED party, not the builder. |
| thread without a registry row (orphan) | `check_threads_have_index_row` | ORPHAN THREAD: branch is alive but absent from the thread registry — no session can see it. |
| capturing another's file into a commit (from snapshot feed) | `check_captured_files` | CAPTURING ANOTHER'S FILE INTO A COMMIT — question to the person, not a machine verdict. |
| hooks are executable on disk (§14) | `check_git_hooks_executable` | An active hook without the execute bit = ALL gates disabled at once, silently. |
| idempotency gate liveness (§14/§15) | `check_dispgate_liveness` | §14 for the idempotency gate (§15): it may have stopped protecting in two ways — |
| intent-receipt gate liveness (§14, intent-receipts) | `check_intentgate_liveness` | §14 for the intent-receipt gate (thread intent-receipts): two ways to stop protecting — |
| recurrence of 'not idempotent' reasons (§15/§13) | `check_disposability_causes` | §15/§13: the same negative-verdict reason surfacing across N DIFFERENT calendar |
| episodic memory growth (F4.3 tripwire) | `check_memory_facts_growth` | WARN (F4.3 tripwire, plan "PHASE 4 REBUILD"): active EPISODIC |
| temporal_class coverage (F1) | `check_temporal_class_coverage` | WARN: active state with temporal_class IS NULL — write path bypassed derivation |
| frozen-relative → transient (F1/F2) | `check_frozen_relative_not_current` | WARN: active state with «today/yesterday» in TEXT but NOT classified as transient → |
| regression of row count vs backup | `check_db_row_regression` | Table dropped >50% against the peak of recent backups → silent data erasure. |
| visual-intake orphans (photo/case/hypothesis) | `check_visual_orphans` | visual-intake integrity: photo without case / case without photo / handed_off without |
| visual-intake: parse lifetime is working | `check_stale_visual_cases` | Watchdog of the parse lifetime MECHANISM (thread symptom-ttl, 03.10), not of a human. |
| symptom_intake verdict-rate (lagging) | `check_visual_verdict_rate` | LAGGING elicitation quality signal: share of symptom_intake hypotheses, |
| symptom-intake prompt discipline (pose+§9) | `check_symptom_prompt_discipline` | Safe-pose guard: elicitation prompt has not lost the reassurance prohibition; |
| shrinkage of lab history vs backup (FAIL) | `check_lab_history_regression` | FAIL (health-critical): lab_results shrank sharply vs last backup — class |
| CBCR methodology in git (presence+non-emptiness) | `check_cbcr_methodology_present` | CBCR methodology load-bearing (manifest = system prompt for hypothesis generation, |
| Validation gate methodology in git (presence+non-emptiness) | `check_validation_gate_methodology_present` | Validation gate methodology load-bearing (spec = subsystem intent, |
| canon: «norm» does not contradict the form reference | `check_canon_normal_flag_matches_reference` | «Norm» does not contradict the printed reference. Both tenants (wave 4, 27.09). |
| SQLite integrity_check ok per-tenant (no B-tree corruption) | `check_db_integrity` | PRAGMA integrity_check per-tenant — B-tree corruption and out-of-order rowids. |
| tenant DBs reachable (anti-silent-truncation for per-tenant checks) | `check_tenant_dbs_reachable` | Meta-sensor against SILENT TRUNCATION (RST, partner-integrity 2026-07-17). Trap: |
| partner readiness for per-tenant stratification (Group 3 trigger) | `check_partner_epochs_ready` | SIBLING-tenant readiness for per-tenant A/D stratification. |
| validation gate family names resolve to live data | `check_family_names_resolve` | Every declared validation gate family name must resolve to LIVE data. |
| lab thresholds reach data (canon name + row in lab_results) | `check_threshold_names_reach_data` | Every lab threshold must MEET data: name = canon lab_canon.normalize, |
| norms not expired (next_review) | `check_norm_review_overdue` | Active norm with a past next_review — red for the owner. This is literally |
| norm reference agrees with the lab interval (external witness) | `check_norm_vs_lab_reference` | External witness of the norm. Red — only for norm_kind='reference_interval': |
| norm coverage of measured analytes | `check_norm_coverage` | Norm coverage: analyte with ≥norm.coverage_min_n measurements and no norm in any house — |
| norm kind declared (norm_kind ≠ unclassified) | `check_norm_kind_unclassified` | Remainder without a norm_kind verdict — WARN counter for the owner. Not red: verdict |
| norm documents are fresh (sha256 / updated_at by next_check) | `check_norm_documents_fresh` | A norm is a replica of an external document; the sole axis of consistency is staleness, |
| observation schedule within the guideline window per episode | `check_schedule_vs_guideline` | Physician assignment (lab_monitoring_schedule, source encounter:*) — primary; guideline for |
| safety_net thresholds are derived from the document (not typed in) | `check_threshold_source_is_document` | Invariant threshold_derived_from_document (registry norm_from_documents): typed |
| liveness of brief reschedule (local tz) | `check_reschedule_liveness` | WARN: 12h-job for recalculating the brief's local timezone (reschedule_local) is silent for the tenant. |
| device metrics: each one has an owner (HAE) | `check_hae_arrivals_have_owner` | WARN: the metric that the device actually sends has no owner (owner decision 26.09). |
| HAE raw archive is compressed (rotator, older than 15 days) | `check_hae_raw_archive_compressed` | FAIL: HAE raw archive contains uncompressed exports older than 15 days (owner decision 26.09). |
| weekly_digest: delivered to all tenants (Monday) | `check_weekly_digest_delivered` | weekly-digest thread (2026-09-05). Monday: the digest file for the PREVIOUS week exists and |
| clinical_kb is populated (sex/food frame are non-degenerate, F2) | `check_clinical_kb_populated` | FAIL: clinical_kb IS EMPTY → medical_frame degenerates into a standard frame AND assert_floor is silent → |
| effect_allele coverage (strand) | `check_effect_allele_coverage` | strand-resolution coverage of effect_allele (genome strand-fix F7). |
| clinical_kb replica is fresh (source==table, F2) | `check_clinical_kb_replica_fresh` | FAIL: clinical_kb in the tenant DB HAS DIVERGED from the git source (yaml changed, init_db not |
| freshness of lab_results | `check_lab_freshness` | Results are no older than LAB_ALERT_DAYS (9 months = alert, 6 months = warning). |
| lab_results canon: no impossible values or conflicts | `check_lab_canon_health` | Canon lab_results: no physiologically impossible values. |
| promote is not stalled (staging queue is moving) | `check_promotion_backlog_stale` | The recognition pipeline must not silently block at the OUTPUT. |
| LOINC reference: single home, canon does not intercept it | `check_reference_tables_not_in_canon` | The LOINC reference has ONE home — the shared `loinc.db`. A copy in the canon is a trap. |
| different analytes are not merged under one name (reference ranges do not diverge) | `check_lab_names_not_glued` | Different analytes sharing the same name silently corrupt the trend. |
| lab_results canon: every row has a result | `check_canon_rows_have_a_result` | The canon contains no rows without a result — neither numeric nor text. Both tenants. |
| lab_results canon: one analyte in a document — one date | `check_canon_one_date_per_analyte_in_doc` | Within ONE document, one analyte in one material cannot have two dates. |
| lab_results canon: a new value has a unit | `check_lab_unit_present` | A number without a scale is unsuitable for clinical inference — yet the canon accepts it. |
| lab_results canon: reference is in the same scale as the value | `check_lab_ref_scale` | The reference must be in the same scale as the value. |
| staging: sample material and its stage have not diverged | `check_staging_specimen_provenance` | A material WITHOUT its own stage is indistinguishable from an assigned one — and it is exactly this |
| value and comparison operator have not diverged | `check_censored_values_coherent` | A comparison operator and a number exist ONLY together. |
| specialized_lab_results: integrity (panel_type + value) | `check_specialized_lab_health` | specialized_lab_results (BL-LAB-CANON-2): integrity of specialized panels. Each |
| spec-layer drains: a resolved name does not get stuck outside the canon | `check_specialized_canon_waiting` | The spec-layer is the canon's waiting room: a row with verdict home=canon lives here, |
| canon's nominal debt does not grow | `check_canon_naming_debt` | Canon's nominal debt does not grow: a row from a document lives under its own name. |
| history does not obscure the present | `check_unrepeated_draw_not_swamping` | The historical block does not outgrow the current laboratory picture. |
| literature partner tap: suppressor | `check_literature_partner_gate` | The deferred owner decision is returned on EVENT, not on schedule. |
| special panels: electrophoresis converges with itself | `check_electrophoresis_sums` | Electrophoresis converges with itself (see `electrophoresis_offenders`). |
| promote: refusal to merge two values is visible to the user | `check_promote_conflicts` | Measurements that promote REFUSES to place in canon must be visible. |
| LOINC decisions do not contradict filtering by facts about the person | `check_loinc_decisions_possible` | A recorded decision must not reference a code impossible for this person. |
| canon lab_results: no billing garbage (currency/procedures) | `check_lab_no_billing_rows` | Canon lab_results does NOT contain billing garbage from invoices (#67, 2026-07-02). |
| carrier-status⟹effect_allele coupling (null≠clean) | `check_carrier_status_allele_coupling` | Coupling carrier-status ⟹ effect_allele NOT NULL (invariant |
| memory_facts invariants (E: no-double-active-key + R11) | `check_memory_facts_invariants` | FAIL: memory_facts invariants (E, debt from 5 July audit). |
| constitutions are not hollow (effect_allele populated before generation) | `check_constitutions_not_hollow` | FAIL if constitutions are generated on EMPTY effect_allele (hollow). |
| pulse of constitution rebuild on new inputs | `check_constitutions_trigger_alive` | Pulse of constitution rebuild on new inputs (owner decision 2026-09-25: «further |
| the fuse has something to judge by | `check_safety_net_can_judge` | "Nothing to judge by" at the fuse — system debt to engineering queue. |
| tenant genotype profile in public zone | `check_public_genotype_profile` | Tenant genotype profile in public zone (2026-09-25, thread genotype-scrub). |
| single canonical health.db (R1/R2 split-brain) | `check_single_canonical_db` | R1/R2 (split-brain prevention, 2026-06-18): exactly one health.db, not in sync folder. |
| no cross-tenant Oura contamination (fail-closed belt-and-suspenders) | `check_cross_tenant_contamination` | Belt-and-suspenders for fail-closed secrets_dir() (2026-07-03). |
| document parsing: one file — one event | `check_one_document_one_event` | Document parsing produces exactly one event per file (thread intake-tails, 24.09). |
| longitudinal_analysis freshness | `check_longitudinal_freshness` | longitudinal_analysis runs on schedule `com.larry.health.longitudinal`. |
| longitudinal gate applied | `check_longitudinal_gate_applied` | A fresh longitudinal summary must have the statistical gate applied. |
| gate run receipt | `check_gate_run_receipt` | Gate failure is an event with a reader, not "the row is simply absent". |
| belief is fresher than data edits | `check_belief_fresh_vs_data` | Belief is a CACHE computed from `daily_metrics`. The cache had no invalidation. |
| sleep stage provenance (self-healing) | `check_sleep_stage_provenance` | Fake sleep stages must not exist — and if they appear, they are fixed BY THEMSELVES. |
| device mixing in belief (ratchet) | `check_sleep_device_mixing` | Sleep from one device, HRV from another — in one belief row. Ratchet, not threshold. |
| quarantine without verdict | `check_quarantine_stuck` | Quarantine without verdict longer than QUARANTINE_STUCK_DAYS is a blockage, not order. |
| mc_gap: discoveries at Monte Carlo resolution (Phase 1/3-a trigger) | `check_mc_gap` | MC gap: every passing BY-verdict fell within ±2·MCSE of its selection line → decision on |
| pass-set flickering (Phase 4 online-controller trigger) | `check_passset_flicker` | pass-set flicker: membership of passing pairs changed between two runs → pair first time |
| pass-set history depth (silent loss of comparison baseline) | `check_passset_history_depth` | Snapshot history is shorter than the calendar → it was trimmed or the file was recreated. |
| gate artifacts after applied-run | `check_gate_artifacts_liveness` | After a SUCCESSFUL gate run, mandatory artifacts must exist and be non- |
| whether the warn delivery rail is alive (integrity→triage→Telegram) | `check_triage_delivery_liveness` | WHETHER THE WARN DELIVERY RAIL ITSELF IS ALIVE (integrity → triage → Telegram). |
| tenant-human morning review is alive | `check_tenant_triage_alive` | TENANT-HUMAN morning review is alive (28.09, owner decision: 'partner gets own review'). |
| GP monthly schedule | `check_gp_schedule` | GP monthly ≤35d. |
| review_date of issues and protocols | `check_review_dates` | Issues and protocols with an expired review_date. |
| clinical periods currency | `check_periods_expiry` | Clinical periods: watchful_waiting phases with an expired date and no next phase. |
| therapy dates: periods ↔ problem_list | `check_treatment_dates_agree` | Therapy dates in `periods` (primary) vs `problem_list` (copies in fields and headings). |
| treatment extracted from documents | `check_treatment_history_extracted` | Treatment is derived from documents (medications), not a hand-written string. |
| no hardcoded diagnosis in prompt-builders (thread diagnosis-hardcode) | `check_no_hardcoded_diagnosis` | FAIL: a removed onco-literal has returned to a prompt-builder (thread diagnosis-hardcode). |
| document classifier: per-tenant seed is seeded (A3 liveness) | `check_doc_classifier_seeded` | WARN: tenant_doc_patterns.yaml exists but its patterns are NOT in doc_patterns — per-tenant |
| CPIC canon is seeded and consistent (A2-full: 3 projections, one seed_version) | `check_cpic_canon_consistent` | FAIL: CPIC canon (3 projections) is not seeded or is inconsistent in seed_version/references. |
| PubMed egress is neutral (thread diagnosis-hardcode B: no raw diagnosis in query) | `check_pubmed_egress_neutral` | FAIL: egress-guard lets raw diagnosis specifics through to PubMed, or onco-pattern |
| labs freshness per-tenant (thread diagnosis-hardcode B6: oncomarkers not in shared base) | `check_labs_freshness_per_tenant` | FAIL: oncomarkers are in the NEUTRAL lab-freshness base → imposed on all tenants, or |
| Oura columns are populated with fresh data (#169) | `check_oura_column_completeness` | W5K-#169 (2026-05-14): freshness of Oura columns in daily_metrics. |
| all doc-files are present, CHANGELOG and SECURITY.md are valid | `check_doc_structure` | Checks that key doc-files exist after Diataxis restructuring. |
| Documentation translations are not lagging behind the original | `check_translations_fresh` | The English translation has fallen behind the Russian original (28.09.2026, owner decision: |
| survivorship: assessments freshness | `check_assessments_freshness` | Each tool — last fill is no older than cadence_days + 14. |
| survivorship: PRO score in one dimension | `check_pro_score_unit_single` | A PRO score under one name is stored in EXACTLY ONE dimension (§16 identity clause: |
| literature: literature_search freshness | `check_literature_freshness` | The latest literature_search covers the latest scheduled run against the live plist |
| literature: partner literature_search | `check_literature_freshness_partner` | Partner literature search covers its latest scheduled run (owner decision 27.09). |
| survivorship: analyzer freshness | `check_survivorship_agent_freshness` | The last survivorship_analysis is no older than 18 days. |
| survivorship: pending proposals ageing | `check_pending_proposals_ageing` | problem_list_proposals with status='pending' older than 21 days. |
| ECG: non-sinus rhythm | `check_ecg_nonsinus` | ECG records with alarming rhythm (AFib / High HR) in the last 48 h → cardio alert. |
| survivorship: open hypotheses ageing | `check_open_hypotheses_ageing` | Hypotheses with status='open' older than 60 days without transition to testing. |
| hypothesis: consilium assessment | `check_unresolved_evaluations` | HV-6: hypotheses in testing, lab data present, outcome absent > 7 days. |
| recommendation engine is healthy | `check_recommendation_engine_health` | evaluate_domain_need must not silently fail on the error-path. |
| survivorship: constitution conflicts unresolved | `check_constitution_conflicts_unresolved` | memory(category='constitution_conflict') with status='open' older than 45 days. |
| pharmacogenomics synchronized with genome (WFR) | `check_pharmaco_genome_sync` | Pharmacogenomics is synchronized with the last genome import. |
| genome traits synchronized with genome (WFR, Phase F) | `check_traits_genome_sync` | Deterministic traits (Phase F) synchronized with the latest genome import. |
| wellness genomics synchronized with genome (WFR, Phase G) | `check_wellness_genome_sync` | Wellness genomics (Phase G) synchronized with the latest genome import. |
| PRS synchronized with genome (WFR, Phase H) | `check_prs_genome_sync` | PRS (Wave 4, Phase H) synchronized with the latest genome import. |
| Backup freshness (every scheduled run) | `check_backup_freshness` | The most recent daily backup of EVERY tenant covers the last scheduled backup run. |
| DB size < 500MB | `check_db_size` | — |
| Logs outside rotation config | `check_logs_all_listed` | Rotation blind spot: log is GROWING but is absent from the rotation config. |
| Bot and service failures per day | `check_fault_journal` | Owner decision 2026-09-28: failures go into the nightly cycle, not into Telegram. |
| Log rotation is alive and clean | `check_logrotate_liveness` | Rotator is alive AND completed cleanly (§14, producer_registry finding 01.09). |
| Secret class registry vs. directories | `check_secret_scope_matches_reality` | Declared secret class vs. what actually resides in the directories (02.09). |
| LLM pipelines without secret_guard do not multiply (§19) | `check_llm_tracts_guarded` | The number of LLM pipelines WITHOUT secret_guard does not grow (§19, owner decision 2026-08-03). |
| Anthropic constructors outside llm_client do not multiply | `check_llm_direct_constructors` | Anthropic client is constructed ONLY in llm_client — the secret guard is in place there by design. |
| LLM guard blocks are visible in the report | `check_llm_guard_blocks_reported` | Guard blocks are not silent: the daily counter is visible to a human. |
| Correlations in reports are justified (UC-B-09) | `check_correlations_grounded` | UC-B-09: every `r=0.X` in recent agent reports is confirmed by an ACCEPTED belief. |
| UC-B-09: ratchet log of unjustified correlations | `check_ungrounded_corr_ratchet` | JOURNAL READER — reads CONTENT, not length. |
| UC-B-09: correlation producers are declared | `check_correlation_producers_declared` | WARN: WHO inside the perimeter computes correlation — is declared or is a finding. |
| Held-out/lags: validation-gate-repair thread suppressor | `check_heldout_ready` | validation-gate-repair thread suppressor (2026-08-08): revive held-out/lags with DATA, not memory. |
| SEC sensors completed (perms/ports/tokens) | `check_security_sensors` | SECURITY.md 'Quarterly checklist' → code (security_sensors.py). |
| changelog: every merged thread has an entry | `check_changelog_freshness` | BL-DOCAGENT-DEAD-1: every merged thread with code work has a changelog entry. |
| Project map rebuilt after thread closure. | `check_arch_map_fresh` | Project map (ARCH_SNAPSHOT ru/en) rebuilt after the last thread closure. |
| MDT consilium is fresh (producer) | `check_consilium_freshness` | monthly_consilium writes agent_report('monthly_consilium'). Silence = hypotheses |
| calendar is fresh (producer) | `check_calendar_freshness` | calendar_cache.json feeds the GP context; fetcher runs hourly. Silence = stale |
| biometrics are fresh for every tenant (producer: oura-import.partner) | `check_tenant_biometrics_freshness` | Biometrics are fresh for EVERY tenant, not only for the owner. |
| producer registry is complete (context_gate_orphan) | `check_producer_registry` | context_gate_orphan: every scheduled producer is covered by a sensor or |
| heartbeat of the entry watcher (§14) | `check_lab_intake_pulse` | §14 for the entry watcher: proves the LOOP is spinning, not merely that the process is alive. |
| entry is not blind: new files reach staging (§17) | `check_lab_intake_blind_spot` | Second half of the predicate (§17): pulse is present but no work is done. |
| review queue is moving (detect-without-delivery) | `check_lab_review_queue_movement` | Human queue is moving. Non-empty queue without movement = lost |
| staging status coverage (no row is silent) | `check_staging_status_coverage` | RATCHET: no staging row sits in a status that nobody watches. |
| glossary targets are known to canon (F-14) | `check_glossary_targets_known` | The target of a confirmed alias must be a KNOWN canonical name. |
| unit conversion coverage for labs (non-convergence) | `check_unit_conversion_coverage` | WARN burn-in (plan_consilium_under_manifest 2026-07-08 Phase 1): analyte in |
| urine naming convention (Urine_*) | `check_urine_name_convention` | Urine domain naming convention is Urine_* (owner decision 2026-08-31). |
| morning_brief: gate liveness | `check_morning_brief_gate_liveness` | Anti-repeat gate is alive: context_cards is being populated. If the gate silently fails, |
| morning_brief: does not retell the owner's own words | `check_brief_does_not_retell_owner` | WARN: the morning brief opens by retelling what the owner said to the system themselves. |
| patient_profile: does not fall behind live sources | `check_profile_reconciler_fresh` | WARN: patient_profile has diverged from the live source — reconciler is lagging or dead. |
| unified time contract (_time_inject) | `check_time_contract` | Unified time contract: production code reads the clock via _time_inject. |
| sample carrier is alive and green (§14) | `check_probe_liveness` | §14 for the SAMPLE CARRIER: the sensor on which registry promises rest must have a heartbeat. |
| test suite ran recently (§14 for the evidence carrier) | `check_suite_freshness` | §14 for the MOST LOADED sensor in the project — the test suite. |
| nightly test run completed (§14) | `check_nightly_suite_liveness` | §14 for the NIGHTLY test task (`com.larry.health.test-suite`, 00:00). |
| nightly cycle is alive (§14) | `check_night_cycle_liveness` | §14 for the nightly cycle engine: a silently dead engine means nightly failures go unnoticed. |
| decision bell is alive (§14, behavioral) | `check_doorbell_liveness` | §14 for the BELL, behavioral: silence = launchd owner_nag is dead (WARN); |
| boundary between the two lab-data houses is intact | `check_domain_boundary` | Boundary between the two lab-data houses is enforced (work B, 2026-08-01). |
| every lab-row class has a house verdict | `check_lab_class_verdicts_complete` | Every class that ACTUALLY exists in the data has a human verdict. |
| Studio machine checks reached (machine judge) | `check_machine_judge_alive` | Studio machine checks reached the owner: the machine run receipt is fresh, its findings — |

### Threshold constants (41 constants, generated)

| Constant | Value | Explanation |
|---|---|---|
| `DATA_FRESHNESS_HOURS` | 26 | oura/apple_health: conit C1 |
| `MIN_DAYS_WITH_DATA` | 5 | data from the last 7 days must be >= N |
| `GP_WEEKLY_MAX_AGE_DAYS` | 8 | GP weekly report is no older than N days |
| `GP_MONTHLY_MAX_AGE_DAYS` | 35 | GP monthly report is no older than N days |
| `GP_REPORT_MAX_AGE_DAYS` | 8 | backward compatibility → weekly |
| `MIN_ACTIVE_PROBLEMS` | 1 | minimum active issues in problem_list |
| `MIN_OPEN_TASKS` | 0 | 0 = only check that the table is readable |
| `LAB_REMINDER_DAYS` | 180 | 6 months — reminder to resubmit lab tests |
| `LAB_ALERT_DAYS` | 270 | 9 months — alert: agent is operating on stale labs |
| `PROMOTION_BACKLOG_DAYS` | 7 | staging: row 'pending auto-promote' has been waiting > N days = bottleneck |
| `GENOME_UPDATE_ALERT_DAYS` | 30 | genome_update_agent has not run for > N days |
| `HYPOTHESIS_MAX_AGE_DAYS` | 14 | hypothesis without experiment/log for > N days |
| `REPORT_MIN_CHARS` | 500 | minimum length of GP report in chars |
| `PASS` | 0 | — |
| `FAIL` | 0 | — |
| `WARN` | 0 | — |
| `_ABSENCE_SURFACE_BASELINE` | 0 | measurement 14.09: no open tracts remaining |
| `NAMING_DEBT_RATCHET` | 0 | — |
| `_PARTNER_TENANTS_AT_DECISION` | 1 | — |
| `_LAUNCHD_UNCOVERED_BASELINE` | 23 | — |
| `MACBOOK_SNAPSHOT_STALE_D` | 3 | snapshot every 3h; three days of silence is no longer 'laptop was asleep' |
| `THREAD_COPY_GRACE_H` | 6 | two snapshot periods (backup-wip every 3h), owner decision 16.09 |
| `THREAD_ORPHAN_GRACE_H` | 48 | owner decision 22.09: do not treat a fresh thread as noise for two days |
| `MEMORY_EPISODIC_GROWTH_WARN` | 1500 | active state+question; above threshold — time to build F4.1 |
| `FDR_QUAL_DRIFT_AMP_WARN` | 2.0 | σ: warning threshold; exceeding the qualification range requires re-verification. |
| `RAW_ARCHIVE_STALE_DAYS` | 15 | compression — after 14 days (hae_checker.compress_raw_archive) + one day margin |
| `GENOTYPE_PROFILE_SEED` | 3 | seed (§9 p.4): written to system_config, read from there |
| `SLEEP_DEVICE_MIXING_KNOWN` | 3 | — |
| `QUARANTINE_STUCK_DAYS` | 21 | — |
| `PASSSET_DEPTH_MIN_RATIO` | 0.6 | fraction of calendar-expected snapshots below which history is incomplete |
| `PASSSET_HISTORY_MAX` | 120 | mirror of longitudinal_analysis.PASSSET_HISTORY_MAX (cap → stay silent) |
| `TREATMENT_DATE_TOLERANCE_DAYS` | 45 | — |
| `DB_SIZE_WARN_MB` | 300 | WARN threshold |
| `DB_SIZE_FAIL_MB` | 500 | FAIL threshold |
| `LOG_UNLISTED_WARN_MB` | 1.0 | measurement 01.09: 160 files outside config, NOT A SINGLE ONE >1 MB — |
| `_LLM_TRACTS_UNGUARDED_BASELINE` | 20 | measurement 2026-08-03 by AST: 21 tracts, guard present in 1 |
| `_DIRECT_CTOR_BASELINE` | 0 | 29.09: migration completed (was 27 on 2026-08-03, 23 on 29.09) |
| `_CORR_WINDOW_DAYS` | 30 | — |
| `_RETELL_WINDOW_DAYS` | 3 | owner reply window looking back from brief date |
| `_RETELL_THETA_SEED` | 0.15 | fraction of words in the first sentence that came from the owner's reply |
| `SUITE_STALE_DAYS` | 10 | the sample carrier has a weekly rhythm and threshold of 10; we do not introduce our own rhythm |

<!-- END AUTOGEN: integrity-sensors -->

---

## 5. WHAT IS NOT TESTED AND WHY

| Component              | Status       | Reason                                   |
|------------------------|--------------|------------------------------------------|
| memory (observations)  | Backlog      | The expiration mechanism has not been worked out |
| morning report quality | Non-critical | Narrative, not medical conclusions       |
| Telegram bot E2E       | Not implemented | Requires a test account; low ROI       |
| Genome annotation       | One-off      | The data does not change, only the annotations |

---

## 6. WHEN CHECKS RUN

**Automatically, before reports are sent to Telegram:**
- Data freshness (conit) — before every morning report
- labs/genome/GP freshness — on every GP agent run
- PubMed verification — after generating each specialist/GP report

**Scheduled (launchd):**
- integrity_tests.py — daily at 07:50 (before the 08:00 report)

**Manually:**
- smoke_tests.py — before/after each code change
- check_contracts.py — when refactoring signatures across modules
