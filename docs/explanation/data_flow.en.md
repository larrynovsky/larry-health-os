<!-- translation-of: docs/explanation/data_flow.md sha256:f0469ceb0d54 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](data_flow.md)

# Data Flow: From Source to Report

> **Document type:** Explanation (Diataxis) — explains how the system works internally.
> For the DB/paths reference: [ARCH_SNAPSHOT.md](../../ARCH_SNAPSHOT.md).
> For running manually: [docs/how-to/run_analysis.md](../how-to/run_analysis.md).

---

## Where the data comes from

The system receives data from three sources:

1. **Oura Ring** — biometrics for sleep, recovery, and activity (API v2)
2. **Apple Health** — steps, HR, VO2Max, weight, calories (XML export)
3. **Medical documents** — PDFs with test results (via fswatch + import_all.py)

Each source lands in one of two stores:
- `daily_metrics/YYYY-MM-DD.json` — daily biometrics (merge, not overwrite)
- `health.db` — everything structured: lab results, tasks, reports, genome, memory

---

## A typical day's schedule

```
03:00 local  MacBook LaunchAgent → backup.sh
                                 → ssh studio "git add -A && git commit"
                                 → триггер commit на Studio (canonical git с 2026-05-09)
03:00 local  Studio  LaunchAgent → backup_studio.sh
                                 → sqlite3 .backup → ~/health/backups/health_YYYY-MM-DD.db
                                 → sqlite3 .backup → <neighbour>/backups/<db>_YYYY-MM-DD.db
                                 → ротация: удаляет копии старше 30 дней
                                 → оффсайт: делегирован Time Machine

06:45 local  LaunchAgent → morning_wake.sh → caffeinate -i -t 7200

07:50 local  LaunchAgent → run_checks.sh --scheduled      [integrity_tests]
                         → exit 0: маркер "OK"
                         → exit 1: маркер "WARN:N" (без Telegram до 08:00)
                         → exit 2: Telegram алерт, отчёт заблокирован

непрерывно  HAE (телефон) → POST /hae/ingest → разбор + судья метрик (hae_checker)
08:00 local  → morning_report.py → triage_agent.run_triage()
                              → GP-отчёт отсутствует → gp_agent.py (Popen)
                              → genome_update не запускался → genome_update_agent.py
                              → анализы/периоды → Telegram-вопрос пользователю
08:30 мест.  APScheduler → send_morning_report()
                         → gp_agent.generate_daily_report()
                         → task_agent.process_gp_report()
                         → _send_tasks_from_report() + _send_problem_proposals()

             (18:45 caffeinate + 20:00 по местному времени вечерний чекин СНЯТЫ 2026-08-17 —
              автозапуск и LaunchAgent com.larry.healthbot.checkinwake удалены;
              чекин остался только по ручной команде /checkin)

23:00 мест.  воскресенье → run_specialists_scheduled() [MDT]
03:00        воскресенье → integrity_tests.py --pubmed-only
07:00 мест.  понедельник → send_weekly_report()

каждые 1ч   LaunchAgent → calendar_sync.py
каждые 3ч   LaunchAgent → reminders_sync.py (8:00–23:00)
каждые 3ч   LaunchAgent → import_oura.py 2 (8:00, 11:00, 14:00, 17:00, 20:00)
каждые 6ч   LaunchAgent → icloud_conflict_check.sh
каждые 120с APScheduler → check_pending_consults()
при изменении .py/.sh  → watch_and_test.sh [smoke + integrity]
```

---

## How data gets from the API into the database

### Oura Ring (primary source)
**Triggers:** LaunchAgent oura-import (08:00, 11:00, 14:00, 17:00, 20:00) + `refresh_data()` before the report (08:30)
**Reason for frequent runs:** Oura processes data with a delay; running only at 08:00 yields empty data for the evening check-in.

```
Oura API v2
  └── 5 эндпоинтов параллельно:
      sleep           → totalSleep, deep, REM, HRV ночной
      daily_sleep     → score, contributors
      daily_readiness → readiness_score, hrv_balance, recovery_index
      daily_activity  → steps, active_kcal, distance_km
      daily_spo2      → spo2.avg, breathing_disturbance_index

  └── Merge по дате: save_day(d, merged)
      Правило: берётся только long_sleep сессия (> 1 ч, max total_sleep_duration)
      Дремота < 3600с — отфильтровываются
      Путь: ~/iCloud/health/data/daily_metrics/YYYY-MM-DD.json
```

