<!-- translation-of: ARCH_SNAPSHOT.md sha256:5bee0085f197 -->
**English** · [Русский](ARCH_SNAPSHOT.md)
# ARCH_SNAPSHOT — Larry Health OS

**Version:** 15.281 | **Date:** 2026-09-30

<!-- AUTO-READABLE ARCHITECTURE INDEX. The line above is written by doc_agent on post-commit
     (moved from BLUEPRINT.md on 2026-08-03; that file was deleted) — do not edit manually. -->
<!-- RULE FOR AI: read this file FIRST before any work on the project. -->
<!-- After each change, update the corresponding section. -->
<!-- “Why it is designed this way” — subsystem_intent.yaml (intent) and docs/explanation/.
     Navigation to all information homes — CLAUDE.md § B “Where things live”. -->
<!-- ⚰️ The old line “Version: 1.1 | Date: 2026-06-15” remained here while BLUEPRINT
     maintained the version: the comment's date went unchanged for 49 days and was heading for an
     INV-DOC-5 failure (60-day threshold). It now has one active writer. -->

---
---

## PROJECT PURPOSE AND CONCEPT
<!-- Updated manually. Tenant clinical context does NOT belong here — the docs are disease-neutral. -->

**Patient:** the system is multitenant. Each patient's clinical profile (diagnosis,
metrics, genome) lives in THEIR data, not in this document (brief-neutralization, 2026-07-16).
The runtime source of the profile for prompts is the tenant DB via `patient_context.build_patient_brief()`.
The human-readable developer overview of the owner is `~/health/data/PATIENT_PROFILE.md` (outside the repo/iCloud).

**Core idea:**
Health is data, not intuition. The system does not replace a doctor, but creates
an intelligence layer between raw biometric data and clinical decisions.

**What the system does:**
1. Collects data: Oura Ring (sleep, HRV, readiness) + Apple Watch + labs + check-ins
2. Analyzes: GP agent + 9 MDT specialists + lifestyle agents — daily/weekly
3. Once a month (multidisciplinary case review), generates hypotheses from metric drift → tests them → turns them into behavioral protocols
4. Tracks tasks (lab tests, questions for the doctor, actions) through Telegram + macOS Reminders
5. Contextualizes the genome: raw genotype (hundreds of thousands of SNPs) → clinically significant variants → risks by domain
6. Dashboard on Mac Studio (uvicorn :8001/:8002, tailnet-only) — data display. ⚰️ Telegram Mini App was retired on 2026-07-06 (TD-09); the `/`→`:8000` route was removed

**Workflow:**
Data → Report → Hypothesis → Protocol → Monitoring → Confirmation/Rejection

**Relevance when adding new code:**
Every new function must fit into one of the layers above.
Priority: reliable data → accurate reports → testable hypotheses → tasks that can be completed.
The medical context means that errors in data or logic have health consequences.


## SYSTEM NODES

| Node | Address | Role |
|------|-------|------|
| MacBook Pro | local | development (code, AI, Claude) |
| Mac Studio | <studio_host> (Tailscale) | production: scripts, bot, FastAPI, DB |

**Studio DB:** `~/health/data/health.db` (source of truth)
**Studio exposure** (canonical definition — `infra_config.py`, sensor SEC-20): Serve `:443` — **tailnet-only, not Funnel**; Funnel `:10000` → `:9001` neighbour project gateway. Verified live on 2026-07-06 and 2026-08-02.
**MacBook:** development; code delivery via `git push` (post-commit), **not rsync** (scripts deleted on 2026-06-28, CLAUDE.md §6 RETIRED)
**Secrets:** `~/.health_secrets/` (anthropic_key, sync_token, oura_token, telegram_*)

### Topology: who writes, where changes go, and where things live

<!-- Moved from BLUEPRINT (the “Architecture” section) on 2026-08-02. Canonical Tailscale exposure is defined in infra_config.py (sensor SEC-20);
     the launchd schedule is in §SCHEDULE below (generated). This section only shows the connections. -->

```
┌─────────────────────────────────────────────────────────┐
│  MacBook Pro (<macbook_host> Tailscale)                  │
│  Role: sole CODE writer (single-writer,                 │
│  e017929). Full git clone, remote `studio`.             │
│  Model 2026-05-23; canon: git_architecture.md           │
│                                                          │
│  Flow: edit → git commit → post-commit hook             │
│    └── git push studio main (ff-only) + bot restart     │
│  LaunchAgent: com.larry.health.backup (03:00)           │
│    └── backup.sh — tree snapshot: refs/backups/daily-*  │
│        (outside main; no commit/deploy — CLAUDE.md §1)   │
└───────────────┬─────────────────────────────────────────┘
                │ git push studio main (receive: updateInstead)
                ▼
┌─────────────────────────────────────────────────────────┐
│  Mac Studio — PRODUCTION (<studio_host> Tailscale)     │
│                                                          │
│  Data: ~/health/  (HEALTH_DATA_DIR)                     │
│    health.db (SQLite, 27 tables)                        │
│    daily_metrics/YYYY-MM-DD.json  ← import_oura.py      │
│    reports/YYYY-MM-DD.md                                │
│    genome/ ← iCloud sync                               │
│                                                          │
│  LaunchAgents: NOT listed here — generated by           │
│    gen_schedule.py in ARCH_SNAPSHOT §SCHEDULE.           │
│    A hand-written copy here went stale silently.         │
│                                                          │
│  Tailscale (canon: infra_config.py, sensor SEC-20):     │
│    Serve :443 — TAILNET-ONLY (not Funnel!) → neighbour  │
│      (route /→:8000 removed 2026-07-06 — TD-09 closed,  │
│       mini app retired; UI = dashboard :8001)           │
│    Funnel :10000 — public → :9001 neighbour gateway     │
└─────────────────────────────────────────────────────────┘
                │
                │ Telegram Bot API (Long Polling)
                ▼
         Telegram bot (chat + commands)
```

---

---

## TOP-LEVEL STRUCTURE (codebase map)
<!-- Static navigation map. Updated manually when adding a new top-level directory or class of modules. -->
<!-- The detailed module registry is below in GEN:MODULE_REGISTRY. -->

### Directories in `~/health_scripts/`

| Directory | Contents |
|---|---|
| `tests/` | 7-layer pytest pyramid: `unit/`, `integration/`, `consistency/`, `consistency_specific/`, `self_consistency/`, `meta/`, `charters/` + `fixtures/`, `reports/<YYYY-MM-DD>/` |
| `docs/` | Diátaxis: `explanation/` (why), `how-to/` (how). Reference consists of ARCH_SNAPSHOT + BLUEPRINT + USE_CASES |
| `scripts/` | Maintenance: `install_hooks.sh`, `sync_from_studio.sh`, `pre_destructive_check.sh`, `uncommitted_watchdog.py`, `git-hooks/` (pre-commit + AST silent-except) |
| `migrations/` | Python migrations of the health.db schema (survivorship, etc.). `__init__.py` defines the application order |
| `constitutions/` | Generated `.md` “health constitutions” (TCM style). Written by `generate_constitutions`; read by the dashboard display and `constitution_analysis`. NOT supplied to daily reports (`gp_agent`/`morning_report`) (verified by the 2026-07-18 audit, E3) |
| `dashboard_templates/` | Jinja2 templates for the FastAPI dashboard (Tailscale-only). Contents are not in git (gitignored) |
| `dashboard_static/` | JS/CSS for the dashboard. Not in git |
| `static/` | Legacy SPA from the VPS era (`index.html`, 51KB). Pending decision (BACKLOG: fate of index.html) |
| `charters/` | Test charters (Kaner style). Testing contracts through CH-DOCS-01 and others |
| `outputs/` | Auto-generated `longitudinal_analysis.{json,xlsx}` and others. `.gitignore`d (2026-05-19) |
| `reports/` | Analysis reports in markdown. `.gitignore`d |
| `snapshots/` | DB snapshot dumps. `.gitignore`d |
| `plans/` | Planning documents for future phases |
| `launchd/` | Copies of plist files (canonical files in `~/Library/LaunchAgents/com.larry.health.*.plist`) |
| `logs/` | Runtime logs of all agents. `.gitignore`d |

### Root files (92 `.py` + 10 `.sh`)

Logical groups:

- **launchd agents** (scheduled): `gp_agent`, `triage_agent`, `task_agent`, `morning_report`, `morning_test_summary`, `monthly_consilium`, `monthly_api_report`, `telegram_bot`, `reminders_sync`, `safety_net`, `checkin_agent`, `test_failure_handler`
- **External data importers**: `import_all`, `import_oura`, `import_apple_health`, `import_medical_events`, `import_medical_docs`, `import_hr_activity`, `genome_update_agent`, `genome_parser`, `genome_annotator`, `genome_context`, `promethease_context`, `backfill_effect_alleles`, `pubmed_client`, `pubmed_searcher`, `publication_reader`
- **HAI pipeline** (hypotheses, analysis): `hai_core`, `hai_analysis`, `hai_chat`, `hai_context`, `hai_reports`, `hai_hypotheses`, `hypothesis_semantic_check`, `literature_curator`
- **CBCR pipeline** (Compass-Based Clinical Reasoning): `cbcr_hypothesis`, `cbcr_lookup`
- **Analytics**: `longitudinal_analysis`, `constitution_analysis`, `survivorship_analyzer`, `survivorship_curator`, `swot_analysis`, `lab_extractor`
- **Documentation generators**: `gen_arch_blocks`, `gen_blueprint`, `gen_key_paths`, `gen_schedule`, `generate_constitutions`, `generate_test`
- **Foundation / core**: `health_db` (single-primary, SQLite, hostname-aware), `integrity_tests` (15+ checks), `_time_inject`, `_domain_audit`, `_fmt_helpers`
- **Dashboard / API**: `dashboard` (FastAPI + Jinja2 + HTMX), `api_spend_log`, `main`
- **Wellally**: `wellally_consult`, `check_wellally_updates`
- **Calendar / Reminders**: `calendar_client`, `calendar_sync`, `reminders_sync`
- **Maintenance / contracts**: `arch_guard`, `check_contracts`, `propose_uc`, `validate_uc_index`, `update_problem_list`, `doc_agent`
- **Tests / smoke**: `smoke_tests`, `test_oura`
- **Assessment** (questionnaires): `assessment_bot_handlers`, `assessment_dialog`, `assessment_importer`, `assessment_scheduler`
- **Lifestyle**: `lifestyle_agents`, `consult_prep`
- **Migration-only helpers** (one-off, `_` prefix): `_create_*`, `_doc_update`, `_fix_*`, `_generate_*`, `_lifestyle_*`, `_patch_*`, `_rebuild_*`, `_sleep_audit` — not run again after use
- **Legacy from the VPS era**: `vps_sync` (under review)

`.sh` (9): `backup.sh`, `backup_studio.sh`, `run_checks.sh`, `run_full_test_suite.sh`, `watch_and_import.sh`, `watch_and_test.sh`, `morning_wake.sh`, `doc_sync.sh`, `icloud_conflict_check.sh` (`checkin_wake.sh` was deleted on 2026-08-17 along with automatic check-in startup)

### Related spaces

- **`~/health/` (iCloud)** — patient data + 19 analyzer-skills + AGENTS.md. Internal map: `~/health/MAP.md`. Cross-link: `health_db._resolve_health_dir()`
- **`~/.infrastructure.md`** — manifest of nodes, ports, services, secrets (shared by health-os and neighbour projects)

---

<!-- GEN:SCHEDULE:START -->

## launchd SCHEDULE (Studio)
<!-- Generated by gen_schedule.py from ~/Library/LaunchAgents/com.larry.health.*.plist; descriptions are machine-translated from the Russian source. -->
<!-- Do not edit manually. -->

| Label | Schedule | Module |
|---|---|---|
| `com.larry.health.assessment-scheduler.partner` | 03:30 | `assessment_scheduler.py` |
| `com.larry.health.assessment-scheduler` | 03:30 | `assessment_scheduler.py` |
| `com.larry.health.backup` | 03:00 | `backup_studio.sh` |
| `com.larry.health.bot.partner` | KeepAlive | `telegram_bot.py` |
| `com.larry.health.bot` | KeepAlive | `telegram_bot.py` |
| `com.larry.health.calendar-sync.partner` | *:05 | `google_calendar_fetcher.py` |
| `com.larry.health.calendar-sync` | *:05 | `google_calendar_fetcher.py` |
| `com.larry.health.code-watcher` | KeepAlive | `watch_and_test.sh` |
| `com.larry.health.colima` | RunAtLoad | `sh` |
| `com.larry.health.consilium.partner` | day 1 04:00 | `bash` |
| `com.larry.health.consilium` | day 1 04:00 | `bash` |
| `com.larry.health.constitutions.partner` | Sun 05:30 | `bash` |
| `com.larry.health.constitutions` | Sun 05:30 | `bash` |
| `com.larry.health.dashboard.partner` | KeepAlive | `dashboard.py` |
| `com.larry.health.dashboard` | KeepAlive | `dashboard.py` |
| `com.larry.health.import-poll` | every 5min | `import_all.py` |
| `com.larry.health.import-watchdog` | every 2h | `import_watchdog.py` |
| `com.larry.health.integrity-check.partner` | 07:50 | `run_checks.sh` |
| `com.larry.health.integrity-check` | 07:50 | `run_checks.sh` |
| `com.larry.health.lab-intake.partner` | KeepAlive | `lab_intake_watcher.py` |
| `com.larry.health.lab-intake` | KeepAlive | `lab_intake_watcher.py` |
| `com.larry.health.literature-read.partner` | 04:30 | `run_literature_pipeline.sh` |
| `com.larry.health.literature-read` | 04:30 | `run_literature_pipeline.sh` |
| `com.larry.health.literature-search.partner` | Sun 04:00 | `pubmed_searcher.py` |
| `com.larry.health.literature-search` | Sun 04:00 | `pubmed_searcher.py` |
| `com.larry.health.logrotate` | every 30min | `log_rotate.py` |
| `com.larry.health.longitudinal.partner` | Sun 03:00 | `bash` |
| `com.larry.health.longitudinal` | Sun 03:00 | `bash` |
| `com.larry.health.multitenant-env` | RunAtLoad | `launchctl` |
| `com.larry.health.night-cycle` | 08:00 | `night_cycle.py` |
| `com.larry.health.night-repair` | 08:40 | `night_repair.py` |
| `com.larry.health.oura-import.partner` | 08:00; 11:00; 14:00; 17:00; 20:00 | `bash` |
| `com.larry.health.oura-import` | 08:00; 11:00; 14:00; 17:00; 20:00 | `bash` |
| `com.larry.health.owner-nag` | 08:00; 11:00; 14:00; 17:00; 20:00 | `owner_nag.py` |
| `com.larry.health.owner-weekly` | Mon 09:10 | `owner_weekly.py` |
| `com.larry.health.pgs-discovery.partner` | day 5 03:00 | `pgs_discovery.py` |
| `com.larry.health.pgs-discovery` | day 5 03:00 | `pgs_discovery.py` |
| `com.larry.health.pilot-shadow` | every 1h | `pilot_shadow.py` |
| `com.larry.health.pipaudit` | Mon 03:30 | `pip_audit_check.py` |
| `com.larry.health.probes` | Mon 06:20 | `run_probes.sh` |
| `com.larry.health.profile-reconciler.partner` | 06:00 | `bash` |
| `com.larry.health.profile-reconciler` | 06:00 | `bash` |
| `com.larry.health.reminders-sync.partner` | every 3h | `reminders_sync.py` |
| `com.larry.health.reminders-sync` | every 3h | `reminders_sync.py` |
| `com.larry.health.survivorship.partner` | Mon 03:30 | `run_survivorship_pipeline.sh` |
| `com.larry.health.survivorship` | Mon 03:30 | `run_survivorship_pipeline.sh` |
| `com.larry.health.test-api-report` | day 1 09:30 | `monthly_api_report.py` |
| `com.larry.health.test-suite` | 00:00 | `run_full_test_suite.sh` |
| `com.larry.health.triage` | 08:00 | `triage_agent.py` |
| `com.larry.health.uncommitted-watchdog` | every 1h | `uncommitted_watchdog.py` |
| `com.larry.health.watcher` | KeepAlive | `watch_and_import.sh` |
| `com.larry.health.weekly-digest` | Sat 22:00 | `weekly_digest.py` |
| `com.larry.health.wellally-check` | Mon 10:00 | `check_wellally_updates.py` |

