<!-- translation-of: docs/explanation/data_flow.md sha256:920016146384 -->
**English** · [Русский](data_flow.md)

# Data flow: from source to report

> **Document type:** Explanation (Diataxis) — explains how the system works internally.
> For the database/path reference: [ARCH_SNAPSHOT.md](../../ARCH_SNAPSHOT.md).
> For manual execution: [docs/how-to/run_analysis.md](../how-to/run_analysis.md).

---

## Where the data comes from

The system receives data from three sources:

1. **Oura Ring** — sleep, recovery, and activity biometrics (API v2)
2. **Apple Health** — steps, heart rate, VO2Max, weight, calories (XML export)
3. **Medical documents** — PDFs with lab results (through fswatch + import_all.py)

Each source goes into one of two stores:
- `daily_metrics/YYYY-MM-DD.json` — daily biometrics (merge, not overwrite)
- `health.db` — everything structured: lab results, tasks, reports, genome, memory

---

## A typical day's schedule


```
03:00 local  MacBook LaunchAgent → backup.sh
                                 → ssh studio "git add -A && git commit"
                                 → commit trigger on Studio (canonical git since 2026-05-09)
03:00 local  Studio  LaunchAgent → backup_studio.sh
                                 → sqlite3 .backup → ~/health/backups/health_YYYY-MM-DD.db
                                 → sqlite3 .backup → <neighbour>/backups/<db>_YYYY-MM-DD.db
                                 → rotation: deletes copies older than 30 days
                                 → offsite: delegated to Time Machine

06:45 local  LaunchAgent → morning_wake.sh → caffeinate -i -t 7200

07:50 local  LaunchAgent → run_checks.sh --scheduled      [integrity_tests]
                         → exit 0: marker "OK"
                         → exit 1: marker "WARN:N" (no Telegram before 08:00)
                         → exit 2: Telegram alert, report blocked

continuous  HAE (phone) → POST /hae/ingest → parsing + metrics judge (hae_checker)
08:00 local  → morning_report.py → triage_agent.run_triage()
                              → GP report missing → gp_agent.py (Popen)
                              → genome_update did not run → genome_update_agent.py
                              → labs/periods → Telegram question to the user
08:30 local  APScheduler → send_morning_report()
                         → gp_agent.generate_daily_report()
                         → task_agent.process_gp_report()
                         → _send_tasks_from_report() + _send_problem_proposals()

             (18:45 caffeinate + 20:00 local-time evening check-in REMOVED 2026-08-17 —
              autostart and LaunchAgent com.larry.healthbot.checkinwake deleted;
              check-in remains only via the manual /checkin command)

23:00 local  Sunday → run_specialists_scheduled() [MDT]
03:00        Sunday → integrity_tests.py --pubmed-only
07:00 local  Monday → send_weekly_report()

every 1h    LaunchAgent → calendar_sync.py
every 3h    LaunchAgent → reminders_sync.py (8:00–23:00)
every 3h    LaunchAgent → import_oura.py 2 (8:00, 11:00, 14:00, 17:00, 20:00)
every 6h    LaunchAgent → icloud_conflict_check.sh
every 120s  APScheduler → check_pending_consults()
on .py/.sh change      → watch_and_test.sh [smoke + integrity]
```

---

## How data gets from the API to the database

### Oura Ring (primary source)
**Triggers:** LaunchAgent oura-import (08:00, 11:00, 14:00, 17:00, 20:00) + `refresh_data()` before the report (08:30)
**Reason for frequent runs:** Oura processes data with a delay; running only at 08:00 leaves the evening check-in with no data.

```
Oura API v2
  └── 5 endpoints in parallel:
      sleep           → totalSleep, deep, REM, overnight HRV
      daily_sleep     → score, contributors
      daily_readiness → readiness_score, hrv_balance, recovery_index
      daily_activity  → steps, active_kcal, distance_km
      daily_spo2      → spo2.avg, breathing_disturbance_index

  └── Merge by date: save_day(d, merged)
      Rule: only the long_sleep session is taken (> 1 h, max total_sleep_duration)
      Naps < 3600s are filtered out
      Path: ~/iCloud/health/data/daily_metrics/YYYY-MM-DD.json
```


