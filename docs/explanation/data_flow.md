[English](data_flow.en.md) · **Русский**

# Поток данных: от источника до отчёта

> **Тип документа:** Explanation (Diataxis) — объясняет, как устроена система изнутри.
> Для справочника по DB/путям: [ARCH_SNAPSHOT.md](../../ARCH_SNAPSHOT.md).
> Для запуска вручную: [docs/how-to/run_analysis.md](../how-to/run_analysis.md).

---

## Откуда приходят данные

Система получает данные из трёх источников:

1. **Oura Ring** — биометрика сна, восстановления, активности (API v2)
2. **Apple Health** — шаги, ЧСС, VO2Max, вес, калории (XML export)
3. **Медицинские документы** — PDF с анализами (через fswatch + import_all.py)

Каждый источник попадает в одно из двух хранилищ:
- `daily_metrics/YYYY-MM-DD.json` — биометрика дня (merge, не перезапись)
- `health.db` — всё структурированное: анализы, задачи, отчёты, геном, память

---

## Расписание типичного дня


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

## Как данные попадают из API в БД

### Oura Ring (основной источник)
**Триггеры:** LaunchAgent oura-import (08:00, 11:00, 14:00, 17:00, 20:00) + `refresh_data()` перед отчётом (08:30)
**Причина частого запуска:** Oura обрабатывает данные с задержкой; запуск только в 08:00 даёт пустые данные для вечернего чекина.

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
**Триггер:** телефон сам отправляет выгрузку Health Auto Export на `POST /hae/ingest`
(`dashboard_routers/api_hae_ingest.py`, с 06.07.2026). Сырой файл сохраняется в `data/hae_rest/`,
разбирается тем же `import_apple_health.process_hae_json`, и каждая метрика проходит судью
`hae_checker.judge_payload`: у всего, что прибор присылает, должен быть хозяин (ветка разбора или
записанное решение «не берём»). Прежний путь — LaunchAgent `com.larry.health.daily` в 08:00 через
iCloud-каталог — снят 26.09.2026: каталог пуст с перехода на REST.

### JSON → SQLite
`health_db.upsert_metrics_from_json()` читает все .json и записывает в `daily_metrics`.
Вызывается при инициализации и из `import_all.py`.

### Медицинские документы (fswatch)
**LaunchAgent:** com.larry.health.watcher (KeepAlive) — работает на **MacBook**

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

**⚠️ OCR-ограничения:**
- `tesseract` установлен только на **MacBook** — watcher должен работать там же
- `fitz` (PyMuPDF) установлен только на **Studio** — для сканированных PDF (без текстового слоя) нужна конвертация через Studio
- Сканированные PDF: fitz рендерит страницу → sips конвертирует в TIFF → tesseract читает
- JPEG/PNG в папке health/ watcher'ом **не обрабатываются** — только PDF

**Результат импорта:**
- Лабораторные анализы: JSON в `data/biochemical/` → `import_all_biochemical()` → `lab_results`
- Клинические документы: `consultations` (дата, specialist_type, key_findings, source_file)
- Биопсии, патология, эндоскопия: `consultations` с соответствующим specialist_type

### Структура хранилища
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

## Как данные попадают в контекст агентов

Каждый агент при генерации отчёта получает контекст через `_build_gp_context()`:

- **7 дней детально:** каждый день — сон/deep/ВСР/score/шаги в строку; контекстные события (`context_events`) из БД добавляются как метка ⚑ (например, night_flight, travel) — агент не интерпретирует аномалию как паттерн
- **Тренды 4 горизонта:** среднее за 7/14/30/90 дней (`health_db.get_stats()`)
- **Лабораторные данные:** последние результаты за 2 года по 13 ключевым тестам;
  флаг ⚠ при отклонении от референса; блок "ДАННЫЕ УСТАРЕЛИ" если ключевой онкомаркер > 90 дней
  или критичный тест > 180 дней
- **Lifestyle-паттерны:** LifestyleAgents (Sleep/Movement/Stress/Energy) за каждый
  день периода, их ⚠-флаги агрегируются
- **Последний MDT:** если был за последние 14 дней — включается в контекст
- **Problem list:** список активных проблем
- **Геномный контекст:** `build_genetic_context_block()` — клинически значимые варианты
- **Открытые гипотезы:** `get_open_hypotheses()`

**Чего нет автоматически:** данные за >90 дней, все лаборатории (только 13 ключевых),
полная история экспериментов.

### Изоляция агентов
Lifestyle-агенты не видят GP-отчёты. GP видит lifestyle-флаги через агрегацию.
MDT-специалисты получают одинаковый data_package — не видят мнений друг друга
(независимые оценки, не консенсус). GP читает MDT synthesis, не индивидуальные мнения.