<!-- GEN:SCHEDULE:END -->


---

## EXTERNAL INTEGRATIONS


ServiceSecret fileUsageModuleAnthropic Claude API`~/.health_secrets/anthropic_key`AI, reports, chathealth_ai, gp_agent, checkin_agent, genome\_\*Oura Ring API v2`~/.health_secrets/oura_token`Sleep, HRV, activityimport_ouraTelegram Bot API`~/.health_secrets/telegram_token`User interfacetelegram_botTelegram Chat ID`~/.health_secrets/telegram_chat_id`Target chattelegram_botPubMed E-utilitiesnone (public)Medical literaturepubmed_client[MyVariant.info](http://MyVariant.info)none (public)Genome variant annotationgenome_annotatorGWAS Catalognone (public)Trait associationsgenome_contextApple Healthexport XML (iCloud)Steps, heart rate, weightimport_apple_healthmacOS Calendaricalbuddy (brew)Events, travelcalendar_clientmacOS RemindersosascriptUser taskstask_agent, reminders_syncVPS API`~/.health_secrets/sync_token`Data synchronizationvps_sync, telegram_bot

---


## DB SCHEMA (health.db — 25 tables)

### Core data
```
daily_metrics      (date PK, sleep_total, sleep_deep, sleep_rem, sleep_score,
                    hrv, resting_hr, readiness, steps, active_kcal, spo2_avg,
                    weight, vo2max, raw TEXT)

lab_results        (id, date, source, test_name, value, value_text, unit,
                    ref_low, ref_high, status, notes, created_at, event_id,
                    specimen, value_op, method)
                   row identity = UNIQUE(date, test_name, source,
                   COALESCE(specimen,''), COALESCE(method,'')) — index idx_lab_uniq;
                   COALESCE is not cosmetic: NULL != NULL would disable dedup for rows
                   without a method, which are the majority. `method` is what is PRINTED
                   on the form (§17, loinc-name-home thread); dedup is decided in
                   lab_promote.split_groups; the index is a backstop, not the judge.

context_events     (id, date, ts, source, category, key, value_num, value_text,
                    period_id, tags JSON)

periods            (id, name, type, start_date, end_date, tags JSON, source, notes, active)
```

### Genome
```
raw_snps           (rsid PK, chromosome, position, genotype)
                   -- hundreds of thousands of records, imported from a raw genotyping file (23andMe v5 format)

genetic_variants   (id, rsid UNIQUE, gene, genotype, significance, prev_significance,
                    conditions JSON, domain_tags JSON, effect_allele,
                    clinical_summary, annotation_source, annotated_at, updated_at)
                   -- clinically significant only, annotated via myvariant.info + ClinVar

genome_update_log  (id, run_date, variants_checked, variants_changed,
                    changes_json, narrative, sent_to_user)
```

### AI / Intelligence
```
memory             (id, date, category TEXT, key, value JSON, confidence,
                    source, active, created_at, updated_at)
                   -- category: 'hypothesis' | 'protocol' | 'fact' | ...
                   -- HYPOTHESES are stored here as JSON with fields:
                   --   observation, mechanism, prediction, test,
                   --   status (open|testing|confirmed|rejected),
                   --   trigger (manual|drift), linked_experiment_id,
                   --   created_date
                   --   resolution_type: self_managed|needs_specialist

patterns           (id, discovered, category, description, evidence, confidence, is_active)
agent_reports      (id, date, agent_type, agent_name, period_days, has_findings,
                    data_queried JSON, pubmed_ids JSON, peers_reviewed JSON,
                    changes_summary, findings, recommendations, data_requests, raw_output)
conversation_history (id, role, content, created_at)
```

### Clinical data / tasks
```
problem_list       (id, problem_id UNIQUE, title, description,
                    status: active|active_monitoring|watchful_waiting|resolved,
                    priority 1-3, domain, first_seen, last_updated,
                    watch_trigger, watch_deadline, supporting_data JSON, notes)

problem_list_proposals (id, created_at, source, proposed JSON,
                    status: pending|approved|rejected, reviewed_at, review_note)

tasks              (id, created_at, source, source_date, type, priority,
                    content, deadline, status: open|answered|completed|dismissed|snoozed,
                    resolved_at, resolved_text, sent_at, followup_sent_at,
                    reason, fingerprint,
                    resolution_type: self_managed|needs_specialist)

recommendations    (id, date, text, category, followed_up, outcome)
```

### Experiments / protocols
```
experiments        (id, name, hypothesis, intervention, start_date, end_date,
                    status: active|completed|abandoned, result, notes,
                    baseline_metrics JSON, target_metrics JSON,
                    check_days JSON, check_results JSON)

experiment_log     (date, experiment_id FK, adhered, notes)

protocols          (id, title, behavior, rationale, frequency,
                    linked_hypothesis_id, linked_experiment_id,
                    reminder_days JSON, status: active|retired,
                    created_at, retired_at, notes)
```


consultations      (id, date, specialist_type, platform, specialist_name,
                    key_findings, source_file, created_at)
                   -- doctor visits; populated from import_medical_docs.py
                   -- and when importing new medical PDFs

### Check-ins
```
checkins           (id, date, time_of_day: morning|evening, question, answer,
                    context, created_at)
checkins_fts       -- virtual FTS5 table for full-text search
```

---

<!-- GEN:MODULE_REGISTRY:START -->

## MODULE REGISTRY
<!-- Generated by gen_blueprint.py 2026-09-30; descriptions are machine-translated from the Russian source. -->

### DATA LAYER
```

health_db.py  # SQLite layer for the health system.
  on_canonical_db() — Checks whether THIS process is looking at the owner's production 
  assert_not_canonical(purpose) — FAIL-CLOSED guard for operations that MUST NOT touch production s
  attach_reference(conn) — Attach the shared reference dictionary to the connection as schem
  get_conn(read_only) — Returns an SQLite connection.
  init_db() — Creates the schema if it does not exist.
  question_answer_gate_ddl() — DDL for the 'question not closed by void' gate — the single home 
  migrate_all_json() — Migrates all existing JSON files into SQLite.
  import_biochemical_json(filepath) — Imports one file from the biochemical/ folder. Returns the number
  import_all_biochemical() — Imports all files from the biochemical/ folder.
  log_repair(conn) — Writes ONE value-change entry to the log. Must be called within t
  revert_repairs(run_id) — Returns previous values from the run log. Returns the number of r
  migrate_v2() — Creates new v2 architecture tables.
  get_absolute_thresholds_for_person(direction) — Threshold rules with human-language reasons (seeded text is trans
  get_trend_thresholds_for_person() — Window rules with human-language reasons; storage — get_trend_thr

agent_reports_db.py  # agent_reports_db.py — domain module agent_reports. Extracted from health_db.py (
  save_agent_report(agent_type, agent_name, date_str, has_findings) — Saves an agent report.
  get_agent_report(agent_name, date_str, n) — Last N agent reports.
  get_reports_with_findings(date_from, agent_type) — Reports with findings for a period — for building the summary rep

alerts_db.py  # alerts_db.py — domain module alerts. Extracted from health_db.py (Stream C, stra
  save_alert(type_, message, severity, source) — SX-1.8: insert into alerts. Returns id.
  get_active_alerts(source_like) — SX-1.8: list active alerts, optionally filtered by source LIKE pa

assessments_db.py  # assessments_db.py — domain module assessments. Extracted from health_db.py (Stre
  save_assessment_session(instrument_id, wording_version_hash, chat_id, task_id) — SX-1.8: create new assessment session row. Returns id.
  get_assessment_session(session_id) — SX-1.8: load one session by id.
  get_active_assessment_session(chat_id, instrument_id) — SX-1.8: find an active (in_progress) session for chat_id, optiona
  update_assessment_session(session_id, answers_json, status, completed_at) — SX-1.8: update progress / status of assessment session.

checkins_db.py  # checkins_db.py — domain module checkins. Extracted from health_db.py (Stream C, 
  get_recent_checkins(n)
  save_checkin(day, question, answer, context)
  get_checkin_by_date(date_str, time_of_day) — Returns a check-in by date. time_of_day: 'morning' | 'evening' | 
  update_checkin_scores(day, time_of_day, stress_score, mood_score) — UPDATE the last check-in for day+time_of_day with three scores.

config_db.py  # config_db.py — domain module config. Extracted from health_db.py (Stream C, stra
  default_visit_specialist() — Default specialist for /visit — per-tenant from system_config, ot
  get_config(key, default, conn) — Reads a configuration parameter from the DB.
  get_routing_keywords() — Returns {domain: [keyword,...]}. Special key '__full__' contains
  get_domain_signals(domain) — Returns domain-signals for evaluate_domain_need.
  get_doc_patterns() — Returns all document classification patterns from the DB.
  mark_imported(source_file, doc_type) — Marks a file as imported. INSERT OR IGNORE — safe under race cond
  get_imported_sources() — Returns the set of full relative paths of already-imported files.
  upsert_config(key, value_text, value_num, value_json) — Writes or updates a system configuration parameter.
  ocr_languages(conn) — Tenant OCR languages for `tesseract -l` (system_config `ocr.langu
  doc_type_markers(conn) — Tenant document type markers (system_config `docs.type_markers`):
  marker_doc_type(markers, where, haystack) — First doc_type whose substring (case-insensitive) is found in hay

constitutions_db.py  # constitutions_db.py — domain module constitutions. Extracted from health_db.py (
  upsert_constitution(domain, body_md, title, source_version) — Writes/updates the constitution narrative for a domain (sleep|nut
  get_constitution(domain) — Returns {domain, title, body_md, generated_at, source_version} or
  list_constitutions() — List of constitutions without body_md: domain, title, generated_a

consult_sessions_db.py  # consult_sessions_db.py — domain module consult_sessions. Extracted from health_d
  save_consultation_session(chat_id, session_dict) — Saves (or updates) an in-progress /consult session in the DB.
  load_consultation_session(chat_id) — Loads a saved session. Returns dict or None if not found.
  delete_consultation_session(chat_id) — Deletes a persistent session (called on /end or timeout).

consultations_db.py  # consultations_db.py — doctor appointments. Extracted from health_db.py (Stream C
  specialty_key(s) — One specialty under different wordings is reduced to a common key
  get_consultations(n, specialist_type) — Last N doctor visits (from the medical record).
  get_last_consultation(specialist_type) — Last doctor appointment, optionally filtered by specialty (spelli
  consultation_exists(date_str, specialist_type) — Whether the medical record contains an appointment for this date 
  save_consultation(date_str, specialist_type, specialist_name, platform) — Writes a doctor appointment to the medical record (events + encou

dashboard_db.py  # dashboard_db — low-level DB layer for FastAPI dashboard.

doc_reviews_db.py  # doc_reviews_db.py — domain module doc_reviews. Extracted from health_db.py (Stre
  save_pending_doc_review(source_file, proposed_type) — Queues a document for type confirmation.
  get_pending_doc_reviews() — Returns all records with status needs_review.
  mark_doc_review_sent(review_id) — Marks a record as 'notification sent' (awaiting user reply).
  confirm_doc_review(review_id, confirmed_type) — Saves the confirmed type, transitions status to confirmed.
  reject_doc_review(review_id) — Marks a review as rejected (user clicked Skip).
  auto_confirm_stale_reviews(hours) — Auto-confirms pending records older than N hours.
  import_from_pending(pending_path) — Imports pending_labs JSON into lab_results after Telegram confirm

events_db.py  # events_db.py — domain module events. Extracted from health_db.py (Stream C, stra
  save_event(event_type, effective_date, status, performer) — Creates an event in events + subtable atomically. Returns event_i
  get_events(n, event_type, status, date_from) — Last N medical record events with encounters/diagnostics details.
  get_context_events(date_from, date_to, category, source) — Returns context events for a time period.
  save_context_event(date_str, source, category, key) — Saves a life-context event.

experiments_db.py  # experiments_db.py — RETIRED (BL-EXP-1, 2026-07-10).
  get_active_experiments()
  get_experiment_stats(exp_id)
  complete_experiment(exp_id, result, notes)
  log_experiment_day(exp_id, adhered, notes, day)
  start_experiment(name, hypothesis, intervention, start_date)
  update_experiment_check_results(exp_id, check_results_json)

field_reviews_db.py  # field_reviews_db.py — domain module field_reviews. Extracted from health_db.py (
  confirm_field_alias(format_id, raw_name, canonical, source_example) — Confirms (or creates) an alias. Checks orphan status.
  queue_field_reviews(format_id, source_file, unknown_fields) — Queues a list of unknown fields for review.
  get_pending_field_reviews(format_id) — Returns pending field reviews (optionally filtered by format_id).
  set_field_review_tg_message(review_id, tg_message_id) — Saves the Telegram message_id of the notification for reply-to-me
  get_field_review_by_tg_message(tg_message_id) — Finds a pending field review by Telegram notification message_id.
  resolve_field_review(review_id, canonical) — Confirms (canonical is not None) or rejects a field review.

genome_db.py  # genome_db.py — domain module genome. Extracted from health_db.py (Stream C, stra
  get_significant_variants(domain, limit) — Returns clinically significant variants, optionally filtered by d
  get_carrier_variants(limit) — Verified CARRIERS: effect_allele_status='resolved' and effect_all
  carrier_status_null_allele_violations(conn) — Rows violating linkage: carrier status ⟹ effect_allele is populat
  get_variants_by_genes(genes, limit) — Returns variants from genetic_variants for a list of genes.
  genome_summary_counts() — Aggregate counters for genome_context (F5). 'patho' = Pathogenic/
  get_snps_batch(rsids) — Returns {rsid: genotype} for a list of rsIDs.
  upsert_genetic_variant(rsid, data) — Saves or updates an annotated variant.
  save_genome_update_log(data) — Saves the log of a monthly genome update.
  get_raw_snp(rsid) — Returns a genotype from raw_snps by rsID.
  mark_genome_log_sent(log_id)

hae_db.py  # hae_db.py — domain module for hae. Extracted from health_db.py (Stream C, strang
  get_hae_registry() — Returns {metric_name: row_dict} for the entire registry.
  upsert_hae_metric(metric_name, status, unit, sample_values) — Inserts or updates a record in the registry.
  get_pending_hae_alerts(alert_cooldown_days) — Returns metrics that need to be alerted: status='new' AND

hypotheses_db.py  # hypotheses_db.py — domain module for hypotheses. Extracted from health_db.py (St
  save_hypothesis_outcome(memory_id, verdict, confidence, evidence) — Saves a new outcome for a hypothesis (evaluation history, not ups
  get_hypothesis_outcome(memory_id) — Returns the latest outcome for a hypothesis or None.
  get_hypotheses_awaiting_evaluation() — Hypotheses with status='testing' whose CURRENT round has not rece
  get_hypothesis_accuracy_stats() — Accuracy statistics by source (auto_consilium, manual, etc.).
  get_unsent_hypothesis_outcomes() — Returns outcomes that have not been delivered to Telegram.
  mark_hypothesis_outcome_delivering(memory_id, chat_id, outcome_id) — Marks that delivery has started (protection against double-send o
  mark_hypothesis_outcome_sent(memory_id, outcome_id) — Marks that the result has been delivered to Telegram.
  save_cbcr_payload(memory_id, payload_json, structural_score, confidence_level) — Writes or updates a CBCR-payload linked to memory.id.
  get_cbcr_payload(memory_id) — Returns the CBCR-payload (parsed JSON) for memory.id or None.

import_status_db.py  # import_status_db.py — domain module for import_status. Extracted from health_db.
  set_import_status(source, ts) — Writes the timestamp of the last successful import for a source.
  get_import_staleness(source) — Returns (is_stale, age_hours) for a data source.
  check_data_freshness(sources) — Checks data freshness for all or specified sources.
  get_conit_limit(source) — Returns the freshness limit for a data source (hours).

labs_db.py  # labs_db.py — domain module for labs. Extracted from health_db.py (Stream C, stra
  get_recent_labs(n_days, key_tests, exclude_pro) — Returns the latest result for each test over the last n_days days
  canon_window_note(n_days, exclude_pro, shown_tests, named_below) — Declares the boundary of a lab block window: what is shown and wh
  declared_boundary(n_days, unjudged) — `canon_window_note` that does NOT raise: a build failure itself b
  get_lab_series(test_name, n_days, exclude_pro) — ALL numeric results for a single test over n_days, chronologicall
  get_modal_reference(test_name, min_docs) — Modal (ref_low, ref_high) across DIFFERENT documents (source); No
  get_lab_trend(test_name, n) — Last n results for a specific test for trend construction.
  trend_members(mappings, terms, our_name, unit) — Which of our names form ONE trend with this one. Pure: index as a
  get_lab_trend_by_component(our_name, unit, specimen, n) — Trend by SUBSTANCE via LOINC mapping, not by our name.
  result_text(row) — Canon row → human-readable result. Never returns raw text.
  flag_direction(r) — Deviation arrow for a canon row: lab annotation if printed; other
  draw_summaries() — Submitted tests in brief — by draw (draw_key: file + date), for t
  get_labs_by_date(date_str) — All results for a specific date.
  get_lab_history(days) — All lab results for the last N days, chronologically.
  compute_bank_refs(min_docs) — Reference for each analyte = MODE of the printed form interval ac
  get_lab_refs() — Reference intervals: CACHE of form modes (system_config.lab_refs,
  get_lab_refs_meta() — {test_name: n_docs} and calculation date — provenance of the refe
  get_effective_lab_schedule() — Returns {test_name: {interval_days, priority, source, note}} from
  build_lab_history_context(min_points, max_hist, horizon_days) — Full lab history with recency annotation by effective validity pe
  unrepeated_draw(rows, cutoff) — Pure block logic. rows — canon rows (dict/Row) with fields
  build_unrepeated_draw_context(n_days) — Block «submitted once and never retaken» for LLM contexts.
  draw_key(source, date_str) — conit identifier of a draw for co-draw: (basename without doc: pr
  build_codraw_context(days, min_cluster) — Slice of fence readings for the last `days` days (source + date).
  build_specialized_context(max_abnormal) — Summary of specialized_lab_results (microbiome/autoantibodies/met
  upsert_monitoring_rule(test_name, interval_days, priority, source) — Upsert monitoring schedule. default does not override encounter/m
  get_lab_format_by_name(name)
  get_lab_format_by_id(format_id)
  get_confirmed_aliases(format_id, conn) — Returns {raw_name: canonical} for confirmed aliases of the format
  confirmed_alias_conflicts(conn) — Raw names to which DIFFERENT formats assigned different canonical
  confirmed_alias_targets(conn) — Set of canonical names referenced by the confirmed glossary.
  analyte_norm_verdicts(conn, today) — {канон аналита: verdict} из `analyte_norm_verdicts` — только ЖИВЫ ⟨untranslated⟩
  set_analyte_norm_verdict(analyte, verdict, rationale, oracle) — Write/replace an analyte norm verdict. Name is normalized via lab
  domain_verdicts(conn) — {класс строки: дом} из `lab_domain_verdicts`. Дом ∈ {canon, speci ⟨untranslated⟩
  domain_home(panel_type, conn) — Domain of a class by human verdict. None = no verdict, not for th
  get_confirmed_aliases_all(conn) — {raw_name.lower(): canonical} across ALL formats — glossary for p
  get_all_canonical_names() — All unique canonical names in lib (for fuzzy-matching).
  get_all_lab_dates() — All dates for which lab results exist.
  effective_freshness(active_conds) — Freshness schedule for the tenant: BASE + conditional tests of it

memory_db.py  # memory_db.py — domain module for memory. Extracted from health_db.py (Stream C, 
  get_memory(category, n, active_only)
  save_memory(category, value, key, confidence) — Saves a fact to memory. If key is provided — updates the existing

metrics_db.py  # metrics_db.py — domain module for metrics. Extracted from health_db.py (Stream C
  get_day(day_str)
  get_window(end, days)
  get_stats(days, end) — Aggregated statistics for a period.
  get_metric_percentiles(baseline_days) — Returns the percentile rank of yesterday's key metrics
  upsert_metrics_from_json(day_str, data, source) — W5K-B (2026-05-14): per-source ownership.
  build_context(target) — Assembles a compact context (~2000 tokens) for report generation.
  metric_columns(conn) — Columns of daily_metrics that are metric indicators (everything e
  render_all_metrics(days, end) — Block «all collected metrics» for any clinical context.
  data_sources(days, end) — Sources from which data actually arrived within the window (by co

periods_db.py  # periods_db.py — domain module for periods. Extracted from health_db.py (Stream C
  historical_periods(exclude_types, include_deleted) — All periods (past + current + future), default excludes soft-dele
  current_periods(date_str) — Periods active on a given date (default: today). Excludes soft-de
  get_active_period(date_str) — DEPRECATED (2026-06-19): use current_periods().
  save_period(name, type_, start_date, end_date) — Creates a new period (active=1, deleted_at=NULL by default).
  future_periods() — Periods starting in the future. Excludes soft-deleted.
  soft_delete_period(period_id, reason) — Mark period as soft-deleted (sets deleted_at=now()).

problems_db.py  # problems_db.py — domain module for problems. Extracted from health_db.py (Stream
  get_problem_list(status) — Returns the problem list, optionally filtered by status.
  upsert_problem(problem_id, title, description, status) — Creates or updates a problem in the problem list.
  proposal_dedup_key(ch, source, conn) — Edit identity (2026-09-01, owner decision: "one edit — one row, d
  existing_problem_for(ch, conn) — The medical-record problem that an 'add' proposal DUPLICATES: a l
  save_problem_proposal(source, changes, conn) — Saves proposals to change the problem list — ONE EDIT = ONE ROW.
  record_surveillance_decision(topic, title, decision, decided_by) — Write a doctor/owner decision on a monitoring topic. The previous
  active_surveillance_decisions(on_date) — Active decisions: not retired and (valid_until IS NULL or ≥ on_da
  format_surveillance_decisions(decs) — Prompt lines (GP, curator): one per decision, with author, deadli

profile_db.py  # profile_db.py — domain module for profile. Extracted from health_db.py (Stream C
  get_patient_profile(category) — Returns the patient profile as a key→value dictionary.
  profile_fields() — Profile fields that a person reports about themselves: key → spec
  apply_stated(name, raw, source) — Write to the profile what the person said about themselves — if t
  upsert_profile(key, value_text, value_json, category) — Writes or updates a patient profile field.
  get_profile_context() — Reads the patient profile. DB-first (patient_profile), fallback t

proposals_db.py  # proposals_db.py — domain module for proposals. Extracted from health_db.py (Stre
  get_pending_proposals() — Returns all unconfirmed proposals.
  get_undelivered_proposals() — Pending decisions for the person that have not yet been delivered
  stuck_undelivered_proposals() — (Total pending and undelivered count, id of items stuck longer th
  mark_proposal_delivered(proposal_id, tg_message_id) — Delivery receipt — set AFTER successful send (failure leaves the 
  format_proposal_card(prop) — Proposal card text — PLAIN text, no markup. Single home for outbo
  apply_proposal(proposal_id) — Applies a proposal: makes changes to problem_list.
  reject_proposal(proposal_id, note) — Rejects a proposal without changes to problem_list.
  expire_aged_proposals(days) — Lifecycle: pending older than threshold → 'expired'. Returns coun

protocols_db.py  # protocols_db.py — domain module protocols.
  get_active_protocols(domain)
  save_protocol(protocol) — Saves a protocol to the protocols table.
  retire_protocol(protocol_id, note) — Retires a protocol (status → retired).

rules_db.py  # rules_db.py — domain module for rules. Extracted from health_db.py (Stream C, st
  get_absolute_thresholds(direction) — Returns active absolute thresholds. Optional filter by direction.
  get_trend_thresholds() — Active trend/window thresholds (N consecutive days / moving avera
  get_lab_trend_thresholds() — Active LAB trend thresholds (safety-net-thresholds, §9 extraction
  get_threshold(metric, direction, band_label, variant) — §9: the sole runtime source of clinical norms is the absolute_thr
  get_active_constraints(protocol_id) — SX-1.8 (2026-05-18): now reads from `alerts` table.

tasks_db.py  # tasks_db.py — domain module for tasks. Extracted from health_db.py (Stream C, st
  save_task(source, type_, content, priority) — Saves a task to the database.
  resolve_task(task_id, resolved_text, status) — Closes an OPEN (or deferred) task. True — closed; False — not fou
  get_open_tasks(limit) — Returns unclosed tasks sorted by priority.
  get_overdue_tasks(days_old) — Tasks that have been open longer than N days without a response.
  get_unsent_tasks(type_) — Outbox for tasks of one type: open, with sent_at IS NULL, oldest 
  get_questions_needing_delivery() — Open questions with nowhere to reply: no return address.
  get_open_questions() — ALL open questions, delivered and not — the set against which
  get_task_by_tg_message(tg_message_id) — Open task to whose message a reply arrived.
  get_unsent_assessment_tasks() — Assessment outbox: open tasks with type='assessment' where sent_a
  wake_snoozed_assessment_tasks(today) — A deferred assessment whose due date has arrived is placed back i
  mark_task_sent(task_id, tg_message_id) — Marks a task as sent to Telegram.

treatment_db.py  # treatment_db.py — domain module for treatment. Extracted from health_db.py (Stre
  get_medications(confirmation, include_proposed) — List of treatment regimens. By default only canonical ones (gate 
  upsert_medication(name, modality, intent, cycles_completed) — Idempotent upsert of a treatment regimen into medications.
  get_episodes(status, problem_id) — Returns episodes, optionally filtered by status and/or problem.
  set_medication_confirmation(med_id, confirmation) — Human gate: confirm/reject an extracted regimen.
  get_proposed_medications() — Regimens awaiting human confirmation (for gate cards).
  get_unsent_proposed_medications() — Proposed regimens for which a gate card has NOT yet been sent.
  mark_medication_gate_sent(med_id) — Marks that the regimen confirmation card has been sent (do not se
  save_episode(title, start_date, primary_problem_id, end_date) — Creates a help episode. Returns episode_id.
  complete_planned_event(event_id, interpreted_report, abnormal_flags) — Transitions a planned event → completed. Updates diagnostic_event

workouts_db.py  # workouts_db.py — domain module for workouts. Extracted from health_db.py (Stream
  upsert_workout(date_str, workout) — Saves a workout. Deduplicates by date + start_time.

```

### INTELLIGENCE LAYER
```

health_ai.py  # health_ai — public interface of the intelligence layer.

hai_core.py
  get_client()
  get_model(role) — Model for a role. system_config.model.<role> > MODEL_DEFAULTS.
  model_for(task_role) — Model for the semantic role of a task (ROLE_MODELS → get_model(ti
  answer_language(lang) — Model response language = user language (thread model-lang, 28.09
  with_answer_language(build) — System-prompt builder decorator: appends answer_language() at the
  get_system_prompt()
  save_message(role, content)
  get_history(n)
  wrapped()

hai_context.py  # hai_context — context blocks, tool execution, smart context.
  build_context_block_compact(target) — Compact data block (~500 tokens) for substitution into a chat req
  build_smart_context(question, target) — Proactive context: analyses the question, loads the required data
  fmt_sleep(d)

hai_analysis.py  # hai_analysis — recovery index, metric drift detection.
  compute_recovery_index(target, window_days, conn) — Recovery index: compares current metrics against pre-illness base
  recovery_baseline(conn) — Tenant averages over its baseline window; see _baseline_window.
  recovery_series(target, days, window_days) — Trailing composites (NEWEST first) for `days` days — series for p
  format_recovery_index(ri) — Text string for injection into GP context:
  detect_metric_drift(target, window, streak_threshold) — Detects sustained metric drift relative to personal rolling basel
  format_drift_report(drifts) — Short text for injection into GP context.
  detect_correlation_drift(target, window, baseline_window, threshold_r) — Detects metric pairs with sharply changed correlation.
  format_correlation_drift_report(drifts) — Short text for injection into GP context. See C-4 (P5-future).

hai_hypotheses.py  # hai_hypotheses — hypotheses, protocols, formatting.
  save_hypothesis(observation, mechanism, prediction, test) — Saves a hypothesis to memory (category='hypothesis').
  get_open_hypotheses(n) — Returns active (status != confirmed/rejected) hypotheses from mem
  hypothesis_verdict(memory_id) — Hypothesis verdict: 'confirmed' | 'rejected' | None (open/not fou
  update_hypothesis_status(memory_id, status, note) — Updates hypothesis status (open → testing → confirmed | rejected)
  append_dynamics_to_hypothesis(memory_id, note) — Stage 2: appends a series-dynamics record to the hypothesis (dyna
  generate_hypothesis_from_drift(drifts, linked_experiment_id) — W5H-B (2026-05-14): defunct.
  confirm_hypothesis(memory_id) — Confirms the hypothesis (status → confirmed), returns payload.
  reject_hypothesis(memory_id, reason) — Rejects the hypothesis (status → rejected).
  get_specialist_hypotheses(n) — Open hypotheses with resolution_type=needs_specialist.
  generate_protocol_from_hypothesis(hypothesis) — LLM generates a behavioural protocol from a confirmed hypothesis.
  save_protocol(protocol) — Saves a protocol to the protocols table.
  get_active_protocols() — Returns all active protocols.
  retire_protocol(protocol_id, note) — Retires a protocol (status → retired).
  format_hypotheses_for_gp(hypotheses) — Short block for injection into GP context — active hypotheses.
  generate_hypothesis_from_correlation(correlation_drifts, linked_experiment_id) — W5H-B (2026-05-14): defunct.

hai_reports.py  # hai_reports — morning, weekly, monthly reports + extended context block.
  build_context_block(target) — Full data block for generating the morning report.
  generate_morning_report(target) — Generates the morning report via Claude Sonnet.
  generate_weekly_report(end_date) — Weekly report with MDT WellAlly + PubMed evidence base.
  generate_monthly_check(target) — Monthly report with vital metrics check and tasks.
  fmt_sleep(d)
  trend_arrow(v7, v90)
  pct(a, b)

hai_chat.py  # hai_chat — chat(), run_arbiter(), CLI.
  build_chat_payload(user_message, include_data) — Unified model input assembly → (system_prompt, messages). Inspect
  assembled_context_text(user_message, include_data) — Flat string of ALL context visible to the model (system + all tur
  chat(user_message, include_data) — Claude requests the needed data itself via tool use.
  chat_with_image(caption, image_bytes, mime_type) — Analyzes an image in the context of the user's health.
  run_arbiter(user_message, assistant_reply, unverified) — Analyzes a user↔assistant pair and saves artifacts.
  judge_service_trouble(user_message, assistant_reply) — Hypothesis judge 'system is making the person feel bad' (§17: NOT

```

### GENOME
```

genome_parser.py  # genome_parser.py
  parse_tsv(filepath) — Pure 23andMe TSV parser → list of {rsid, chromosome, position, ge
  import_raw_genome(filepath, force, allow_identity_change) — Reads raw 23andMe TSV, imports into raw_snps.

genome_annotator.py  # genome_annotator.py
  tag_domains_keywords(gene, conditions, significance) — Deterministic keyword mapping of domains. Fast, no API.
  fetch_myvariant_batch(rsids) — Requests annotations for a batch of rsIDs via myvariant.info POST
  parse_clinvar(item) — Extracts clinically significant fields from a myvariant response.
  extract_ref_alts(hits) — ClinVar-first extraction of (ref, alts) for ONE rsid from myvaria
  annotate_all(limit_rsids) — Main function: takes all rsIDs from raw_snps,
  annotate_rsids(rsids) — Annotates a specific list of rsIDs (for ad-hoc requests from /gen

genome_context.py
  build_genetic_context_block(domain, max_variants) — Returns a text block of clinically significant variants.
  answer_trait_question(question) — Answers a question about predisposition.
  build_lifestyle_genome_block(domain, max_variants, only_genes) — Compact genomic block for lifestyle agents.

genome_update_agent.py
  fetch_clinvar_updates(variants) — Re-checks a list of variants via myvariant.info.
  generate_genome_narrative(changes, genotype_map) — GP generates a narrative on significant changes.
  run_monthly_update() — Main function of the monthly update.

```

### GP AND SPECIALISTS
```

gp_agent.py
  run_specialists_and_save(end_date, period_days) — Runs MDT specialists and saves the result to agent_reports.
  generate_weekly_report(end_date, run_mdt) — Weekly GP report.
  generate_monthly_report(end_date) — Monthly GP report — strategic view, 30/90 days.
  compute_step_target(activity_date) — Deterministic step recommendation for the day.
  generate_daily_report(target, gate_sink) — Daily morning report: lifestyle agents → GP synthesis.
  run_experiment_checks(target) — Checks active experiments: if today = start_date + check_day[N],
  generate_attribution_report(experiment_id) — Final attribution report upon experiment completion.
  fmt_row(label, mkey)

gp_context.py  # gp_context.py — assembles text context for GP reports.
  absent_claims_contradicted(report, last_dates, window_days) — Report phrases 'X was not submitted', where X is an analyte with 
  judge_absence_claims(text, window_days) — Absence judge for ANY tract: (hits, ready single-line correction)
  recommendations_without_evidence(text, last_dates, schedule) — Recommendations to retest an analyte whose record is NEWER than t
  annotate_lab_recency(text) — Appends to the task text the date of the last submission of analy

wellally_consult.py
  class ConsultationRound
  class ConsultationSession
  async run_consultation_cycle_async(user_message, session, end_date, period_days) — One cycle of a dialogue consultation.
  async run_mdt_consultation_async(end_date, period_days, specialists, user_question) — Legacy single-shot consultation (for compatibility).
  run_mdt_consultation(end_date, period_days, specialists) — Synchronous wrapper for scripts and cron.
  to_dict()
  from_dict(d)

lifestyle_agents.py  # lifestyle_agents.py — 4 lifestyle specialists for the daily morning report.
  class LifestyleAgent
  class SleepAgent
  class MovementAgent
  class StressAgent
  class EnergyAgent
  run_lifestyle_agents(sleep_date, activity_date) — Runs all lifestyle agents.
  has_data(day)
  generate_brief(day, stats7, stats30, target)
  get_mdt_data_brief(sleep_date, activity_date) — Returns a text brief for the MDT data package. Uses the existing 
  async generate_mdt_opinion(sleep_date, activity_date, patient_question, round_a_opinions) — MDT participation via Claude API.
  run(sleep_date, activity_date) — Returns the brief text if data is present, None if not.

safety_net.py  # safety_net.py — deterministic safety net.
  check_lab_alerts(reference_date) — Checks the latest lab data against thresholds.
  check_lab_trends(reference_date) — Checks directional trends in lab data.
  check_lifestyle_alerts(target) — Checks lifestyle metrics against absolute and relative thresholds
  person_urgent_message(urgent, max_level, data_doubt) — Urgent message to the person: heading, metric lines, what to do.
  run_safety_net(target) — Runs all checks.
  cannot_judge_open(conn, today) — Open 'nothing to judge' items for the tenant by its connection (r

```

### TASKS AND CHECK-INS
```

task_agent.py
  extract_tasks_from_report(report_text, source, source_date, report_date) — Parses a GP report, extracts tasks, saves to DB.
  format_tasks_message(tasks) — Formats a task list for Telegram.
  format_open_tasks_message(tasks) — Formats a list of open tasks (for weekly follow-up).
  resolve_task_by_number(task_list, number, resolved_text) — Closes a task by its index number in the list.
  process_gp_report(report_text, report_type, report_date) — Full cycle: extract tasks → create reminders → format brief messa
  summary_tasks(tasks)
  get_weekly_followup() — For weekly follow-up on Sunday:
  create_macos_reminder(task) — Creates a task in macOS Reminders (Health list).
  create_reminders_for_tasks(tasks) — Creates reminders for a list of tasks. Returns the number created
  record_answer(task_id, answer_text, source) — Accept an answer to a question-task: close the task and deliver t
  addressed_to_patient(items) — Unified judge 'this is a question TO THE PERSON': {id: {verdict, 
  promote_memory_questions(limit) — Promotes questions from memory into tasks that are actually asked
  should_ask_again(fingerprint) — Whether to ask a question again if it has already been answered.
  complete_macos_reminder(task_id) — Marks a reminder in macOS as completed by task_id in the note bod
  esc(s)

checkin_agent.py
  generate_opening_question() — Generates the first evening check-in question taking the day's co
  continue_checkin(conversation) — Continues a check-in conversation.
  finalize_checkin(conversation) — Extracts structured data from a completed conversation.
  class CheckinState
  start(opening_question)
  add_user(text)
  add_assistant(text)
  should_force_end()
  is_stale(ttl_hours) — True if a check-in has stalled without completion for longer than
  reset()

reminders_sync.py  # reminders_sync.py — macOS Reminders → SQLite synchronization.
  reminders_list_name() — Owner keeps Health; other tenants use their data-directory suffix
  get_completed_task_ids() — Reads completed reminders from the current tenant's list via Appl
  sync_completed_reminders()

```

### DATA IMPORT
```

import_oura.py  # Oura Ring data import via API v2.
  get_token()
  oura_get(endpoint, start, end) — W5K-A2 (2026-05-14): follow next_token pagination to the end.
  parse_sleep(sessions, scores) — Returns dict[date_str → sleep_summary].
  parse_readiness(data)
  parse_activity(data)
  parse_spo2(data) — daily_spo2: spo2_percentage.average (number %), breathing_disturb
  parse_hrv(sessions) — HRV from night sessions — take average_hrv from long_sleep.
  parse_stress(data) — daily_stress: day_summary, stress_high (sec), recovery_high (sec)
  parse_resilience(data) — daily_resilience: level + contributors.
  parse_workouts(data) — workout: list of workouts with date.
  load_day(d)
  save_day(d, data)
  import_oura(start, end)

import_apple_health.py  # Health Auto Export JSON importer.
  historical_export() — The largest complete HealthAutoExport dump in the installation's 
  parse_hae_date(date_str) — Parse HAE date string '2026-01-01 08:00:00 +0000' → date object.
  aggregate_metric_by_day(metric_name, entries, daily) — Aggregate metric entries into daily buckets.
  process_hae_json(filepath) — Parse a Health Auto Export JSON file.
  save_daily_summaries(daily, mode) — Save daily summaries to HEALTH_DATA/YYYY-MM-DD.json.
  import_historical() — Import the historical JSON export.
  import_daily_new_automation(archive) — Import new daily JSON files from HAE New Automation folder.
  print_summary() — Print recent daily summaries to verify import.

import_hr_activity.py  # Imports heart_rate and active_energy from HealthAutoExport JSON into daily_metri
  load_metric(metrics, name)
  aggregate_by_day(data_points, value_keys) — Aggregates data points by date (average), tries different keys fo
  run()

import_medical_docs.py  # Imports clinical data from read PDFs into health.db:
  save_consultations() — Saves historical consultations to the medical record (events + en
  run()

import_all.py  # WellAlly-Health: full recursive import of the entire health/ folder.
  is_financial(path)
  pdf_to_images(pdf_path)
  ocr_image(img_path)
  ocr_pdf(pdf_path)
  extract_text(pdf_path) — Extracts text from PDF. For digital PDF uses fitz (fast, accurate
  classify(path, text)
  parse_date(text, filename) — Document date — from reliable signal to best-guess fallback:
  parse_lab_values(text)
  is_synevo_format(text) — Detects multi-line Synevo format by Georgian characters in the te
  parse_synevo_values(pdf_path) — Synevo-PDF parser via fitz blocks.
  save(path, data)
  make_record(pdf_path, text, date, doc_type)
  consult_conclusion(text, limit) — Render the intake document for the medical record: starting from 
  save_clinical_to_db(doc_type, date, pdf_path, rec) — Saves clinically significant appointment documents to the medical
  apply_confirmed_type(source_file, doc_type) — Applies confirmed document type from Telegram doc-review.
  main()

calendar_client.py  # calendar_client.py — reads events from Google Calendar JSON cache.
  is_birthday(e)
  format_calendar_context(days) — Formatted event block for AI context.
  get_travel_events(days)
  parse_events(raw)

```

### AUTOMATED DOCUMENTATION
```

doc_agent.py  # doc_agent.py — auto-documentation of changes in Larry Health OS.
  notify_telegram(text) — Compatible input for info entries: one row in the Monday digest.
  get_last_commit_diff(max_lines) — (commit_message, diff_text) for the last commit.
  thread_changelog_row(slug, subjects, day, version) — Log table row per thread. None — no code work occurred in the thr
  bump_version(version) — 15.28 → 15.29: last-digit step, as in the journal since July.
  record_thread_merge(merge_sha, slug, root) — Write a log row for a merged thread and stage the log. Returns th
  get_current_version()
  get_changelog_tail(n) — Last N rows of the CHANGELOG.md table for context.
  get_last_sec_number()
  analyze_diff(commit_msg, diff, description) — Calls Claude and retrieves a structured description of changes.
  apply_changelog_entry(entry, new_version, dry_run)
  apply_security_entry(entry, dry_run)
  install_hook()
  regenerate_intent_page(entry_id, dry_run) — Regenerates docs/explanation/<id>.md from a registry entry. G2 (s
  translate_intent_page(entry_id, dry_run, notify) — Writes <page>.en.md alongside the Russian warm page. Changes the 
  translate_page(page) — General translation path: form and stamp unchanged; intent status
  regenerate_install_page(page, dry_run) — Fixes only install_facts contradictions; validates the response B
  translate_all_pages(dry_run) — Translates warm pages that have no translation or an outdated one
  regenerate_stale_pages(dry_run) — Regenerates all pages with stale provenance (registry has moved a
  main()
  extract(tag)

run_checks.sh

integrity_tests.py  # integrity_tests.py — integrity checks for Larry Health OS.
  check(label, fn, critical, repo_only) — repo_only (docker-install, stage 6): subject of check — REPOSITOR
  warn(label, detail)
  fail_(label, detail) — Direct FAIL without wrapping in check().
  check_data_freshness()
  check_db_integrity() — PRAGMA integrity_check per-tenant — B-tree corruption and out-of-
  check_data_window()
  check_steps_visible_via_get_day() — Steps consistency: if steps exist in flat daily_metrics.steps —
  check_core_metrics()
  check_stats_non_empty()
  check_gp_reports()
  check_report_content()
  check_problem_list()
  check_problem_plain_summary() — An open issue carries a plain-language description (27.09, wave 4
  check_protocols()
  check_tasks_pipeline()
  check_recent_task_activity()
  check_question_answer_integrity() — A question closed without an answer; an undelivered question; an 
  check_question_queue_drains() — The question queue MUST FLUSH within the deadline it sets for its
  check_proposals_delivered() — A GP suggestion on the problem list is awaiting a human decision 
  check_question_candidates_not_discarded() — A candidate fell outside the window without EVER reaching the jud
  check_llm_answer_parsed() — A model response that could not be parsed AT ALL must be visible.
  check_reco_repeats_fresh_lab() — A text advises running a test that has already been run within th
  check_absence_surface_guarded(root) — A tract with a SURFACE for an absence claim is either guarded or 
  check_question_answers_reach_doctor() — Receipt at the consumption point: a fresh response IS VISIBLE in 
  watched_missing_from_review(rows, ctx) — Observation-status issues not present as a line in THEIR OWN cont
  check_watched_problems_reach_review(rows, ctx) — Every issue with monitoring status is visible in the GP weekly re
  check_build_context()
  check_lab_freshness() — Results are no older than LAB_ALERT_DAYS (9 months = alert, 6 mon
  check_lab_canon_health() — Canon lab_results: no physiologically impossible values.
  check_lab_single_writer() — Single-writer invariant of the canon (BL-LAB-CANON-1, Primary-Bas
  promotion_backlog(canon_rows, staging_rows, today_) — Pure congestion sensor logic: how many rows are pending promotion
  check_promotion_backlog_stale() — The recognition pipeline must not silently block at the OUTPUT.
  disjoint_reference_ranges(rows) — Pure: keys (name, unit, material) under which NON-OVERLAPPING ent
  check_loinc_decisions_possible() — A recorded decision must not reference a code impossible for this
  check_lab_names_not_glued() — Different analytes sharing the same name silently corrupt the tre
  unitless_offenders(rows, fence) — Pure sensor logic: canon rows YOUNGER than the fence where a numb
  ref_scale_mismatch(rows) — Pure logic: a value is in one scale while its reference is in ano
  check_lab_ref_scale() — The reference must be in the same scale as the value.
  check_canon_rows_have_a_result() — The canon contains no rows without a result — neither numeric nor
  multidate_offenders(rows) — Pure part: [(source, name, material, date)] → keys with more than
  check_canon_one_date_per_analyte_in_doc() — Within ONE document, one analyte in one material cannot have two 
  check_lab_unit_present() — A number without a scale is unsuitable for clinical inference — y
  specimen_provenance_offenders(rows) — Pure part of the sensor (like `unitless_offenders` and `ref_scale
  check_staging_specimen_provenance() — A material WITHOUT its own stage is indistinguishable from an ass
  censored_value_offenders(rows) — Pure part: (value, value_op) → violations. An operator without a 
  check_censored_values_coherent() — A comparison operator and a number exist ONLY together.
  check_reference_tables_not_in_canon() — The LOINC reference has ONE home — the shared `loinc.db`. A copy 
  check_specialized_lab_health() — specialized_lab_results (BL-LAB-CANON-2): integrity of specialize
  specialized_canon_waiting(rows, canon_keys) — Canon-pending rows of the special layer by NAME+MATERIAL+DIMENSIO
  check_specialized_canon_waiting() — The spec-layer is the canon's waiting room: a row with verdict ho
  canon_naming_debt(rows, canonicals) — Names of canon rows from DOCUMENTS that the canon does not know. 
  check_canon_naming_debt() — Canon's nominal debt does not grow: a row from a document lives u
  check_unrepeated_draw_not_swamping() — The historical block does not outgrow the current laboratory pict
  literature_partner_gate(approved, partner_plist_exists) — Pure suppressor logic: whether it is time to return to the partne
  check_literature_partner_gate() — The deferred owner decision is returned on EVENT, not on schedule
  electrophoresis_offenders(rows, tol) — Pure logic: protein electrophoresis must converge with itself.
  check_electrophoresis_sums() — Electrophoresis converges with itself (see `electrophoresis_offen
  check_promote_conflicts() — Measurements that promote REFUSES to place in canon must be visib
  check_lab_no_billing_rows() — Canon lab_results does NOT contain billing garbage from invoices 
  check_pgs_reference() — Reference DB of polygenic weights (pgs_catalog+pgs_weights) is ex
  check_genome_update() — genome_update_agent is not older than GENOME_UPDATE_ALERT_DAYS.
  check_daemons_alive() — KeepAlive daemons must have a live PID. Covers the class 'service
  check_stderr_watch_blindness() — Whether the stderr sensor itself is blind. MUST be placed BEFORE 
  check_stderr_errors() — Errors APPENDED to stderr logs of jobs since the last check.
  check_deploy_restart_completeness() — Ratchet of restart completeness: set of long-lived jobs == set th
  check_tenant_count_vs_silence_decision() — Counter under the decision 'human silence is not treated as a sig
  check_plist_env_consistency() — WARN: owner job has HEALTH_DATA_DIR in plist but WITHOUT it in th
  check_launchd_inventory() — WARN: plist copy in repository has diverged from live, or jobs wi
  check_arbiter_liveness() — WARN: memory extractor (arbiter) has fallen behind FRESH messages
  check_reschedule_liveness() — WARN: 12h-job for recalculating the brief's local timezone (resch
  check_consolidation_delivery_liveness() — WARN: delivering job disagree/supersede (run_nightly_consolidatio
  check_promote_questions_liveness() — WARN: the candidate-to-question elevator may have silently died.
  check_watchdog_liveness() — WARN: §14 terminus — uncommitted_watchdog itself may have silentl
  check_macbook_uncommitted() — UNCOMMITTED WORK ON MACBOOK — judged from here, from snapshot, no
  check_macbook_head_deployed() — CODE FROM MacBook HAS NOT REACHED STUDIO — and nobody reported it
  threads_in_snapshot(threads_field) — Parsing the `threads=` field of a MacBook snapshot — ONE format h
  thread_copies_missing(threads_field, have, now_ts, grace_h) — Pure/testable: which thread branches older than grace_h have NOT 
  check_thread_work_has_a_second_copy() — THREAD WORK IN A SINGLE INSTANCE — question for the AFFECTED part
  threads_without_index_row(threads_field, index_text, now_ts, grace_h) — Pure/testable: threads whose branch has been alive longer than gr
  check_threads_have_index_row() — ORPHAN THREAD: branch is alive but absent from the thread registr
  check_captured_files() — CAPTURING ANOTHER'S FILE INTO A COMMIT — question to the person, 
  check_git_hooks_executable() — An active hook without the execute bit = ALL gates disabled at on
  check_dispgate_liveness() — §14 for the idempotency gate (§15): it may have stopped protectin
  check_intentgate_liveness() — §14 for the intent-receipt gate (thread intent-receipts): two way
  check_disposability_causes() — §15/§13: the same negative-verdict reason surfacing across N DIFF
  check_memory_facts_growth() — WARN (F4.3 tripwire, plan "PHASE 4 REBUILD"): active EPISODIC
  check_memory_facts_invariants() — FAIL: memory_facts invariants (E, debt from 5 July audit).
  check_temporal_class_coverage() — WARN: active state with temporal_class IS NULL — write path bypas
  check_clinical_kb_populated() — FAIL: clinical_kb IS EMPTY → medical_frame degenerates into a sta
  check_clinical_kb_replica_fresh(conn) — FAIL: clinical_kb in the tenant DB HAS DIVERGED from the git sour
  check_frozen_relative_not_current() — WARN: active state with «today/yesterday» in TEXT but NOT classif
  check_db_row_regression() — Table dropped >50% against the peak of recent backups → silent da
  check_visual_orphans() — visual-intake integrity: photo without case / case without photo 
  check_stale_visual_cases() — Stuck elicitation dialog: case open >72h with no activity (livene
  check_visual_verdict_rate() — LAGGING elicitation quality signal: share of symptom_intake hypot
  check_symptom_prompt_discipline() — Safe-pose guard: elicitation prompt has not lost the reassurance 
  check_lab_history_regression() — FAIL (health-critical): lab_results shrank sharply vs last backup
  check_effect_allele_coverage() — strand-resolution coverage of effect_allele (genome strand-fix F7
  check_carrier_status_allele_coupling() — Coupling carrier-status ⟹ effect_allele NOT NULL (invariant
  check_cbcr_methodology_present() — CBCR methodology load-bearing (manifest = system prompt for hypot
  check_validation_gate_methodology_present(root) — Validation gate methodology load-bearing (spec = subsystem intent
  check_lab_path_scope(db_path, base) — Auto-revert mechanism for lab path upon sufficient coverage.
  check_fdr_qualification_drift(db_path, base) — M1 wrapper: reads canon (read-only) + manifest core, computes dri
  check_weekly_digest_delivered() — weekly-digest thread (2026-09-05). Monday: the digest file for th
  check_tenant_dbs_reachable() — Meta-sensor against SILENT TRUNCATION (RST, partner-integrity 202
  check_canon_normal_flag_matches_reference() — «Norm» does not contradict the printed reference. Both tenants (w
  check_partner_epochs_ready() — SIBLING-tenant readiness for per-tenant A/D stratification.
  check_family_names_resolve(db_path, base) — Every declared validation gate family name must resolve to LIVE d
  check_threshold_names_reach_data(db_path) — Every lab threshold must MEET data: name = canon lab_canon.normal
  check_norm_review_overdue() — Active norm with a past next_review — red for the owner. This is 
  check_norm_vs_lab_reference() — External witness of the norm. Red — only for norm_kind='reference
  check_norm_coverage() — Norm coverage: analyte with ≥norm.coverage_min_n measurements and
  check_norm_kind_unclassified() — Remainder without a norm_kind verdict — WARN counter for the owne
  check_norm_documents_fresh() — A norm is a replica of an external document; the sole axis of con
  check_schedule_vs_guideline() — Physician assignment (lab_monitoring_schedule, source encounter:*
  check_threshold_source_is_document() — Invariant threshold_derived_from_document (registry norm_from_doc
  check_hae_arrivals_have_owner() — WARN: the metric that the device actually sends has no owner (own
  check_hae_raw_archive_compressed() — FAIL: HAE raw archive contains uncompressed exports older than 15
  scan_hollow_constitutions(db_paths) — Pure function: for each DB returns a flag if it has
  check_constitutions_not_hollow() — FAIL if constitutions are generated on EMPTY effect_allele (hollo
  check_constitutions_trigger_alive() — Pulse of constitution rebuild on new inputs (owner decision 2026-
  check_safety_net_can_judge() — "Nothing to judge by" at the fuse — system debt to engineering qu
  check_public_genotype_profile() — Tenant genotype profile in public zone (2026-09-25, thread genoty
  check_single_canonical_db() — R1/R2 (split-brain prevention, 2026-06-18): exactly one health.db
  scan_cross_tenant(cur_db_path, sibling_paths, cand) — Finds identical Oura fingerprints between DB cur and siblings.
  check_cross_tenant_contamination() — Belt-and-suspenders for fail-closed secrets_dir() (2026-07-03).
  check_one_document_one_event() — Document parsing produces exactly one event per file (thread inta
  check_longitudinal_freshness() — longitudinal_analysis runs on schedule `com.larry.health.longitud
  check_longitudinal_gate_applied() — A fresh longitudinal summary must have the statistical gate appli
  check_gate_run_receipt(artifact, belief_day) — Gate failure is an event with a reader, not "the row is simply ab
  check_belief_fresh_vs_data(conn) — Belief is a CACHE computed from `daily_metrics`. The cache had no
  check_sleep_stage_provenance(auto_repair) — Fake sleep stages must not exist — and if they appear, they are f
  check_sleep_device_mixing() — Sleep from one device, HRV from another — in one belief row. Ratc
  check_quarantine_stuck(conn) — Quarantine without verdict longer than QUARANTINE_STUCK_DAYS is a
  check_mc_gap(artifact) — MC gap: every passing BY-verdict fell within ±2·MCSE of its selec
  check_passset_flicker(artifact) — pass-set flicker: membership of passing pairs changed between two
  check_passset_history_depth(artifact) — Snapshot history is shorter than the calendar → it was trimmed or
  check_gate_artifacts_liveness(receipt, artifacts) — After a SUCCESSFUL gate run, mandatory artifacts must exist and b
  check_triage_delivery_liveness(base, now) — WHETHER THE WARN DELIVERY RAIL ITSELF IS ALIVE (integrity → triag
  check_tenant_triage_alive(base) — TENANT-HUMAN morning review is alive (28.09, owner decision: 'par
  check_gp_schedule() — GP monthly ≤35d.
  check_review_dates() — Issues and protocols with an expired review_date.
  check_periods_expiry() — Clinical periods: watchful_waiting phases with an expired date an
  treatment_date_disagreements(periods, problems, tol_days) — PURE function (testable without DB). periods: dict(name,type,star
  check_treatment_dates_agree() — Therapy dates in `periods` (primary) vs `problem_list` (copies in
  check_treatment_history_extracted() — Treatment is derived from documents (medications), not a hand-wri
  check_no_hardcoded_diagnosis() — FAIL: a removed onco-literal has returned to a prompt-builder (th
  check_doc_classifier_seeded() — WARN: tenant_doc_patterns.yaml exists but its patterns are NOT in
  check_cpic_canon_consistent() — FAIL: CPIC canon (3 projections) is not seeded or is inconsistent
  check_pubmed_egress_neutral() — FAIL: egress-guard lets raw diagnosis specifics through to PubMed
  check_labs_freshness_per_tenant() — FAIL: oncomarkers are in the NEUTRAL lab-freshness base → imposed
  check_doc_structure() — Checks that key doc-files exist after Diataxis restructuring.
  check_oura_column_completeness() — W5K-#169 (2026-05-14): freshness of Oura columns in daily_metrics
  check_translations_fresh() — The English translation has fallen behind the Russian original (2
  check_assessments_freshness() — Each tool — last fill is no older than cadence_days + 14.
  check_pro_score_unit_single(db_path) — A PRO score under one name is stored in EXACTLY ONE dimension (§1
  check_literature_freshness() — The latest literature_search covers the latest scheduled run agai
  check_literature_freshness_partner() — Partner literature search covers its latest scheduled run (owner 
  check_survivorship_agent_freshness() — The last survivorship_analysis is no older than 18 days.
  check_pending_proposals_ageing() — problem_list_proposals with status='pending' older than 21 days.
  check_ecg_nonsinus() — ECG records with alarming rhythm (AFib / High HR) in the last 48 
  check_open_hypotheses_ageing() — Hypotheses with status='open' older than 60 days without transiti
  check_unresolved_evaluations() — HV-6: hypotheses in testing, lab data present, outcome absent > 7
  check_recommendation_engine_health() — evaluate_domain_need must not silently fail on the error-path.
  check_constitution_conflicts_unresolved() — memory(category='constitution_conflict') with status='open' older
  check_pharmaco_genome_sync() — Pharmacogenomics is synchronized with the last genome import.
  check_traits_genome_sync() — Deterministic traits (Phase F) synchronized with the latest genom
  check_wellness_genome_sync() — Wellness genomics (Phase G) synchronized with the latest genome i
  check_prs_genome_sync() — PRS (Wave 4, Phase H) synchronized with the latest genome import.
  scan_backup_staleness(db_paths, now) — Clean/testable: daily backup freshness per tenant.
  check_backup_freshness() — The most recent daily backup of EVERY tenant covers the last sche
  check_db_size()
  check_logs_all_listed() — Rotation blind spot: log is GROWING but is absent from the rotati
  check_fault_journal() — Owner decision 2026-09-28: failures go into the nightly cycle, no
  check_logrotate_liveness() — Rotator is alive AND completed cleanly (§14, producer_registry fi
  check_secret_scope_matches_reality() — Declared secret class vs. what actually resides in the directorie
  check_llm_tracts_guarded() — The number of LLM pipelines WITHOUT secret_guard does not grow (§
  check_llm_direct_constructors(root) — Anthropic client is constructed ONLY in llm_client — the secret g
  check_llm_guard_blocks_reported() — Guard blocks are not silent: the daily counter is visible to a hu
  collect_ungrounded(window_days) — Findings '`r=0.X` not present in ACCEPTED belief'
  check_correlations_grounded() — UC-B-09: every `r=0.X` in recent agent reports is confirmed by an
  check_ungrounded_corr_ratchet() — JOURNAL READER — reads CONTENT, not length.
  check_correlation_producers_declared(root, declared) — WARN: WHO inside the perimeter computes correlation — is declared
  check_heldout_ready() — validation-gate-repair thread suppressor (2026-08-08): revive hel
  check_security_sensors() — SECURITY.md 'Quarterly checklist' → code (security_sensors.py).
  check_changelog_freshness(changelog) — BL-DOCAGENT-DEAD-1: every merged thread with code work has a chan
  check_arch_map_fresh(root) — Project map (ARCH_SNAPSHOT ru/en) rebuilt after the last thread c
  check_consilium_freshness() — monthly_consilium writes agent_report('monthly_consilium'). Silen
  scan_calendar_staleness(db_paths, current_path, error_age_h) — Clean/testable: findings about calendar cache per tenant.
  check_calendar_freshness() — calendar_cache.json feeds the GP context; fetcher runs hourly. Si
  check_tenant_biometrics_freshness() — Biometrics are fresh for EVERY tenant, not only for the owner.
  check_producer_registry() — context_gate_orphan: every scheduled producer is covered by a sen
  check_lab_intake_pulse() — §14 for the entry watcher: proves the LOOP is spinning, not merel
  blind_spot_ages(candidates, seen, decided, watermark) — Pure sensor core for blindness: (name, mtime) → ages of stuck ent
  check_lab_intake_blind_spot() — Second half of the predicate (§17): pulse is present but no work 
  check_lab_review_queue_movement(paths) — Human queue is moving. Non-empty queue without movement = lost
  check_staging_status_coverage(paths) — RATCHET: no staging row sits in a status that nobody watches.
  check_glossary_targets_known() — The target of a confirmed alias must be a KNOWN canonical name.
  check_unit_conversion_coverage(conn) — WARN burn-in (plan_consilium_under_manifest 2026-07-08 Phase 1): 
  check_urine_name_convention(conn) — Urine domain naming convention is Urine_* (owner decision 2026-08
  check_morning_brief_gate_liveness() — Anti-repeat gate is alive: context_cards is being populated. If t
  retell_score(first_sentence, owner_messages) — PURE fraction of words in the first sentence of the brief that ca
  check_brief_does_not_retell_owner() — WARN: the morning brief opens by retelling what the owner said to
  check_profile_reconciler_fresh() — WARN: patient_profile has diverged from the live source — reconci
  check_time_contract() — Unified time contract: production code reads the clock via _time_
  check_probe_liveness() — §14 for the SAMPLE CARRIER: the sensor on which registry promises
  check_suite_freshness() — §14 for the MOST LOADED sensor in the project — the test suite.
  check_nightly_suite_liveness(rows, now, la_dir, host) — §14 for the NIGHTLY test task (`com.larry.health.test-suite`, 00:
  check_night_cycle_liveness() — §14 for the nightly cycle engine: a silently dead engine means ni
  check_doorbell_liveness() — §14 for the BELL, behavioral: silence = launchd owner_nag is dead
  check_domain_boundary() — Boundary between the two lab-data houses is enforced (work B, 202
  check_lab_class_verdicts_complete() — Every class that ACTUALLY exists in the data has a human verdict.
  epoch_of(ts)

check_contracts.py  # check_contracts.py — inter-module integrity check.
  check(label, condition, detail)
  warn(label, detail)

smoke_tests.py  # Phase 0.5 — Smoke tests for Health OS.
  check(name, fn)
  test_save_retire_protocol()
  must_exist(p)
  test_update_exp()

gen_blueprint.py  # gen_blueprint.py — auto-update of the MODULE REGISTRY in ARCH_SNAPSHOT.md.
  class DescriptionText
  blueprint_descriptions() — Full Russian first lines, before the 80/65-character rendering li
  update_arch_snapshot(lang) — Overwrites the MODULE REGISTRY section in ARCH_SNAPSHOT.md betwee
  main(argv)
  text(source) — Collect and translate the full source, then apply the rendering l
  cell(source) — Escape table separators after lookup, without changing the memory

```

### INTERFACE
```

telegram_bot.py  # telegram_bot.py — thin entry-point for launchd plist com.larry.health.bot.

```

<!-- GEN:MODULE_REGISTRY:END -->

<!-- GEN:GRAPH:START -->
<!-- Generated by arch_guard.py; descriptions are machine-translated from the Russian source. -->
## DEPENDENCY GRAPH

```
integrity_tests              → _time_inject, agent_reports_db, assessment_importer, assessment_scheduler, belief_contract, cbcr_lookup, clinical_kb, config_db, correlation_gate, cpic_reference_db, daemon_liveness, db_regression, diagnosis_guard, doc_translation, ecg_db, finding_identity, generate_constitutions, genome_db, git_facts, google_calendar_fetcher, gp_context, hae_checker, health_db, heldout_readiness, i18n, import_medical_events, infra_config, intent_registry, lab_canon, lab_intake_watcher, lab_oracles, lab_promote, labs_db, log_rotate, loinc_match, memory_facts_db, metrics_db, morning_test_summary, norm_documents, owner_weekly, parked_decisions, pgs_reference, pii_census, plist_env_liveness, producer_registry, profile_reconciler, proposals_db, pubmed_client, quarantine_db, safety_net, secrets_paths, security_sensors, signal_family, stderr_watch, time_contract_sensor, triage_agent, visual_db, weekly_digest
health_db                    → _time_inject, agent_reports_db, alerts_db, assessments_db, checkins_db, clinical_kb, config_db, constitutions_db, consult_sessions_db, consultations_db, cpic_reference_db, doc_reviews_db, events_db, experiments_db, field_reviews_db, food_floor, generated_food_rules, genome_db, hae_db, hai_context, hypotheses_db, i18n, import_status_db, infra_config, lab_canon, labs_db, memory_db, metrics_db, norm_documents, periods_db, problems_db, profile_db, proposals_db, protocols_db, quarantine_db, rules_db, tasks_db, treatment_db, workouts_db
gp_agent                     → _fmt_helpers, _time_inject, beliefs, brief_pipeline, brief_state, brief_validator, config_db, env_context, food_rule_review, genome_context, gp_context, hai_core, hai_hypotheses, health_ai, health_db, i18n, labs_db, lifestyle_agents, llm_client, location_signal, patient_context, promethease_context, safety_net, wellally_consult
monthly_consilium            → _time_inject, cbcr_hypothesis, consilium_roster, food_rule_generator, generated_food_rules, genome_context, gp_agent, gp_context, hai_analysis, hai_core, hai_hypotheses, health_db, hypothesis_semantic_check, i18n, llm_client, notify, patient_context, pharmaco_context, prs_context, traits_context, wellness_context
task_agent                   → _fmt_helpers, _time_inject, config_db, hai_core, health_db, i18n, lab_canon, labs_db, llm_client, location_signal, memory_facts_db, reminders_backend, reminders_sync, secrets_paths
assessment_dialog            → _fmt_helpers, _time_inject, assessment_importer, assessment_scheduler, config_db, genome_intake, health_db, i18n, notify, problems_db, profile_db, secrets_paths, treatment_db
consult_prep                 → _time_inject, config_db, genome_context, hai_core, hai_hypotheses, health_db, i18n, infra_config, labs_db, llm_client, notify, patient_context, treatment_summary
generate_constitutions       → _time_inject, belief_contract, config_db, correlation_gate, doc_translation, hai_core, health_db, i18n, infra_config, labs_db, llm_client, profile_reconciler, secrets_paths
gp_context                   → _fmt_helpers, _time_inject, belief_contract, clinical_kb, correlation_gate, ecg_db, genome_context, health_db, i18n, lab_canon, labs_db, lifestyle_agents, patient_context
brief_pipeline               → _time_inject, brief_cards, brief_gate, calendar_client, env_context, food_profile, genome_context, health_ai, health_db, location_signal, safety_net, trails
import_medical_events        → config_db, hai_core, health_db, i18n, infra_config, lab_schedule_extractor, link_fetch, llm_client, notify, problems_db, treatment_db, treatment_extractor
lab_intake_watcher           → _time_inject, daemon_liveness, genome_intake, health_db, i18n, import_all, import_medical_events, infra_config, lab_backfill, link_fetch, notify, plist_env_liveness
longitudinal_analysis        → _time_inject, belief_contract, correlation_gate, git_facts, health_db, i18n, lab_canon, metrics_db, notify, quarantine_db, secrets_paths, signal_family
night_cycle                  → _fmt_helpers, _time_inject, agent_reports_db, finding_identity, i18n, morning_test_summary, night_investigator, notify, owner_gate, owner_nag, parked_decisions, weekly_digest
hai_context                  → _fmt_helpers, _time_inject, calendar_client, genome_context, gp_context, health_db, lab_canon, labs_db, lifestyle_agents, metrics_db, patient_context
hypothesis_consilium_eval    → _time_inject, consilium_roster, hai_core, health_db, i18n, lab_canon, labs_db, lifestyle_agents, llm_client, patient_context, wellally_consult
wellally_consult             → _fmt_helpers, _time_inject, consilium_roster, genome_context, hai_core, health_db, labs_db, lifestyle_agents, llm_client, patient_context, treatment_summary
doc_agent                    → _time_inject, doc_translation, git_facts, hai_core, i18n, intent_registry, llm_client, notify, secret_guard, secrets_paths
food_profile                 → clinical_kb, food_floor, food_genome, food_staples, generated_food_rules, health_db, i18n, repertoire, taste
food_rule_generator          → _time_inject, clinical_kb, food_floor, food_genome, food_profile, food_staples, generated_food_rules, hai_core, llm_client
hai_reports                  → _fmt_helpers, _time_inject, clinical_kb, gp_context, hai_core, health_db, infra_config, pubmed_client, wellally_consult
patient_context              → _time_inject, beliefs, health_db, labs_db, memory_config, memory_facts_db, memory_salience, problems_db, treatment_summary
safety_net                   → _time_inject, config_db, health_db, i18n, lab_canon, labs_db, norm_documents, notify, rules_db
weekly_digest                → _time_inject, config_db, diagnosis_guard, hai_core, i18n, llm_client, notify, pii_census, secrets_paths
genome_pipeline              → backfill_effect_alleles, fix_palindromic_het, generate_constitutions, genome_annotator, genome_parser, health_db, profile_reconciler, prs_pipeline
hai_chat                     → _time_inject, config_db, gp_context, hai_context, hai_core, hai_reports, health_db, memory_facts_db
hai_hypotheses               → _time_inject, cbcr_hypothesis, gp_context, hai_core, health_db, i18n, patient_context, secrets_paths
checkin_agent                → _time_inject, gp_context, hai_core, health_db, llm_client, patient_context, region_pack
genome_intake                → config_db, genome_pipeline, health_db, i18n, infra_config, link_fetch, notify
hai_core                     → _time_inject, calendar_client, health_db, i18n, llm_client, patient_context, region_pack
import_all                   → _time_inject, config_db, health_db, hypothesis_lab_linker, import_coordinator, infra_config, lab_intake_watcher
lab_backfill                 → _time_inject, health_db, infra_config, lab_oracles, lab_recognizer, lab_specimen, labs_db
lifestyle_agents             → consilium_roster, genome_context, gp_context, hai_core, health_db, patient_context, promethease_context
literature_curator           → _time_inject, cbcr_hypothesis, hai_core, hai_hypotheses, health_db, hypothesis_semantic_check, i18n
owner_nag                    → _fmt_helpers, _time_inject, i18n, notify, parked_decisions, plist_env_liveness, region_pack
owner_weekly                 → _time_inject, i18n, night_cycle, notify, owner_nag, parked_decisions, triage_agent
survivorship_curator         → _time_inject, cbcr_hypothesis, hai_core, hai_hypotheses, health_db, hypothesis_semantic_check, i18n
test_failure_handler         → _time_inject, doc_translation, hai_core, i18n, infra_config, llm_client, notify
triage_agent                 → _fmt_helpers, _time_inject, finding_identity, health_db, i18n, notify, task_agent
vcf_import_pipeline          → _time_inject, genome_intake, health_db, pharmaco_pipeline, prs_pipeline, traits_pipeline, wellness_pipeline
assessment_bot_handlers      → _fmt_helpers, _time_inject, assessment_dialog, health_db, i18n, notify
assessment_scheduler         → _fmt_helpers, _time_inject, assessment_importer, health_db, i18n, secrets_paths
food_quarterly               → _time_inject, clinical_kb, food_profile, health_db, i18n, secrets_paths
genome_update_agent          → _fmt_helpers, _time_inject, hai_core, health_db, i18n, llm_client
health_ai                    → hai_analysis, hai_chat, hai_context, hai_core, hai_hypotheses, hai_reports
hypothesis_resolution        → _time_inject, hai_hypotheses, health_db, hypothesis_consilium_eval, i18n, notify
lab_promote                  → _time_inject, config_db, health_db, lab_canon, lab_oracles, labs_db
labs_db                      → _time_inject, clinical_kb, health_db, i18n, lab_canon, safety_net
problems_db                  → _time_inject, api_spend_log, clinical_kb, hai_core, health_db, llm_client
cbcr_hypothesis              → _time_inject, cbcr_lookup, gp_agent, hai_core, llm_client
dashboard                    → daemon_liveness, dashboard_db, dashboard_state, health_db, infra_config
env_context                  → brief_cards, config_db, env_sources, location_signal, region_pack
hae_checker                  → _time_inject, health_db, import_apple_health, infra_config, metrics_db
loinc_match                  → _time_inject, health_db, lab_canon, lab_promote, profile_db
memory_consolidation         → beliefs, hai_core, health_db, i18n, memory_facts_db
morning_test_summary         → _time_inject, health_db, infra_config, plist_env_liveness, test_failure_handler
proposals_db                 → _fmt_helpers, _time_inject, health_db, i18n, problems_db
assessment_importer          → _time_inject, assessment_scheduler, health_db, i18n
food_genome                  → clinical_kb, i18n, region_pack, seasonal_produce
hypothesis_semantic_check    → _time_inject, assessment_scheduler, hai_core, health_db
lab_extractor                → _time_inject, hai_core, infra_config, llm_client
lab_reconcile                → _time_inject, health_db, import_all, lab_backfill
lab_specialized              → health_db, lab_canon, lab_promote, labs_db
location_signal              → _time_inject, config_db, health_db, memory_facts_db
monthly_api_report           → _time_inject, i18n, model_health_check, notify
night_investigator           → hai_core, i18n, llm_client, owner_gate
oura_freshness_check         → health_db, i18n, notify, secrets_paths
pgs_discovery                → genome_weights, health_db, pgs_reference, prs_pipeline
publication_reader           → _time_inject, hai_core, health_db, patient_context
pubmed_client                → _time_inject, clinical_kb, pii_census, rules_db
pubmed_searcher              → _time_inject, clinical_kb, health_db, pubmed_client
reminders_sync               → health_db, notify, reminders_backend, secrets_paths
symptom_intake               → hai_core, hai_hypotheses, i18n, visual_db
backfill_effect_alleles      → effect_allele, genome_annotator, health_db
backfill_hae_coverage        → _time_inject, hae_checker, health_db
belief_contract              → _time_inject, health_db, secrets_paths
check_contracts              → health_db, infra_config, secrets_paths
check_wellally_updates       → _time_inject, i18n, notify
clinical_kb                  → _time_inject, health_db, i18n
constitution_analysis        → _time_inject, health_db, promethease_context
food_rule_review             → cbcr_hypothesis, generated_food_rules, i18n
gen_arch_blocks              → gen_blueprint, health_db, infra_config
genome_context               → hai_core, health_db, llm_client
google_calendar_fetcher      → _time_inject, infra_config, secrets_paths
hai_analysis                 → _time_inject, health_db, metrics_db
import_apple_health          → _time_inject, health_db, infra_config
import_fitdays               → _time_inject, health_db, infra_config
import_oura                  → health_db, infra_config, secrets_paths
import_watchdog              → health_db, import_status_db, notify
lab_drytest                  → hai_core, health_db, infra_config
lab_freshness_twotier        → _time_inject, health_db, lab_canon
lab_review_sheet             → health_db, lab_backfill, lab_recognizer
lab_schedule_extractor       → hai_core, health_db, llm_client
lab_staging_summary          → health_db, lab_canon, labs_db
lab_triage                   → health_db, lab_canon, lab_oracles
link_fetch                   → genome_intake, i18n, notify
memory_facts_db              → beliefs, health_db, memory_salience
model_health_check           → hai_core, i18n, notify
notify                       → _time_inject, i18n, secrets_paths
profile_db                   → _time_inject, health_db, treatment_summary
reminders_backend            → _time_inject, config_db, secrets_paths
smoke_tests                  → gp_agent, health_ai, health_db
survivorship_analyzer        → _time_inject, assessment_scheduler, health_db
treatment_extractor          → hai_core, health_db, llm_client
backfill_critical_flag       → health_db, memory_salience
beliefs                      → health_db, memory_facts_db
brief_cards                  → hai_analysis, metrics_db
brief_state                  → brief_gate, config_db
calendar_client              → _time_inject, secrets_paths
calendar_sync                → _time_inject, health_db
consilium_roster             → _time_inject, health_db
correlation_gate             → lab_canon, signal_family
cpic_reference_db            → health_db, i18n
daemon_liveness              → infra_config, plist_env_liveness
db_regression                → health_db, infra_config
desc_translation             → hai_core, llm_client
doc_intake                   → doc_triage, media_intake
ecg_db                       → _time_inject, health_db
experiments_db               → _time_inject, health_db
field_reviews_db             → health_db, lab_canon
fill_description_ru          → hai_core, health_db
food_floor                   → clinical_kb, health_db
genome_db                    → _time_inject, health_db
genome_parser                → _time_inject, health_db
genome_weights               → _time_inject, pgs_reference
hae_db                       → _time_inject, health_db
import_coordinator           → config_db, health_db
import_hr_activity           → health_db, import_apple_health
lab_recognizer               → hai_core, lab_canon
log_rotate                   → hae_checker, secrets_paths
memory_truthcheck            → health_db, memory_consolidation
metrics_db                   → _time_inject, health_db
night_repair                 → infra_config, secrets_paths
norm_documents               → _time_inject, lab_canon
parked_decisions             → _time_inject, config_db
periods_db                   → _time_inject, health_db
pharmaco_context             → cpic_reference_db, health_db
plist_env_liveness           → infra_config, secrets_paths
profile_reconciler           → _time_inject, health_db
promethease_context          → _time_inject, health_db
protocols_db                 → _time_inject, health_db
prs_pipeline                 → _time_inject, pgs_reference
quarantine_db                → health_db, signal_family
repertoire                   → i18n, taste
security_sensors             → infra_config, plist_env_liveness
service_trouble              → config_db, health_db
tasks_db                     → gp_context, health_db
treatment_summary            → health_db, i18n
trend_alerts                 → clinical_kb, health_db
wellness_pipeline            → i18n, traits_pipeline
agent_reports_db             → health_db
alerts_db                    → health_db
api_spend_log                → _time_inject
assessments_db               → health_db
checkins_db                  → health_db
config_db                    → health_db
constitutions_db             → health_db
consult_sessions_db          → health_db
consultations_db             → health_db
dashboard_db                 → health_db
dashboard_filters            → _time_inject
dashboard_state              → dashboard_filters
dashboard_views              → i18n
diagnosis_guard              → pii_census
doc_reviews_db               → health_db
doc_triage                   → hai_core
env_sources                  → region_pack
events_db                    → health_db
fix_palindromic_het          → health_db
food_staples                 → i18n
gen_blueprint                → desc_translation
gen_key_paths                → infra_config
gen_schedule                 → infra_config
gen_testing_contracts        → gen_blueprint
generate_test                → doc_translation
generated_food_rules         → health_db
genome_annotator             → health_db
heldout_readiness            → i18n
hypotheses_db                → health_db
hypothesis_lab_linker        → health_db
i18n                         → profile_db
import_medical_docs          → health_db
import_status_db             → health_db
lab_backfill_report          → health_db
lab_fuzzy                    → health_db
lab_oracles                  → lab_canon
llm_client                   → secret_guard
loinc_loader                 → health_db
memory_config                → config_db
memory_db                    → health_db
memory_salience              → memory_config
pharmaco_pipeline            → _time_inject
pii_census                   → git_facts
pilot_shadow                 → notify
producer_registry            → plist_env_liveness
propose_uc                   → _time_inject
prs_context                  → health_db
rules_db                     → health_db
seasonal_produce             → region_pack
secret_guard                 → secrets_paths
signal_family                → _time_inject
stderr_watch                 → plist_env_liveness
test_oura                    → secrets_paths
time_contract_sensor         → _time_inject
trails                       → region_pack
traits_context               → health_db
traits_pipeline              → i18n
treatment_db                 → health_db
validate_uc_index            → doc_translation
visual_db                    → health_db
wellness_context             → health_db
workouts_db                  → health_db

-- ALL MODULES → health_db (single data access point)
-- health_db imports no internal modules
```
<!-- GEN:GRAPH:END -->

---

<!-- GEN:KEY_PATHS:START -->

## KEY PATHS
<!-- Generated by gen_key_paths.py; descriptions are machine-translated from the Russian source. Do not edit manually. -->
<!-- Source: actual Studio filesystem + health_db._STUDIO_LOCAL_DIR. -->

### Studio (canonical, via Tailscale; address in private/infra.yaml)

```
~/health/                                                   -- HEALTH_DATA_DIR (canonical after P1-1 hostname-aware)
~/health/data/health.db                                     -- SQLite DB, 27 tables (single-primary, 2026-04-24)
~/health/daily_metrics/                                     -- Daily JSON from import_oura.py ⚠️ missing
~/health/reports/                                           -- Markdown GP reports YYYY-MM-DD.md
~/health/genome/                                            -- raw 23andMe TSV (imported into raw_snps)
~/health/biochemical/                                       -- Laboratory report PDFs
~/health/backups/                                           -- Daily sqlite3 .backup (30-day rotation, com.larry.health.backup)
~/health/logs/                                              -- api.log, bot logs, per-machine
~/health/.claude/specialists/                               -- MDT specialist prompts (oncology.md etc.) ⚠️ missing
~/health_scripts/                                           -- Production code, git canonical after the 2026-05-09 migration
~/health_scripts/scripts/git-hooks/pre-commit               -- Pre-commit hook (P1-4, INV-DOC enforcement)
~/health_scripts/scripts/install_hooks.sh                   -- Hook installation via symlink (Studio-only)
~/health_scripts/scripts/pre_destructive_check.sh           -- Manual guard before reset/checkout/merge
~/health_scripts/doc_inventory.yaml                         -- SSOT for .md classification (LIVE/archive)
~/.health_secrets/anthropic_key                             -- Claude API key
~/.health_secrets/oura_token                                -- Oura Ring API v2 token
~/.health_secrets/telegram_token                            -- Telegram Bot API token
~/.health_secrets/telegram_chat_id                          -- Target chat for reports
~/.health_secrets/sync_token                                -- X-Sync-Token for bot→Studio FastAPI
~/Library/LaunchAgents/com.larry.health.bot.plist           -- Telegram bot (KeepAlive)
~/Library/LaunchAgents/com.larry.health.api.plist           -- FastAPI :8000 (KeepAlive) ⚠️ missing
~/Library/LaunchAgents/com.larry.health.backup.plist        -- Daily sqlite3 backup 03:00
~/Library/LaunchAgents/com.larry.health.test-suite.plist    -- Nightly test pyramid 00:00
~/Library/LaunchAgents/com.larry.health.watcher.plist       -- fswatch → import
~/Library/LaunchAgents/com.larry.health.reminders-sync.plist-- macOS Reminders sync every 3h
```

### MacBook (working copy)

```
~/health_scripts/                                           -- Working copy, full clone: sole writer, commits only here (CLAUDE.md §C)
~/health_scripts/scripts/pre_destructive_check.sh           -- Pre-destructive (rule #4)
~/Library/Mobile Documents/com~apple~CloudDocs/health/      -- Installation cloud folder: read-only replica (NOT canonical, lags behind)
~/.infrastructure.md                                        -- Infrastructure manifest (nodes, ports, Tailscale, FileVault)
~/.health_secrets/                                          -- Local copy of secrets
~/Library/LaunchAgents/com.larry.health.backup.plist        -- 03:00 tree snapshot to refs/backups/daily-<date> → push to Studio; leaves main unchanged (CLAUDE.md §1)
```

<!-- GEN:KEY_PATHS:END -->

---

## KEY PATTERNS AND CONVENTIONS

```
1. Everything via health_db.py — direct sqlite3 access is forbidden (except genome_parser bulk insert)
2. Hypotheses in the memory table (category='hypothesis') as a JSON blob
   Statuses: open → testing → confirmed | rejected
   resolution_type: self_managed|needs_specialist (set by LLM; Telegram notifies on needs_specialist)
3. Problem list statuses: active | active_monitoring | watchful_waiting | resolved
   (NOT 'monitoring' — specifically 'active_monitoring' and 'watchful_waiting')
4. Time zone: data in UTC, reports and schedules in the tenant's local time (<zone>)
5. Studio auth: X-Sync-Token header (NOT Telegram initData — that is only for the Mini App browser)
6. Studio URL: https://<tailnet_hostname> (Tailscale Funnel)
   MacBook → Studio rsync: <studio_ssh>
7. doc_agent does not commit — only stages files; backup.sh commits at 03:00
8. The post-commit hook skips "auto: daily backup" commits (recursion)
9. Regex with re.DOTALL on BLUEPRINT.md → catastrophic backtracking. Use line-by-line only!
10. get_day() returns JSON with a nested structure (sleep.totalSleep).
    daily_metrics has flat columns (sleep_total, hrv). Do not confuse them.
11. Lifestyle agents get genomic context via build_lifestyle_genome_block(domain)
12. MDT/wellally_consult injects build_genetic_context_block() into data_package
13. Caffeinate: morningwake starts at 06:45 local; 7200s covers until 08:45.
    For time zones UTC+4 and further east, check APScheduler coverage.
14. CheckinState.is_stale(ttl_hours=4) resets a stuck check-in.
    The 20:00 local-time autostart was removed on 2026-08-17 — the only entry now is
    handlers/meta.cmd_checkin (manual /checkin command).
15. Determine resilience_level categories from data using GROUP BY.
    Do not hardcode categories or assume they match the vendor vocabulary.
16. generate_constitutions.py: model claude-opus-4-7, max_tokens=8000.
    Prompt sources: genetic_variants + periods + agent_reports.raw_output.
    NOT used: promethease_variants (0 rows), lifestyle reports, 30/90-day aggregates.
    Read raw_output, NOT findings (findings is a brief summary that loses longitudinal detail).
17. longitudinal_analysis.py: reads agent_reports WHERE agent_type='longitudinal_analysis'.
    Returns key correlations and multi-year trends in tenant metrics (numbers are in the DB, not here).
```

---

## ALERT LOGIC (recommendation_engine v2, 2026-04-21)

> Architecture explanation and evidence base: [recommendation_engine.md](recommendation_engine.md).
> How to add a new domain: [docs/how-to/add_domain.md](docs/how-to/add_domain.md).

### DOMAIN_SIGNALS

Domain signals are defined by `system_config.domain_signals.*`
(generated block GEN:DOMAIN_SIGNALS below). Admission checks association strength, pair count and stability.
An independently invented configuration example; its fields and numbers do not come from tenant data:

```python
DOMAIN_SIGNALS = {
    "example_domain": [
        {"metric": "example_metric", "label": "example_outcome", "r": 0.42, "threshold_pct": 20},
    ],
}
```

The signal direction determines percentile interpretation: for an inverted metric,
a higher value corresponds to a lower percentile.

### Absolute floors (Layer 2)

These trigger regardless of the domain and the 90-day percentile.
Source: `threshold_analysis.py` — threshold = the metric's historical lower percentile (p10) over the tenant's entire series
or the last two years. Threshold values are in the threshold table in the tenant DB, not in the document.

| Metric | Threshold | How it was selected |
|---------|-------|------------|
| hrv | < tenant p10, ms | p10 over the entire series (the threshold was raised after recalculation) |
| readiness | < tenant p10 | p10 over the last 2 years |
| sleep_deep | < tenant p10, h | p10 over the last 2 years |

### Urgency

- `recommended` — 1 signal, percentile 10–30%
- `required` — 2+ signals **or** percentile < 10%

### Baseline and active protocols

The “baseline versus period” comparison and active protocols with constraints are tenant data:
they live in the tenant's DB (`threshold_analysis.py`, protocol tables and `patient_constraints`),
not in this document (the owner's numbers and protocols were here until 2026-09-26).

> ⚠️ When constitutions (`constitutions/*.md`) change, regenerate the protocols.

---

## TEST INFRASTRUCTURE (2026-05-08, v3.0)

> Details: `TEST_ARCHITECTURE.md`, `USE_CASES.md`,
> `docs/explanation/test_architecture.md`.

### Canonical documents

| File | Diataxis type | Purpose |
|---|---|---|
| `USE_CASES.md` | Reference + rules | Catalogue of 52 UCs across 12 groups with lifecycle status × confirmation. |
| `uc_index.yaml` | Reference (machine) | Machine-readable index of confirmed UCs: modules/data/tests/oracle/risk. |
| `TEST_ARCHITECTURE.md` | Reference + Explanation | 7-layer pyramid + consistency-specific layer. |
| `EXTERNAL_DEPENDENCIES.md` + `external_dependencies.yaml` | Reference | Registry of upstream artifacts. |
| `ROADMAP.md` | Status-tracking | Work plan + statuses. |
| `docs/explanation/test_architecture.md` | Explanation | Why this architecture was chosen. |
| `docs/how-to/run_tests.md` | How-to | Commands for running tests. |
| `docs/how-to/add_new_uc.md` | How-to | UC-J-02 process. |
| `docs/how-to/handle_test_failure.md` | How-to | How to handle a nightly failure. |
| `tests/charters/CH-*.md` | Reference (procedures) | Risk-based routes for UCs that cannot be automated. |

### New code modules

| Module | Purpose |
|---|---|
| `_time_inject.py` | Centralized time injection (get_now/get_today/set_test_clock) |
| `propose_uc.py` | UC-J-01: git diff → proposal in `tests/plans/proposed/` (does NOT write to USE_CASES.md) |
| `generate_test.py` | UC-J-02: skeleton generator with a `confirmation=confirmed` check |
| `validate_uc_index.py` | UC-J-03: `uc_index.yaml` validator |
| `test_failure_handler.py` | 3 levels of nightly failure handling (Triage A + Haiku diagnosis B; Repair C is not implemented — CLAUDE.md §13 narrows its scope as of 2026-08-03, rather than banning it) |
| `morning_test_summary.py` | Suite summary in `agent_reports type='test_summary'` |
| `monthly_api_report.py` | Monthly TG report of API spending on diagnosis |
| `run_full_test_suite.sh` | Pyramid orchestrator |

### New launchd agents on Studio

| Label | Schedule | What runs |
|---|---|---|
| `com.larry.health.test-suite` | 00:00 daily (ExitTimeOut=21600) | `run_full_test_suite.sh` + `test_failure_handler.py` + `morning_test_summary.py` |
| `com.larry.health.test-api-report` | 1st of the month, 09:30 | `monthly_api_report.py` |

### Changed modules (Tier-1 clock refactor)

`gp_agent`, `morning_report`, `integrity_tests`, `triage_agent`, `health_db`,
`import_oura`, `import_apple_health`, `import_all`, `safety_net`,
`checkin_agent`, `task_agent`, `hai_reports`, `hai_core`, `wellally_consult`,
`genome_update_agent` — all use `_time_inject.get_now()` / `get_today()`
in their logic code. CLI blocks under `__main__` are intentionally unchanged.

### Semantic changes

- **`health_db.get_conn()`** — single-primary guard via SQLite `mode=ro`
  on non-Studio hosts. Override: `ALLOW_WRITE_NONPRIMARY=1`. UC-I-07 `implemented`.
- **`run_checks.sh`** exports `ALLOW_WRITE_NONPRIMARY=1` (BACKLOG:
  move pre-commit smoke checks to Studio).
- **`morning_report.py`** contains a “Tests” section: regression at the beginning,
  expected_gap at the end as INFO.

### Coverage as of 2026-05-08

```
301 PASSED · 9 SKIPPED · 4 DESELECTED (requires_anthropic_key) · 2 XFAILED
```

XFAIL — known bugs, documented as `partial`:
- `gp_agent.py:478,389,527-528` — NULL → 0 in an f-string (UC-I-03).
- `import_apple_health.py:248` — shallow merge with null overwrites Oura (UC-A-03).

---

## BACKLOG

> Tasks and technical debt → see the debt log `BACKLOG.md` (maintained in the private part of the project).


## RULES FOR UPDATING THIS FILE

```
AFTER ANY CODE CHANGE:
1. If a public function is added/changed → update the MODULE REGISTRY section
2. If a table in health_db is added/changed → update the DB SCHEMA section
3. If a new module is added → add it to the REGISTRY and DEPENDENCY GRAPH
4. If a pattern/convention changes → update KEY PATTERNS
5. If something is planned/done → update WHAT IS PLANNED

BEFORE ANY WORK ON THE PROJECT:
1. Read this entire file
2. Make sure the required function does not already exist
3. Make sure the required table/field does not already exist
4. Only then propose new code
```

<!-- END OF FILE. Lines: ~350. Generation date: 2026-04-08. -->
<!-- Next regeneration: when a new module or table is added. -->

## ARCHITECTURE CHANGE LOG

<!-- GEN:ARCH_LOG:START -->
<!-- Generated by arch_guard.py; descriptions are machine-translated from the Russian source. -->
- `2026-09-30` — new module `night_repair` (depends on: infra_config, secrets_paths)
- `2026-09-30` — `food_genome` + dependency: region_pack
- `2026-09-30` — `reminders_sync` + dependency: notify
- `2026-09-30` — `integrity_tests` + dependency: finding_identity; `parked_decisions` + dependency: config_db
- `2026-09-30` — removed module `morning_report`
- `2026-09-30` — new module `diagnosis_guard` (depends on: pii_census)
- `2026-09-29` — `import_all` + dependency: config_db; `import_coordinator` + dependency: config_db; `import_medical_events` + dependency: config_db
- `2026-09-29` — `daemon_liveness` + dependency: plist_env_liveness; `dashboard` + dependency: daemon_liveness; new module `import_watchdog` (depends on: health_db, import_status_db, notify); `lab_intake_watcher` + dependency: daemon_liveness, plist_env_liveness (+7 changes)
- `2026-09-29` — `genome_intake` + dependency: config_db
- `2026-09-29` — `vcf_import_pipeline` + dependency: genome_intake
- `2026-09-29` — `cpic_reference_db` + dependency: i18n; `longitudinal_analysis` + dependency: i18n; new module `traits_pipeline` (depends on: i18n); new module `wellness_pipeline` (depends on: i18n, traits_pipeline)
- `2026-09-29` — `clinical_kb` + dependency: i18n; `food_genome` + dependency: i18n; `food_profile` + dependency: i18n; `food_quarterly` + dependency: clinical_kb, i18n (+2 changes)
- `2026-09-29` — `consult_prep` + dependency: i18n; `food_rule_review` + dependency: i18n; `hypothesis_consilium_eval` + dependency: i18n
- `2026-09-29` — `assessment_importer` + dependency: i18n; new module `dashboard_views` (depends on: i18n); `labs_db` + dependency: i18n; `treatment_summary` + dependency: i18n
History before 2026-09-29 is in the Russian [ARCH_SNAPSHOT.md](ARCH_SNAPSHOT.md).
- `patient_constraints` supports protocol-linked constraints with applicability conditions.
<!-- GEN:ARCH_LOG:END -->

---

<!-- GEN:TEST_COVERAGE:START -->

## TEST COVERAGE (latest nightly run)
<!-- Generated by gen_arch_blocks.py from tests/reports/<latest>/summary.json; descriptions are machine-translated from the Russian source. -->

_No data_ — `tests/reports/<YYYY-MM-DD>/summary.json` not found.

<!-- GEN:TEST_COVERAGE:END -->

---

<!-- GEN:XFAIL_LIST:START -->

## XFAIL — known regressions under observation
<!-- Generated by gen_arch_blocks.py: AST scan of @pytest.mark.xfail in tests/; descriptions are machine-translated from the Russian source. -->

**Total:** 10

| Test | Reason |
|---|---|
| `tests/unit/test_import_all_exit_status.py:106` | F-03: `main()` has no return statement at all — success is indistinguishable from any other outcome. Will go green in Ph |
| `tests/unit/test_import_all_exit_status.py:119` | F-03: per-file error is counted and printed but did not reach the return code. Will go green in Phase C. |
| `tests/unit/test_import_all_exit_status.py:178` | F-03: `__main__` calls `main()` without SystemExit — shell always sees 0. Will go green in Phase C. |
| `tests/unit/test_import_medical_events_cli.py:83` | F-03: `main()` returns no code — a successful run returns None. Will go green in Phase C. |
| `tests/unit/test_import_medical_events_cli.py:98` | F-03: handler failure does not reach the process return code. Will go green in Phase C. |
| `tests/unit/test_lab_name_homes.py:88` | F-09: the recognizer dictionary is built from `_VOCAB` ∪ shared `lab_vocab`, but `lab_name_aliases` (the sole human-conf |
| `tests/unit/test_lab_promote_glossary.py:46` | F-14: `_canon` accepts the glossary as an argument and never reads it — human confirmation is structurally disabled. Wil |
| `tests/unit/test_lab_promote_glossary.py:71` | F-14: the glossary is requested with constant `format_id=0`, but format ids are unpredictable (synevo=1, imd_berlin=4242 |
| `tests/unit/test_save_clinical_idempotence.py:69` | F-19: the consultation duplicate key does not include `source_file` — two DIFFERENT documents of the same type on the sa |
| `tests/unit/test_save_clinical_idempotence.py:94` | F-03 inside function: a write failure is silenced in `except Exception: print(...)` and None propagates outward — the ca |

<!-- GEN:XFAIL_LIST:END -->

---

<!-- GEN:EXTERNAL_INTEGRATIONS:START -->

## EXTERNAL INTEGRATIONS
<!-- Generated by gen_arch_blocks.py. The ⚠️ marker means a secret file is missing; descriptions are machine-translated from the Russian source. -->

### Services

| Service | Secret | Purpose | Module |
|---|---|---|---|
| Anthropic Claude API | `~/.health_secrets/anthropic_key` | AI, reports, chat | `hai_core, gp_agent` |
| Oura Ring API v2 | `~/.health_secrets/oura_token` | sleep, HRV, activity | `import_oura` |
| Telegram Bot API | `~/.health_secrets/telegram_token` | user interface | `telegram_bot` |
| Telegram chat-id | `~/.health_secrets/telegram_chat_id` | target chat (OWNER fail-closed) | `telegram_bot` |
| Studio X-Sync-Token | `~/.health_secrets/sync_token` | bot→Studio FastAPI authorization | `telegram_bot, main` |
| PubMed E-utilities | `—` | medical literature (public) | `pubmed_client` |
| MyVariant.info | `—` | genomic variant annotation (public) | `genome_annotator` |
| GWAS Catalog | `—` | trait associations (public) | `genome_context` |
| Apple Health Export | `(iCloud XML)` | steps, heart rate, weight | `import_apple_health` |
| macOS Calendar | `(icalbuddy CLI)` | events, trips | `calendar_client` |
| macOS Reminders | `(osascript)` | user tasks | `task_agent, reminders_sync` |

### Tailscale endpoints

| Endpoint | URL | Purpose |
|---|---|---|
| Private Serve :443 | https://<tailnet_hostname>/ | neighbour project Flask :5001 (tailnet-only); route /→:8000 retired 2026-07-06, TD-09 |
| Public Funnel :10000 | https://<tailnet_hostname>:10000/mm | neighbour project public gateway → :9001 (allow-list reverse proxy) |

<!-- GEN:EXTERNAL_INTEGRATIONS:END -->

---

<!-- GEN:DOMAIN_SIGNALS:START -->

## DOMAIN_SIGNALS (source: `system_config.domain_signals.*`)
<!-- Generated by gen_arch_blocks.py --only domain_signals; descriptions are machine-translated from the Russian source. -->
<!-- Source of truth: the tenant DB; r (correlation over tenant observations) is excluded from this document. -->

### vagal_activation

| metric | label | threshold_pct |
|---|---|---:|
| `hrv` | HRV tomorrow | 30 |
| `recovery_high_min` | HRV tomorrow | 25 |
| `stress_high_min` | Resting HR tomorrow | 30 |
| `resting_hr` | stress load | 30 |

### stress

| metric | label | threshold_pct |
|---|---|---:|
| `stress_high_min` | Resting HR tomorrow | 30 |
| `resting_hr` | stress load | 30 |
| `recovery_high_min` | stress/recovery balance | 25 |

### activity

| metric | label | threshold_pct |
|---|---|---:|
| `activity_score` | REM sleep at night | 30 |
| `readiness` | resource for activity | 30 |

### sleep

| metric | label | threshold_pct |
|---|---|---:|
| `sleep_score` | readiness tomorrow | 25 |
| `sleep_rem` | cognitive recovery | 25 |
| `sleep_efficiency` | recovery tomorrow | 25 |

### nutrition

| metric | label | threshold_pct |
|---|---|---:|
| `stress_high_min` | nutrition adaptation | 25 |
| `resting_hr` | inflammatory proxy | 30 |

<!-- GEN:DOMAIN_SIGNALS:END -->