### Apple Health
**Trigger:** the phone itself pushes a Health Auto Export dump to `POST /hae/ingest`
(`dashboard_routers/api_hae_ingest.py`, from 06.07.2026). The raw file is saved to `data/hae_rest/`,
parsed by the same `import_apple_health.process_hae_json`, and each metric goes through the
`hae_checker.judge_payload` judge: everything the device sends must have an owner (a parse branch or
a recorded decision "not taking this"). The previous path — LaunchAgent `com.larry.health.daily` at 08:00
via the iCloud directory — was removed on 26.09.2026: the directory has been empty since the switch to REST.

### JSON → SQLite
`health_db.upsert_metrics_from_json()` reads all .json files and writes them to `daily_metrics`.
Called on initialisation and from `import_all.py`.

### Medical documents (fswatch)
**LaunchAgent:** com.larry.health.watcher (KeepAlive) — runs on **MacBook**

```
fswatch ~/iCloud/health/ (рекурсивно)
  Защита: /tmp/health_import.lock (предотвращает параллельный запуск)

При изменении в папке:
  └── import_all.py
        ├── Сканирует все *.pdf (только PDF, JPEG не поддерживаются)
        ├── Пропускает финансовые: SKIP_PATTERNS (платёжки, чеки)
        ├── OCR через tesseract → classify() → тип документа
        ├── parse_date() → дата из текста или имени файла
        │     Форматы: DD.MM.YYYY, MM.YYYY (→ YYYY-MM-01), YYYY-MM-DD, DD/MM/YYYY
        ├── Сохраняет JSON в data/imaging/, data/biochemical/, data/pathology/ и др.
        └── save_clinical_to_db() — клинические типы → consultations (SQLite)
              Типы: imaging_pet, oncology_visit, endoscopy, pathology, biopsy, discharge
              Условие: дата != "unknown" и нет дубля по (date, specialist_type)
```

**⚠️ OCR limits:**
- `tesseract` is installed only on **MacBook** — the watcher must run there too
- `fitz` (PyMuPDF) is installed only on **Studio** — scanned PDFs (without a text layer) require conversion via Studio
- Scanned PDFs: fitz renders the page → sips converts to TIFF → tesseract reads it
- JPEG/PNG files in health/ are **not processed** by the watcher — only PDF

**Import results:**
- Lab results: JSON in `data/biochemical/` → `import_all_biochemical()` → `lab_results`
- Clinical documents: `consultations` (date, specialist_type, key_findings, source_file)
- Biopsies, pathology, endoscopy: `consultations` with the corresponding specialist_type

### Storage structure
```
~/iCloud/health/data/
  daily_metrics/YYYY-MM-DD.json  — биометрика (Oura + Apple Health), merge
  reports/YYYY-MM-DD.md          — утренний нарратив (pure text)
  health.db                      — всё остальное (SQLite, 25 таблиц)
      daily_metrics              — flatten-копия JSON
      lab_results                — анализы с историей
      agent_reports              — GP/MDT/daily отчёты
      checkins                   — вечерние чекины + FTS
      tasks                      — задачи с fingerprint-дедупликацией
      problem_list               — медицинский problem list
      experiments                — протоколы экспериментов
      memory                     — долгосрочная память AI + гипотезы
      genetic_variants           — аннотированные SNP
      protocols                  — активные поведенческие протоколы
```

---

## How data gets into agent context

Each agent receives context via `_build_gp_context()` when generating a report:

- **7 days in detail:** each day — sleep/deep/HRV/score/steps as a single line; contextual events (`context_events`) from the DB are added as a ⚑ label (e.g. night_flight, travel) — the agent does not interpret an anomaly as a pattern
- **Trends across 4 horizons:** averages for 7/14/30/90 days (`health_db.get_stats()`)
- **Lab data:** most recent results over the past 2 years for 13 key tests;
  ⚠ flag when outside the reference range; "DATA OUTDATED" block if a key oncology marker is >90 days old
  or a critical test is >180 days old
- **Lifestyle patterns:** LifestyleAgents (Sleep/Movement/Stress/Energy) for each
  day of the period; their ⚠ flags are aggregated
- **Most recent MDT:** if one occurred within the last 14 days — included in context
- **Problem list:** list of active problems
- **Genomic context:** `build_genetic_context_block()` — clinically significant variants
- **Open hypotheses:** `get_open_hypotheses()`

**What is not included automatically:** data older than 90 days, all labs (only 13 key ones),
full experiment history.

### Agent isolation
Lifestyle agents do not see GP reports. The GP sees lifestyle flags via aggregation.
MDT specialists receive the same data_package — they do not see each other's opinions
(independent assessments, not consensus). The GP reads the MDT synthesis, not the individual opinions.