### Apple Health
**Trigger:** the phone automatically sends a Health Auto Export dump to `POST /hae/ingest`
(`dashboard_routers/api_hae_ingest.py`, since 2026-07-06). The raw file is saved in `data/hae_rest/`,
parsed by the same `import_apple_health.process_hae_json`, and each metric passes through the judge
`hae_checker.judge_payload`: everything the device sends must have an owner (a parsing branch or
a recorded "do not take" decision). The previous path — LaunchAgent `com.larry.health.daily` at 08:00 through
an iCloud directory — was removed on 2026-09-26: the directory has been empty since the switch to REST.

### JSON → SQLite
`health_db.upsert_metrics_from_json()` reads all .json files and writes to `daily_metrics`.
Called during initialization and from `import_all.py`.

### Medical documents (fswatch)
**LaunchAgent:** com.larry.health.watcher (KeepAlive) — runs on **MacBook**

```
fswatch ~/iCloud/health/ (recursive)
  Guard: /tmp/health_import.lock (prevents parallel runs)

On a change in the folder:
  └── import_all.py
        ├── Scans all *.pdf (PDF only, JPEG not supported)
        ├── Skips financial ones: SKIP_PATTERNS (payment slips, receipts)
        ├── OCR via tesseract → classify() → document type
        ├── parse_date() → date from text or file name
        │     Formats: DD.MM.YYYY, MM.YYYY (→ YYYY-MM-01), YYYY-MM-DD, DD/MM/YYYY
        ├── Saves JSON to data/imaging/, data/biochemical/, data/pathology/ etc.
        └── save_clinical_to_db() — clinical types → consultations (SQLite)
              Types: imaging_pet, oncology_visit, endoscopy, pathology, biopsy, discharge
              Condition: date != "unknown" and no duplicate by (date, specialist_type)
```

**⚠️ OCR limitations:**
- `tesseract` is installed only on **MacBook** — the watcher must run there too
- `fitz` (PyMuPDF) is installed only on **Studio** — scanned PDFs (without a text layer) need conversion through Studio
- Scanned PDFs: fitz renders the page → sips converts to TIFF → tesseract reads it
- JPEG/PNG files in the health/ folder **are not processed** by the watcher — PDF only

**Import result:**
- Lab tests: JSON in `data/biochemical/` → `import_all_biochemical()` → `lab_results`
- Clinical documents: `consultations` (date, specialist_type, key_findings, source_file)
- Biopsies, pathology, endoscopy: `consultations` with the corresponding specialist_type

### Storage structure
```
~/iCloud/health/data/
  daily_metrics/YYYY-MM-DD.json  — biometrics (Oura + Apple Health), merge
  reports/YYYY-MM-DD.md          — morning narrative (pure text)
  health.db                      — everything else (SQLite, 25 tables)
      daily_metrics              — flattened copy of the JSON
      lab_results                — lab tests with history
      agent_reports              — GP/MDT/daily reports
      checkins                   — evening check-ins + FTS
      tasks                      — tasks with fingerprint deduplication
      problem_list               — medical problem list
      experiments                — experiment protocols
      memory                     — long-term AI memory + hypotheses
      genetic_variants           — annotated SNPs
      protocols                  — active behavioural protocols
```

---

## How data reaches agent context

Each agent receives context through `_build_gp_context()` when generating a report:

- **7 days in detail:** each day gets a line with sleep/deep/HRV/score/steps; contextual events (`context_events`) from the database are added as a ⚑ marker (for example, night_flight, travel) — the agent does not interpret an anomaly as a pattern
- **Trends over 4 horizons:** averages over 7/14/30/90 days (`health_db.get_stats()`)
- **Lab data:** latest results over 2 years for 13 key tests;
  a ⚠ flag for deviations from the reference range; a "DATA IS OUTDATED" block if a key tumor marker is > 90 days old
  or a critical test > 180 days old
- **Lifestyle patterns:** LifestyleAgents (Sleep/Movement/Stress/Energy) for each
  day of the period, with their ⚠ flags aggregated
- **Latest MDT:** included in the context if it occurred within the last 14 days
- **Problem list:** list of active problems
- **Genomic context:** `build_genetic_context_block()` — clinically significant variants
- **Open hypotheses:** `get_open_hypotheses()`

**What is not included automatically:** data older than 90 days, all lab tests (only the 13 key ones),
the full history of experiments.

### Agent isolation
Lifestyle agents do not see GP reports. The GP sees lifestyle flags through aggregation.
MDT specialists receive the same data_package — they do not see each other's opinions
(independent assessments, not consensus). The GP reads the MDT synthesis, not individual opinions.
