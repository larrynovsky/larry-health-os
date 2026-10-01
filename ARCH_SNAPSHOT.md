[English](ARCH_SNAPSHOT.en.md) · **Русский**

# ARCH_SNAPSHOT — Larry Health OS

**Версия:** 15.294 | **Дата:** 2026-10-01

<!-- AUTO-READABLE ARCHITECTURE INDEX. Строку выше пишет doc_agent на post-commit
     (переехала из BLUEPRINT.md 2026-08-03, файл удалён) — руками не править. -->
<!-- ПРАВИЛО ДЛЯ AI: читать этот файл ПЕРВЫМ перед любой работой с проектом. -->
<!-- После каждого изменения — обновлять соответствующий раздел. -->
<!-- «Почему устроено так» — subsystem_intent.yaml (замысел) и docs/explanation/.
     Навигация по всем домам — CLAUDE.md § B «Где что живёт». -->
<!-- ⚰️ Прежняя строка «Версия: 1.1 | Дата: 2026-06-15» стояла здесь, пока версию
     вёл BLUEPRINT: дата в комментарии не обновлялась 49 дней и шла к падению
     INV-DOC-5 (порог 60 дней). Теперь у неё один живой писатель. -->

---
---

## НАЗНАЧЕНИЕ И ИДЕЯ ПРОЕКТА
<!-- Обновляется вручную. Клинический контекст тенанта НЕ здесь — доки нейтральны к болезни. -->

**Пациент:** система мультитенантная. Клинический профиль каждого пациента (диагноз,
метрики, геном) живёт в ЕГО данных, не в этом документе (brief-neutralization, 2026-07-16).
Рантайм-источник профиля для промптов — БД тенанта через `patient_context.build_patient_brief()`.
Человекочитаемый dev-обзор владельца — `~/health/data/PATIENT_PROFILE.md` (вне репо/iCloud).

**Главная идея:**
Здоровье — это данные, не интуиция. Система не заменяет врача, но создаёт
intelligence layer между сырыми биометрическими данными и клиническими решениями.

**Что делает система:**
1. Собирает данные: Oura Ring (сон, ВСР, readiness) + Apple Watch + лабы + чекины
2. Анализирует: GP-агент + 9 специалистов MDT + lifestyle-агенты — ежедневно/еженедельно
3. Раз в месяц (консилиум) рождает гипотезы из дрейфа метрик → тестирует → переводит в протоколы поведения
4. Отслеживает задачи (анализы, вопросы врачу, действия) через Telegram + macOS Reminders
5. Контекстуализирует геном: сырой генотип (сотни тысяч SNP) → клинически значимые варианты → риски по доменам
6. Дашборд на Mac Studio (uvicorn :8001/:8002, tailnet-only) — витрина данных. ⚰️ Telegram Mini App убит 2026-07-06 (TD-09), маршрут `/`→`:8000` снят

**Цикл работы:**
Данные → Отчёт → Гипотеза → Протокол → Мониторинг → Подтверждение/Отклонение

**Для кого это важно при добавлении нового кода:**
Любая новая функция должна укладываться в один из слоёв выше.
Приоритет: данные надёжны → отчёты точны → гипотезы проверяемы → задачи закрываемы.
Медицинский контекст означает: ошибка в данных или логике имеет последствия для здоровья.


## УЗЛЫ СИСТЕМЫ

| Узел | Адрес | Роль |
|------|-------|------|
| MacBook Pro | локально | разработка (код, AI, Claude) |
| Mac Studio | <studio_host> (Tailscale) | продакшн: скрипты, бот, FastAPI, БД |

**Studio DB:** `~/health/data/health.db` (источник правды)
**Studio exposure** (канон — `infra_config.py`, датчик SEC-20): Serve `:443` — **tailnet-only, не Funnel**; Funnel `:10000` → `:9001` gateway соседнего проекта. Проверено live 2026-07-06 и 2026-08-02.
**MacBook:** разработка; доставка кода — `git push` (post-commit), **не rsync** (скрипты удалены 2026-06-28, CLAUDE.md §6 RETIRED)
**Secrets:** `~/.health_secrets/` (anthropic_key, sync_token, oura_token, telegram_*)

### Топология: кто пишет, куда едет, что где лежит

<!-- Переехало из BLUEPRINT (раздел «Архитектура») 2026-08-02. Канон Tailscale-экспозиции — infra_config.py (датчик SEC-20);
     расписание launchd — §РАСПИСАНИЕ ниже (генерируется). Здесь только картина связей. -->

```
┌─────────────────────────────────────────────────────────┐
│  MacBook Pro (<macbook_host> Tailscale)                  │
│  Роль: единственный узел записи КОДА (single-writer,    │
│  e017929). Полный git-клон, remote `studio`.            │
│  Модель 2026-05-23; канон: git_architecture.md          │
│                                                          │
│  Flow: edit → git commit → post-commit hook             │
│    └── git push studio main (ff-only) + рестарт бота    │
│  LaunchAgent: com.larry.health.backup (03:00)           │
│    └── backup.sh — снимок дерева в refs/backups/daily-* │
│        (вне main; не коммитит, не деплоит — CLAUDE.md §1)│
└───────────────┬─────────────────────────────────────────┘
                │ git push studio main (receive: updateInstead)
                ▼
┌─────────────────────────────────────────────────────────┐
│  Mac Studio — PRODUCTION (<studio_host> Tailscale)     │
│                                                          │
│  Данные: ~/health/  (HEALTH_DATA_DIR)                   │
│    health.db (SQLite, 27 таблиц)                        │
│    daily_metrics/YYYY-MM-DD.json  ← import_oura.py      │
│    reports/YYYY-MM-DD.md                                │
│    genome/ ← iCloud sync                               │
│                                                          │
│  LaunchAgents: список НЕ здесь — он генерируется        │
│    gen_schedule.py в ARCH_SNAPSHOT §РАСПИСАНИЕ.          │
│    Рукописная копия жила тут и устаревала молча.         │
│                                                          │
│  Tailscale (канон: infra_config.py, датчик SEC-20):     │
│    Serve :443 — TAILNET-ONLY (не Funnel!) → сосед       │
│      (маршрут /→:8000 снят 2026-07-06 — TD-09 закрыт,   │
│       миниапп убит; витрина = дашборд :8001)            │
│    Funnel :10000 — публично → :9001 gateway соседа      │
└─────────────────────────────────────────────────────────┘
                │
                │ Telegram Bot API (Long Polling)
                ▼
         Telegram-бот (чат + команды)
```

---

---

## ВЕРХНЕУРОВНЕВАЯ СТРУКТУРА (codebase map)
<!-- Статичная навигационная карта. Обновляется вручную при добавлении новой top-level папки или класса модулей. -->
<!-- Детальный модульный реестр — ниже в GEN:MODULE_REGISTRY. -->

### Папки `~/health_scripts/`

| Папка | Что внутри |
|---|---|
| `tests/` | 7-слойная pytest-пирамида: `unit/`, `integration/`, `consistency/`, `consistency_specific/`, `self_consistency/`, `meta/`, `charters/` + `fixtures/`, `reports/<YYYY-MM-DD>/` |
| `docs/` | Diátaxis: `explanation/` (почему), `how-to/` (как делать). Reference — это ARCH_SNAPSHOT + docs/reference/ + USE_CASES |
| `scripts/` | Maintenance: `install_hooks.sh`, `pre_destructive_check.sh`, `uncommitted_watchdog.py`, `git-hooks/` (pre-commit + AST silent-except) |
| `migrations/` | Python-миграции схемы health.db (survivorship, etc.). `__init__.py` — порядок применения |
| `constitutions/` | Сгенерированные `.md` «конституции здоровья» (TCM-стиль). Пишет `generate_constitutions`; читают дашборд-витрина и `constitution_analysis`. В ежедневные отчёты (`gp_agent`/`morning_report`) НЕ подаются (сверено аудитом 2026-07-18, E3) |
| `dashboard_templates/` | Jinja2 templates для FastAPI dashboard (Tailscale-only). Содержимое не в git (gitignored) |
| `dashboard_static/` | JS/CSS для dashboard. Не в git |
| `outputs/` | Auto-generated `longitudinal_analysis.{json,xlsx}` и пр. `.gitignore`d (2026-05-19) |
| `reports/` | Анализ-отчёты в markdown. `.gitignore`d |
| `snapshots/` | DB snapshot dumps. `.gitignore`d |
| `plans/` | План-документы будущих фаз |
| `launchd/` | Копии plist-файлов (canonical в `~/Library/LaunchAgents/com.larry.health.*.plist`) |
| `logs/` | Runtime logs всех агентов. `.gitignore`d |

### Файлы в корне

Логические группы:

- **Агенты launchd** (по расписанию): `gp_agent`, `triage_agent`, `task_agent`, `morning_report`, `morning_test_summary`, `monthly_consilium`, `monthly_api_report`, `telegram_bot`, `reminders_sync`, `safety_net`, `checkin_agent`, `test_failure_handler`
- **Импортёры внешних данных**: `import_all`, `import_oura`, `import_apple_health`, `import_medical_events`, `import_medical_docs`, `import_hr_activity`, `genome_update_agent`, `genome_parser`, `genome_annotator`, `genome_context`, `promethease_context`, `backfill_effect_alleles`, `pubmed_client`, `pubmed_searcher`, `publication_reader`
- **HAI pipeline** (гипотезы, анализ): `hai_core`, `hai_analysis`, `hai_chat`, `hai_context`, `hai_reports`, `hai_hypotheses`, `hypothesis_semantic_check`, `literature_curator`
- **CBCR pipeline** (Compass-Based Clinical Reasoning): `cbcr_hypothesis`, `cbcr_lookup`
- **Аналитика**: `longitudinal_analysis`, `constitution_analysis`, `survivorship_analyzer`, `survivorship_curator`, `lab_extractor`
- **Генераторы документации**: `gen_arch_blocks`, `gen_blueprint`, `gen_key_paths`, `gen_schedule`, `generate_constitutions`, `generate_test`
- **Фундамент / core**: `health_db` (single-primary, SQLite, hostname-aware), `integrity_tests` (15+ checks), `_time_inject`, `_domain_audit`, `_fmt_helpers`
- **Dashboard / API**: `dashboard` (FastAPI + Jinja2 + HTMX), `api_spend_log`
- **Wellally**: `wellally_consult`, `check_wellally_updates`
- **Calendar / Reminders**: `calendar_client`, `calendar_sync`, `reminders_sync`
- **Maintenance / контракты**: `arch_guard`, `check_contracts`, `propose_uc`, `validate_uc_index`, `doc_agent`
- **Tests / smoke**: `smoke_tests`, `test_oura`
- **Assessment** (анкеты): `assessment_bot_handlers`, `assessment_dialog`, `assessment_importer`, `assessment_scheduler`
- **Lifestyle**: `lifestyle_agents`, `consult_prep`
- **Migration-only helpers** (одноразовые, префикс `_`): `_create_*`, `_doc_update`, `_fix_*`, `_generate_*`, `_lifestyle_*`, `_patch_*`, `_rebuild_*`, `_sleep_audit` — после применения не запускаются повторно

`.sh`: `backup.sh`, `backup_studio.sh`, `run_checks.sh`, `run_full_test_suite.sh`, `watch_and_import.sh`, `watch_and_test.sh`, `morning_wake.sh`, `icloud_conflict_check.sh` (`checkin_wake.sh` удалён 2026-08-17 вместе с автозапуском чекина)

### Связанные пространства

- **`~/health/` (iCloud)** — данные пациента + 19 analyzer-skills + AGENTS.md. Карта внутри: `~/health/MAP.md`. Cross-link: `health_db._resolve_health_dir()`
- **`~/.infrastructure.md`** — manifest узлов, портов, сервисов, secrets (общий для health-os и соседних проектов)

---

<!-- GEN:SCHEDULE:START -->

## РАСПИСАНИЕ launchd (Studio)
<!-- АВТОГЕНЕРИРУЕТСЯ gen_schedule.py из ~/Library/LaunchAgents/com.larry.health.*.plist. -->
<!-- Не редактировать вручную. -->

| Label | Расписание | Модуль |
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
| `com.larry.health.consilium.partner` | 1-го 04:00 | `bash` |
| `com.larry.health.consilium` | 1-го 04:00 | `bash` |
| `com.larry.health.constitutions.partner` | Вс 05:30 | `bash` |
| `com.larry.health.constitutions` | Вс 05:30 | `bash` |
| `com.larry.health.dashboard.partner` | KeepAlive | `dashboard.py` |
| `com.larry.health.dashboard` | KeepAlive | `dashboard.py` |
| `com.larry.health.import-poll` | каждые 5мин | `import_all.py` |
| `com.larry.health.import-watchdog` | каждые 2ч | `import_watchdog.py` |
| `com.larry.health.integrity-check.partner` | 07:50 | `run_checks.sh` |
| `com.larry.health.integrity-check` | 07:50 | `run_checks.sh` |
| `com.larry.health.lab-intake.partner` | KeepAlive | `lab_intake_watcher.py` |
| `com.larry.health.lab-intake` | KeepAlive | `lab_intake_watcher.py` |
| `com.larry.health.literature-read.partner` | 04:30 | `run_literature_pipeline.sh` |
| `com.larry.health.literature-read` | 04:30 | `run_literature_pipeline.sh` |
| `com.larry.health.literature-search.partner` | Вс 04:00 | `pubmed_searcher.py` |
| `com.larry.health.literature-search` | Вс 04:00 | `pubmed_searcher.py` |
| `com.larry.health.logrotate` | каждые 30мин | `log_rotate.py` |
| `com.larry.health.longitudinal.partner` | Вс 03:00 | `bash` |
| `com.larry.health.longitudinal` | Вс 03:00 | `bash` |
| `com.larry.health.multitenant-env` | RunAtLoad | `launchctl` |
| `com.larry.health.night-cycle` | 08:00 | `night_cycle.py` |
| `com.larry.health.night-repair` | 08:40 | `night_repair.py` |
| `com.larry.health.oura-import.partner` | 08:00; 11:00; 14:00; 17:00; 20:00 | `bash` |
| `com.larry.health.oura-import` | 08:00; 11:00; 14:00; 17:00; 20:00 | `bash` |
| `com.larry.health.owner-nag` | 08:00; 11:00; 14:00; 17:00; 20:00 | `owner_nag.py` |
| `com.larry.health.owner-weekly` | Пн 09:10 | `owner_weekly.py` |
| `com.larry.health.pgs-discovery.partner` | 5-го 03:00 | `pgs_discovery.py` |
| `com.larry.health.pgs-discovery` | 5-го 03:00 | `pgs_discovery.py` |
| `com.larry.health.pilot-shadow` | каждые 1ч | `pilot_shadow.py` |
| `com.larry.health.pipaudit` | Пн 03:30 | `pip_audit_check.py` |
| `com.larry.health.probes` | Пн 06:20 | `run_probes.sh` |
| `com.larry.health.profile-reconciler.partner` | 06:00 | `bash` |
| `com.larry.health.profile-reconciler` | 06:00 | `bash` |
| `com.larry.health.reminders-sync.partner` | каждые 3ч | `reminders_sync.py` |
| `com.larry.health.reminders-sync` | каждые 3ч | `reminders_sync.py` |
| `com.larry.health.survivorship.partner` | Пн 03:30 | `run_survivorship_pipeline.sh` |
| `com.larry.health.survivorship` | Пн 03:30 | `run_survivorship_pipeline.sh` |
| `com.larry.health.test-api-report` | 1-го 09:30 | `monthly_api_report.py` |
| `com.larry.health.test-suite` | 00:00 | `run_full_test_suite.sh` |
| `com.larry.health.triage` | 08:00 | `triage_agent.py` |
| `com.larry.health.uncommitted-watchdog` | каждые 1ч | `uncommitted_watchdog.py` |
| `com.larry.health.watcher` | KeepAlive | `watch_and_import.sh` |
| `com.larry.health.weekly-digest` | Сб 22:00 | `weekly_digest.py` |
| `com.larry.health.wellally-check` | Пн 10:00 | `check_wellally_updates.py` |

<!-- GEN:SCHEDULE:END -->


---

## ВНЕШНИЕ ИНТЕГРАЦИИ


СервисФайл секретаИспользованиеМодульAnthropic Claude API`~/.health_secrets/anthropic_key`AI, отчёты, чатhealth_ai, gp_agent, checkin_agent, genome\_\*Oura Ring API v2`~/.health_secrets/oura_token`Сон, ВСР, активностьimport_ouraTelegram Bot API`~/.health_secrets/telegram_token`Интерфейс пользователяtelegram_botTelegram Chat ID`~/.health_secrets/telegram_chat_id`Целевой чатtelegram_botPubMed E-utilitiesнет (публичный)Медицинская литератураpubmed_client[MyVariant.info](http://MyVariant.info)нет (публичный)Аннотация геномных вариантовgenome_annotatorGWAS Catalogнет (публичный)Trait-ассоциацииgenome_contextApple Healthexport XML (iCloud)Шаги, ЧСС, весimport_apple_healthmacOS Calendaricalbuddy (brew)События, поездкиcalendar_clientmacOS RemindersosascriptЗадачи пользователяtask_agent, reminders_sync

---


## DB SCHEMA (health.db — 25 таблиц)

### Основные данные
```
daily_metrics      (date PK, sleep_total, sleep_deep, sleep_rem, sleep_score,
                    hrv, resting_hr, readiness, steps, active_kcal, spo2_avg,
                    weight, vo2max, raw TEXT)

lab_results        (id, date, source, test_name, value, value_text, unit,
                    ref_low, ref_high, status, notes, created_at, event_id,
                    specimen, value_op, method)
                   идентичность строки = UNIQUE(date, test_name, source,
                   COALESCE(specimen,''), COALESCE(method,'')) — индекс idx_lab_uniq;
                   COALESCE не косметика: NULL != NULL отключил бы дедуп для строк
                   без метода, а таких большинство. `method` — что НАПЕЧАТАНО в
                   бланке (§17, нить loinc-name-home); дедуп решается в
                   lab_promote.split_groups, индекс — бэкстоп, а не судья.

context_events     (id, date, ts, source, category, key, value_num, value_text,
                    period_id, tags JSON)

periods            (id, name, type, start_date, end_date, tags JSON, source, notes, active)
```

### Геном
```
raw_snps           (rsid PK, chromosome, position, genotype)
                   -- сотни тысяч записей, импорт сырого файла генотипирования (формат 23andMe v5)

genetic_variants   (id, rsid UNIQUE, gene, genotype, significance, prev_significance,
                    conditions JSON, domain_tags JSON, effect_allele,
                    clinical_summary, annotation_source, annotated_at, updated_at)
                   -- только клинически значимые, аннотированные через myvariant.info + ClinVar

genome_update_log  (id, run_date, variants_checked, variants_changed,
                    changes_json, narrative, sent_to_user)
```

### AI / Intelligence
```
memory             (id, date, category TEXT, key, value JSON, confidence,
                    source, active, created_at, updated_at)
                   -- category: 'hypothesis' | 'protocol' | 'fact' | ...
                   -- ГИПОТЕЗЫ хранятся здесь как JSON с полями:
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

### Клиника / задачи
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

### Эксперименты / протоколы
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
                   -- визиты к врачу; заполняется из import_medical_docs.py
                   -- и при импорте новых медицинских PDF

### Чекины
```
checkins           (id, date, time_of_day: morning|evening, question, answer,
                    context, created_at)
checkins_fts       -- виртуальная FTS5 таблица для полнотекстового поиска
```

---

<!-- GEN:MODULE_REGISTRY:START -->

## МОДУЛЬНЫЙ РЕЕСТР
<!-- Авто-генерировано gen_blueprint.py 2026-10-01 -->

### СЛОЙ ДАННЫХ
```

health_db.py  # SQLite-слой для health системы.
  on_canonical_db() — Смотрит ли ЭТОТ процесс в боевую БД владельца.
  assert_not_canonical(purpose) — FAIL-CLOSED гард для операций, которым НЕЛЬЗЯ трогать боевое сост
  attach_reference(conn) — Подключить общий справочник к соединению как схему `ref`.
  get_conn(read_only) — Возвращает SQLite-коннект.
  init_db() — Создаёт схему если не существует.
  question_answer_gate_ddl() — DDL гейта «вопрос не закрывается пустотой» — ОДИН дом текста триг
  migrate_all_json() — Переносит все существующие JSON-файлы в SQLite.
  import_biochemical_json(filepath) — Импортирует один файл из папки biochemical/. Возвращает кол-во им
  import_all_biochemical() — Импортирует все файлы из папки biochemical/.
  log_repair(conn) — Записывает ОДНО изменение значения в журнал. Вызывать в той же тр
  revert_repairs(run_id) — Возвращает прежние значения по журналу прогона. Возвращает число 
  migrate_v2() — Создаёт новые таблицы архитектуры v2.
  get_absolute_thresholds_for_person(direction) — Правила порогов с причинами на языке человека (засеянный текст пе
  get_trend_thresholds_for_person() — Оконные правила с причинами на языке человека; хранилище — get_tr

agent_reports_db.py  # agent_reports_db.py — доменный модуль agent_reports. Вынесен из health_db.py (По
  save_agent_report(agent_type, agent_name, date_str, has_findings) — Сохраняет отчёт агента.
  get_agent_report(agent_name, date_str, n) — Последние N отчётов агента.
  get_reports_with_findings(date_from, agent_type) — Отчёты с находками за период — для сборки итогового отчёта. СВЕЖИ

alerts_db.py  # alerts_db.py — доменный модуль alerts. Вынесен из health_db.py (Поток C, strangl
  save_alert(type_, message, severity, source) — SX-1.8: insert into alerts. Возвращает id.
  get_active_alerts(source_like) — SX-1.8: list active alerts, опционально фильтр по source LIKE pat

assessments_db.py  # assessments_db.py — доменный модуль assessments. Вынесен из health_db.py (Поток 
  save_assessment_session(instrument_id, wording_version_hash, chat_id, task_id) — SX-1.8: create new assessment session row. Возвращает id.
  get_assessment_session(session_id) — SX-1.8: загрузить одну сессию по id.
  get_active_assessment_session(chat_id, instrument_id) — SX-1.8: найти активную (in_progress) сессию для chat_id, опционал
  update_assessment_session(session_id, answers_json, status, completed_at) — SX-1.8: update progress / status of assessment session.

checkins_db.py  # checkins_db.py — доменный модуль checkins. Вынесен из health_db.py (Поток C, str
  get_recent_checkins(n)
  save_checkin(day, question, answer, context)
  get_checkin_by_date(date_str, time_of_day) — Возвращает чекин по дате. time_of_day: 'morning' | 'evening' | No
  update_checkin_scores(day, time_of_day, stress_score, mood_score) — UPDATE последнего чекина за day+time_of_day с тремя scores.

config_db.py  # config_db.py — доменный модуль config. Вынесен из health_db.py (Поток C, strangl
  default_visit_specialist() — Дефолтный специалист для /visit — per-tenant из system_config, ин
  get_config(key, default, conn) — Читает параметр конфигурации из БД.
  get_routing_keywords() — Возвращает {domain: [keyword,...]}. Special key '__full__' содерж
  get_domain_signals(domain) — Возвращает domain-signals для evaluate_domain_need.
  get_doc_patterns() — Возвращает все паттерны классификации документов из БД.
  mark_imported(source_file, doc_type) — Помечает файл как импортированный. INSERT OR IGNORE — безопасен п
  get_imported_sources() — Возвращает множество полных относительных путей уже импортированн
  upsert_config(key, value_text, value_num, value_json) — Записывает или обновляет параметр конфигурации системы.
  ocr_languages(conn) — Языки OCR тенанта для `tesseract -l` (system_config `ocr.language
  doc_type_markers(conn) — Маркеры типа документа тенанта (system_config `docs.type_markers`
  marker_doc_type(markers, where, haystack) — Первый doc_type, чья подстрока (без учёта регистра) есть в haysta

constitutions_db.py  # constitutions_db.py — доменный модуль constitutions. Вынесен из health_db.py (По
  upsert_constitution(domain, body_md, title, source_version) — Записывает/обновляет нарратив конституции по домену (sleep|nutrit
  get_constitution(domain) — Возвращает {domain, title, body_md, generated_at, source_version}
  list_constitutions() — Список конституций без body_md: domain, title, generated_at, size

consult_sessions_db.py  # consult_sessions_db.py — доменный модуль consult_sessions. Вынесен из health_db.
  save_consultation_session(chat_id, session_dict) — Сохраняет (или обновляет) in-progress /consult сессию в БД.
  load_consultation_session(chat_id) — Загружает сохранённую сессию. Возвращает dict или None если нет.
  delete_consultation_session(chat_id) — Удаляет персистентную сессию (вызывается при /end или таймауте).

consultations_db.py  # consultations_db.py — приёмы врача. Вынесен из health_db.py (Поток C, strangler-
  specialty_key(s) — Одна специальность под разными словами сводится к общему ключу.
  get_consultations(n, specialist_type) — Последние N приёмов врача (из медкарты).
  get_last_consultation(specialist_type) — Последний приём врача, опционально по специальности (написания св
  consultation_exists(date_str, specialist_type) — Есть ли в медкарте приём этой даты и специальности — ключ идемпот
  save_consultation(date_str, specialist_type, specialist_name, platform) — Записывает приём врача в медкарту (events + encounters) — в тот ж

dashboard_db.py  # dashboard_db — низкоуровневый DB слой для FastAPI dashboard.

doc_reviews_db.py  # doc_reviews_db.py — доменный модуль doc_reviews. Вынесен из health_db.py (Поток 
  save_pending_doc_review(source_file, proposed_type) — Записывает документ в очередь на подтверждение типа.
  get_pending_doc_reviews() — Возвращает все записи со статусом needs_review.
  mark_doc_review_sent(review_id) — Помечает запись как 'уведомление отправлено' (ждём ответа пользов
  confirm_doc_review(review_id, confirmed_type) — Сохраняет подтверждённый тип, переводит статус в confirmed.
  reject_doc_review(review_id) — Помечает review как отклонённый (пользователь нажал Пропустить).
  auto_confirm_stale_reviews(hours) — Автоматически подтверждает pending-записи старше N часов.
  import_from_pending(pending_path) — Импортирует pending_labs JSON в lab_results после Telegram-подтве

events_db.py  # events_db.py — доменный модуль events. Вынесен из health_db.py (Поток C, strangl
  save_event(event_type, effective_date, status, performer) — Создаёт событие в events + subtable атомарно. Возвращает event_id
  get_events(n, event_type, status, date_from) — Последние N событий медкарты с деталями encounters/diagnostics.
  get_context_events(date_from, date_to, category, source) — Возвращает события контекста за период.
  save_context_event(date_str, source, category, key) — Сохраняет событие жизненного контекста.

experiments_db.py  # experiments_db.py — РЕТАЙР (BL-EXP-1, 2026-07-10).
  get_active_experiments()
  get_experiment_stats(exp_id)
  complete_experiment(exp_id, result, notes)
  log_experiment_day(exp_id, adhered, notes, day)
  start_experiment(name, hypothesis, intervention, start_date)
  update_experiment_check_results(exp_id, check_results_json)

field_reviews_db.py  # field_reviews_db.py — доменный модуль field_reviews. Вынесен из health_db.py (По
  confirm_field_alias(format_id, raw_name, canonical, source_example) — Подтверждает (или создаёт) alias. Проверяет orphan-статус.
  queue_field_reviews(format_id, source_file, unknown_fields) — Ставит список неизвестных полей в очередь ревью.
  get_pending_field_reviews(format_id) — Возвращает pending field reviews (опционально фильтр по format_id
  set_field_review_tg_message(review_id, tg_message_id) — Сохраняет Telegram message_id уведомления для reply-to-message вв
  get_field_review_by_tg_message(tg_message_id) — Находит pending field review по Telegram message_id уведомления.
  resolve_field_review(review_id, canonical) — Подтверждает (canonical is not None) или отклоняет field review.

genome_db.py  # genome_db.py — доменный модуль genome. Вынесен из health_db.py (Поток C, strangl
  get_significant_variants(domain, limit) — Возвращает клинически значимые варианты, опционально фильтруя по 
  get_carrier_variants(limit) — Верифицированные НОСИТЕЛИ: effect_allele_status='resolved' и effe
  carrier_status_null_allele_violations(conn) — Строки, нарушающие сцепление: carrier-статус ⟹ effect_allele запо
  get_variants_by_genes(genes, limit) — Возвращает варианты из genetic_variants по списку генов.
  genome_summary_counts() — Сводные счётчики для genome_context (Ф5). 'patho' = Pathogenic/Li
  get_snps_batch(rsids) — Возвращает {rsid: genotype} для списка rsID.
  upsert_genetic_variant(rsid, data) — Сохраняет или обновляет аннотированный вариант.
  save_genome_update_log(data) — Сохраняет лог месячного обновления генома.
  get_raw_snp(rsid) — Возвращает генотип из raw_snps по rsID.
  mark_genome_log_sent(log_id)

hae_db.py  # hae_db.py — доменный модуль hae. Вынесен из health_db.py (Поток C, strangler-фас
  get_hae_registry() — Возвращает {metric_name: row_dict} для всего реестра.
  upsert_hae_metric(metric_name, status, unit, sample_values) — Вставляет или обновляет запись в реестре.
  get_pending_hae_alerts(alert_cooldown_days) — Возвращает метрики которые нужно алертить: status='new' AND

hypotheses_db.py  # hypotheses_db.py — доменный модуль hypotheses. Вынесен из health_db.py (Поток C,
  save_hypothesis_outcome(memory_id, verdict, confidence, evidence) — Сохраняет новый outcome по гипотезе (история оценок, не upsert).
  get_hypothesis_outcome(memory_id) — Возвращает последний outcome для гипотезы или None.
  get_hypotheses_awaiting_evaluation() — Гипотезы в status='testing', чей ТЕКУЩИЙ круг не получил outcome.
  get_hypothesis_accuracy_stats() — Статистика точности по source (auto_consilium, manual, и т.д.).
  get_unsent_hypothesis_outcomes() — Возвращает outcomes, которые не были доставлены в Telegram.
  mark_hypothesis_outcome_delivering(memory_id, chat_id, outcome_id) — Отмечает что доставка началась (защита от двойной отправки при кр
  mark_hypothesis_outcome_sent(memory_id, outcome_id) — Отмечает что результат доставлен в Telegram.
  save_cbcr_payload(memory_id, payload_json, structural_score, confidence_level) — Записывает или обновляет CBCR-payload, привязанный к memory.id.
  get_cbcr_payload(memory_id) — Возвращает CBCR-payload (parsed JSON) для memory.id или None.

import_status_db.py  # import_status_db.py — доменный модуль import_status. Вынесен из health_db.py (По
  set_import_status(source, ts) — Записывает метку времени последнего успешного импорта источника.
  get_import_staleness(source) — Возвращает (is_stale, age_hours) для источника данных.
  check_data_freshness(sources) — Проверяет свежесть данных по всем или указанным источникам.
  get_conit_limit(source) — Возвращает лимит свежести для источника данных (часы).

labs_db.py  # labs_db.py — доменный модуль labs. Вынесен из health_db.py (Поток C, strangler-ф
  get_recent_labs(n_days, key_tests, exclude_pro) — Возвращает последний результат для каждого теста за последние n_d
  canon_window_note(n_days, exclude_pro, shown_tests, named_below) — Объявление границы окна лаб-блока: что показано и что лежит ЗА гр
  declared_boundary(n_days, unjudged) — `canon_window_note`, который НЕ бросает: отказ сборки сам станови
  get_lab_series(test_name, n_days, exclude_pro) — ВСЕ числовые результаты одного теста за n_days, хронологически, с
  get_modal_reference(test_name, min_docs) — Модальный (ref_low, ref_high) по РАЗНЫМ документам (source); None
  get_lab_trend(test_name, n) — Последние n результатов конкретного теста для построения тренда.
  trend_members(mappings, terms, our_name, unit) — Какие наши имена образуют ОДИН тренд с этим. Чистая: индекс аргум
  get_lab_trend_by_component(our_name, unit, specimen, n) — Тренд по ВЕЩЕСТВУ через отображение LOINC, а не по нашему имени.
  result_text(row) — Строка канона → человекочитаемый результат. Никогда не отдаёт сыр
  flag_direction(r) — Стрелка отклонения строки канона: пометка лаборатории, если напеч
  draw_summaries() — Сданные анализы коротко — по забору (draw_key: файл + дата), для 
  get_labs_by_date(date_str) — Все результаты за конкретную дату.
  get_lab_history(days) — Все лабные результаты за последние N дней, хронологически.
  compute_bank_refs(min_docs) — Референс каждого аналита = МОДА напечатанного интервала бланка по
  get_lab_refs() — Референсные интервалы: КЭШ моды бланков (system_config.lab_refs, 
  get_lab_refs_meta() — {test_name: n_docs} и дата расчёта — провенанс кэша референсов.
  get_effective_lab_schedule() — Возвращает {test_name: {interval_days, priority, source, note}} и
  build_lab_history_context(min_points, max_hist, horizon_days) — Полная лаб-история с пометкой давности по effective-сроку валидно
  unrepeated_draw(rows, cutoff) — ЧИСТАЯ логика блока. rows — строки канона (dict/Row) с полями
  build_unrepeated_draw_context(n_days) — Блок «сдано один раз и не пересдавалось» для контекстов LLM.
  draw_key(source, date_str) — conit-идентификатор забора для co-draw: (basename без doc:-префик
  build_codraw_context(days, min_cluster) — Срез по заборам за последние `days` дней (источник + дата).
  build_specialized_context(max_abnormal) — Сводка specialized_lab_results (микробиом/аутоантитела/метаболоми
  upsert_monitoring_rule(test_name, interval_days, priority, source) — Upsert расписания мониторинга. default не перебивает encounter/ma
  get_lab_format_by_name(name)
  get_lab_format_by_id(format_id)
  get_confirmed_aliases(format_id, conn) — Возвращает {raw_name: canonical} для confirmed aliases формата.
  confirmed_alias_conflicts(conn) — Сырые имена, которым РАЗНЫЕ форматы дали разные канонические имен
  confirmed_alias_targets(conn) — Множество канонических имён, на которые указывает подтверждённый 
  analyte_norm_verdicts(conn, today) — {канон аналита: verdict} из `analyte_norm_verdicts` — только ЖИВЫ
  set_analyte_norm_verdict(analyte, verdict, rationale, oracle) — Записать/заменить вердикт о норме аналита. Имя нормализуется чере
  domain_verdicts(conn) — {класс строки: дом} из `lab_domain_verdicts`. Дом ∈ {canon, speci
  domain_home(panel_type, conn) — Дом класса по вердикту человека. None = вердикта нет, решать не м
  get_confirmed_aliases_all(conn) — {raw_name.lower(): canonical} по ВСЕМ форматам — глоссарий для пр
  get_all_canonical_names() — Все уникальные canonical-имена в lib (для fuzzy-matching).
  get_all_lab_dates() — Все даты, по которым есть анализы.
  effective_freshness(active_conds) — Расписание свежести для тенанта: BASE + условные тесты его активн

memory_db.py  # memory_db.py — доменный модуль memory. Вынесен из health_db.py (Поток C, strangl
  get_memory(category, n, active_only)
  save_memory(category, value, key, confidence) — Сохраняет факт в память. Если key задан — обновляет существующую 

metrics_db.py  # metrics_db.py — доменный модуль metrics. Вынесен из health_db.py (Поток C, stran
  get_day(day_str)
  get_window(end, days)
  get_stats(days, end) — Агрегированная статистика за период.
  get_metric_percentiles(baseline_days) — Возвращает перцентильный ранг вчерашних ключевых метрик
  upsert_metrics_from_json(day_str, data, source) — W5K-B (2026-05-14): per-source ownership.
  build_context(target) — Собирает компактный контекст (~2000 токенов) для генерации отчёта
  metric_columns(conn) — Колонки daily_metrics, которые являются показателями (всё, кроме 
  render_all_metrics(days, end) — Блок «все собранные показатели» для любого врачебного контекста.
  data_sources(days, end) — Источники, от которых за окно реально пришли данные (по колонкам-

periods_db.py  # periods_db.py — доменный модуль periods. Вынесен из health_db.py (Поток C, stran
  historical_periods(exclude_types, include_deleted) — All periods (past + current + future), default excludes soft-dele
  current_periods(date_str) — Periods, активные на дату (default: today). Excludes soft-deleted
  get_active_period(date_str) — DEPRECATED (2026-06-19): use current_periods().
  save_period(name, type_, start_date, end_date) — Создаёт новый период (active=1, deleted_at=NULL по дефолту).
  future_periods() — Periods starting in the future. Excludes soft-deleted.
  soft_delete_period(period_id, reason) — Mark period as soft-deleted (sets deleted_at=now()).

problems_db.py  # problems_db.py — доменный модуль problems. Вынесен из health_db.py (Поток C, str
  get_problem_list(status) — Возвращает problem list, опционально фильтруя по статусу.
  upsert_problem(problem_id, title, description, status) — Создаёт или обновляет проблему в problem list.
  proposal_dedup_key(ch, source, conn) — Идентичность правки (2026-09-01, решение владельца: «одна правка 
  existing_problem_for(ch, conn) — Проблема медкарты, которую предложение «добавить» ДУБЛИРУЕТ: жива
  save_problem_proposal(source, changes, conn) — Сохраняет предложения по изменению problem list — ОДНА ПРАВКА = О
  record_surveillance_decision(topic, title, decision, decided_by) — Записать решение врача/владельца по теме наблюдения. Прежнее акти
  active_surveillance_decisions(on_date) — Действующие решения: не retired и (valid_until IS NULL или ≥ on_d
  format_surveillance_decisions(decs) — Строки для промпта (GP, куратор): одна на решение, с автором, сро

profile_db.py  # profile_db.py — доменный модуль profile. Вынесен из health_db.py (Поток C, stran
  get_patient_profile(category) — Возвращает профиль пациента как словарь key→value.
  profile_fields() — Поля профиля, которые человек сообщает о себе сам: key → специфик
  apply_stated(name, raw, source) — Записать в профиль то, что человек сказал о себе, — если у сказан
  upsert_profile(key, value_text, value_json, category) — Записывает или обновляет поле профиля пациента.
  get_profile_context() — Читает профиль пациента. DB-first (patient_profile), fallback на 

proposals_db.py  # proposals_db.py — доменный модуль proposals. Вынесен из health_db.py (Поток C, s
  get_pending_proposals() — Возвращает все неподтверждённые пропозалы.
  get_undelivered_proposals() — Ждущие решения человека и ещё не доставленные ему (delivered_at I
  stuck_undelivered_proposals() — (ждут и не доставлены всего, id застрявших дольше двух ритмов out
  mark_proposal_delivered(proposal_id, tg_message_id) — Квитанция доставки — ставится ПОСЛЕ успешной отправки (отказ оста
  format_proposal_card(prop) — Текст карточки предложения — ПРОСТОЙ текст, без разметки. Один до
  apply_proposal(proposal_id) — Применяет пропозал: вносит изменения в problem_list.
  reject_proposal(proposal_id, note) — Отклоняет пропозал без изменений в problem_list.
  expire_aged_proposals(days) — Lifecycle: pending старше порога → 'expired'. Возврат — сколько и

protocols_db.py  # protocols_db.py — доменный модуль protocols.
  get_active_protocols(domain)
  save_protocol(protocol) — Сохраняет протокол в таблицу protocols.
  retire_protocol(protocol_id, note) — Снимает протокол (status → retired).

rules_db.py  # rules_db.py — доменный модуль rules. Вынесен из health_db.py (Поток C, strangler
  get_absolute_thresholds(direction) — Возвращает активные абсолютные пороги. Опциональный фильтр по dir
  get_trend_thresholds() — Активные трендовые/оконные пороги (N дней подряд / скользящее сре
  get_lab_trend_thresholds() — Активные пороги ЛАБОРАТОРНЫХ трендов (safety-net-thresholds, §9-в
  get_threshold(metric, direction, band_label, variant) — §9: единственный рантайм-источник клинической нормы — таблица abs
  get_active_constraints(protocol_id) — SX-1.8 (2026-05-18): now reads from `alerts` table.

tasks_db.py  # tasks_db.py — доменный модуль tasks. Вынесен из health_db.py (Поток C, strangler
  save_task(source, type_, content, priority) — Сохраняет задачу в базу.
  resolve_task(task_id, resolved_text, status) — Закрывает ОТКРЫТУЮ (или отложенную) задачу. True — закрыта; False
  get_open_tasks(limit) — Возвращает незакрытые задачи, отсортированные по приоритету.
  get_overdue_tasks(days_old) — Задачи которые открыты дольше N дней без ответа.
  get_unsent_tasks(type_) — Outbox задач одного типа: открытые, с sent_at IS NULL, старшие пе
  get_questions_needing_delivery() — Открытые вопросы, на которые НЕКУДА ответить: нет обратного адрес
  get_open_questions() — ВСЕ открытые вопросы, доставленные и нет — множество, против кото
  get_task_by_tg_message(tg_message_id) — Открытая задача, к сообщению которой прилетел реплай.
  get_unsent_assessment_tasks() — Outbox опросников: открытые задачи type='assessment' с sent_at IS
  wake_snoozed_assessment_tasks(today) — Отложенный опросник, чей срок настал, снова ложится в outbox: sta
  mark_task_sent(task_id, tg_message_id) — Помечает что задача отправлена в Telegram.

treatment_db.py  # treatment_db.py — доменный модуль treatment. Вынесен из health_db.py (Поток C, s
  get_medications(confirmation, include_proposed) — Список режимов лечения. По умолчанию только канонические (гейт пр
  upsert_medication(name, modality, intent, cycles_completed) — Идемпотентный upsert режима лечения в medications.
  get_episodes(status, problem_id) — Возвращает эпизоды, опционально фильтруя по статусу и/или проблем
  set_medication_confirmation(med_id, confirmation) — Человек-гейт: подтвердить/отклонить извлечённый режим.
  get_proposed_medications() — Режимы, ждущие подтверждения человеком (для гейт-карточек).
  get_unsent_proposed_medications() — Proposed-режимы, по которым ещё НЕ отправлена карточка гейта.
  mark_medication_gate_sent(med_id) — Помечает, что карточка подтверждения режима отправлена (не слать 
  save_episode(title, start_date, primary_problem_id, end_date) — Создаёт эпизод помощи. Возвращает episode_id.
  complete_planned_event(event_id, interpreted_report, abnormal_flags) — Переводит planned event → completed. Обновляет diagnostic_events 

workouts_db.py  # workouts_db.py — доменный модуль workouts. Вынесен из health_db.py (Поток C, str
  upsert_workout(date_str, workout) — Записывает тренировку. Дедуплицирует по дате + start_time.

```

### СЛОЙ ИНТЕЛЛЕКТА
```

health_ai.py  # health_ai — публичный интерфейс intelligence layer.

hai_core.py
  get_client()
  get_model(role) — Модель для роли. system_config.model.<role> > MODEL_DEFAULTS.
  model_for(task_role) — Модель для семантической роли задачи (ROLE_MODELS → get_model(tie
  answer_language(lang) — Язык ответа модели = язык человека (нить model-lang, 28.09).
  with_answer_language(build) — Декоратор сборщика system-промпта: дописывает answer_language() в
  get_system_prompt()
  save_message(role, content)
  get_history(n)
  wrapped()

hai_context.py  # hai_context — контекст-блоки, tool execution, smart context.
  build_context_block_compact(target) — Компактный блок данных (~500 токенов) для подстановки в чат-запро
  build_smart_context(question, target) — Проактивный контекст: анализирует вопрос, загружает нужные данные
  fmt_sleep(d)

hai_analysis.py  # hai_analysis — индекс восстановления, детекция дрейфа метрик.
  compute_recovery_index(target, window_days, conn) — Индекс восстановления: сравнивает текущие показатели с доболезнен
  recovery_baseline(conn) — Средние тенанта за его окно baseline; см. _baseline_window.
  recovery_series(target, days, window_days) — Trailing композиты (НОВЕЙШИЙ первым) за `days` дней — ряд для лич
  format_recovery_index(ri) — Текстовая строка для инжекта в контекст GP:
  detect_metric_drift(target, window, streak_threshold) — Детектирует устойчивый дрейф метрик относительно личного rolling 
  format_drift_report(drifts) — Краткий текст для инжекта в контекст GP.
  detect_correlation_drift(target, window, baseline_window, threshold_r) — Детектирует пары метрик с резко изменившейся корреляцией.
  format_correlation_drift_report(drifts) — Краткий текст для инжекта в контекст GP. См. C-4 (P5-future).

hai_hypotheses.py  # hai_hypotheses — гипотезы, протоколы, форматирование.
  save_hypothesis(observation, mechanism, prediction, test) — Сохраняет гипотезу в memory (category='hypothesis').
  get_open_hypotheses(n) — Возвращает активные (status != confirmed/rejected) гипотезы из me
  hypothesis_verdict(memory_id) — Вердикт гипотезы: 'confirmed' | 'rejected' | None (открыта/не най
  update_hypothesis_status(memory_id, status, note) — Обновляет статус гипотезы (open → testing → confirmed | rejected)
  append_dynamics_to_hypothesis(memory_id, note) — этап2: добавляет запись динамики серии в гипотезу (поле dynamics[
  generate_hypothesis_from_drift(drifts, linked_experiment_id) — W5H-B (2026-05-14): defunct.
  confirm_hypothesis(memory_id) — Подтверждает гипотезу (status → confirmed), возвращает payload.
  reject_hypothesis(memory_id, reason) — Отклоняет гипотезу (status → rejected).
  notice_keyboard(memory_id, lang) — Кнопки уведомления о новой гипотезе — один дом для обоих путей (г
  get_specialist_hypotheses(n) — Открытые гипотезы с resolution_type=needs_specialist.
  generate_protocol_from_hypothesis(hypothesis) — LLM генерирует поведенческий протокол из подтверждённой гипотезы.
  save_protocol(protocol) — Сохраняет протокол в таблицу protocols.
  get_active_protocols() — Возвращает все активные протоколы.
  retire_protocol(protocol_id, note) — Снимает протокол (status → retired).
  format_hypotheses_for_gp(hypotheses) — Краткий блок для инжекта в контекст GP — активные гипотезы.
  generate_hypothesis_from_correlation(correlation_drifts, linked_experiment_id) — W5H-B (2026-05-14): defunct.

hai_reports.py  # hai_reports — утренний, еженедельный, ежемесячный отчёты + расширенный контекст-
  build_context_block(target) — Полный блок данных для генерации утреннего отчёта.
  generate_morning_report(target) — Генерирует утренний отчёт через Claude Sonnet.
  generate_weekly_report(end_date) — Еженедельный отчёт с MDT WellAlly + PubMed доказательной базой.
  generate_monthly_check(target) — Ежемесячный отчёт с проверкой витальных метрик и задачами.
  fmt_sleep(d)
  trend_arrow(v7, v90)
  pct(a, b)

hai_chat.py  # hai_chat — chat(), run_arbiter(), CLI.
  build_chat_payload(user_message, include_data) — Единая сборка входа модели → (system_prompt, messages). Инспектир
  assembled_context_text(user_message, include_data) — Плоская строка ВСЕГО контекста, что видит модель (system + все хо
  chat(user_message, include_data) — Claude сам запрашивает нужные данные через tool use.
  chat_with_image(caption, image_bytes, mime_type) — Анализирует изображение в контексте здоровья пользователя.
  run_arbiter(user_message, assistant_reply, unverified) — Анализирует пару user↔assistant и сохраняет артефакты.
  judge_service_trouble(user_message, assistant_reply) — Судья гипотезы «человеку плохо от системы» (§17: НЕ тот вызов, чт

```

### ГЕНОМ
```

genome_parser.py  # genome_parser.py
  parse_tsv(filepath) — Чистый парсер 23andMe TSV → list of {rsid, chromosome, position, 
  import_raw_genome(filepath, force, allow_identity_change) — Читает raw 23andMe TSV, импортирует в raw_snps.

genome_annotator.py  # genome_annotator.py
  tag_domains_keywords(gene, conditions, significance) — Детерминированный keyword-маппинг доменов. Быстро, без API.
  fetch_myvariant_batch(rsids) — Запрашивает аннотации для батча rsID через myvariant.info POST.
  parse_clinvar(item) — Извлекает клинически значимые поля из ответа myvariant.
  extract_ref_alts(hits) — ClinVar-first извлечение (ref, alts) для ОДНОГО rsid из хитов myv
  annotate_all(limit_rsids) — Главная функция: берёт все rsID из raw_snps,
  annotate_rsids(rsids) — Аннотирует конкретный список rsID (для ad-hoc запросов из /genome

genome_context.py
  build_genetic_context_block(domain, max_variants) — Возвращает текстовый блок с клинически значимыми вариантами
  answer_trait_question(question) — Отвечает на вопрос о предрасположенности.
  build_lifestyle_genome_block(domain, max_variants, only_genes) — Компактный геномный блок для lifestyle-агентов.

genome_update_agent.py
  fetch_clinvar_updates(variants) — Перепроверяет список вариантов через myvariant.info.
  generate_genome_narrative(changes, genotype_map) — GP генерирует нарратив по значимым изменениям.
  run_monthly_update() — Главная функция ежемесячного обновления.

```

### GP И СПЕЦИАЛИСТЫ
```

gp_agent.py
  run_specialists_and_save(end_date, period_days) — Запускает MDT специалистов и сохраняет результат в agent_reports.
  generate_weekly_report(end_date, run_mdt) — Еженедельный GP отчёт.
  generate_monthly_report(end_date) — Ежемесячный GP отчёт — стратегический взгляд, 30/90 дней.
  compute_step_target(activity_date) — Детерминированная рекомендация по шагам на день.
  generate_daily_report(target, gate_sink) — Ежедневный утренний отчёт: lifestyle-агенты → GP синтез.
  run_experiment_checks(target) — Проверяет активные эксперименты: если сегодня = start_date + chec
  generate_attribution_report(experiment_id) — Финальный attribution report по завершении эксперимента.
  fmt_row(label, mkey)

gp_context.py  # gp_context.py — сборка текстового контекста для отчётов GP.
  absent_claims_contradicted(report, last_dates, window_days) — Фразы отчёта «X не сдавался», где X — аналит со строкой в каноне.
  judge_absence_claims(text, window_days) — Судья отсутствия для ЛЮБОГО тракта: (хиты, готовая поправка одной
  recommendations_without_evidence(text, last_dates, schedule) — Рекомендации сдать аналит, у которого строка СВЕЖЕЕ окна монитори
  annotate_lab_recency(text) — Дописывает к тексту задачи дату последней сдачи аналитов, которые

wellally_consult.py
  class ConsultationRound
  class ConsultationSession
  async run_consultation_cycle_async(user_message, session, end_date, period_days) — Один цикл диалоговой консультации.
  async run_mdt_consultation_async(end_date, period_days, specialists, user_question) — Legacy single-shot consultation (для совместимости).
  run_mdt_consultation(end_date, period_days, specialists) — Синхронная обёртка для скриптов и cron.
  to_dict()
  from_dict(d)

lifestyle_agents.py  # lifestyle_agents.py — 4 lifestyle specialists для ежедневного утреннего отчёта.
  class LifestyleAgent
  class SleepAgent
  class MovementAgent
  class StressAgent
  class EnergyAgent
  run_lifestyle_agents(sleep_date, activity_date) — Запускает все lifestyle-агенты.
  has_data(day)
  generate_brief(day, stats7, stats30, target)
  get_mdt_data_brief(sleep_date, activity_date) — Возвращает текстовый бриф для MDT-пакета данных. Использует сущес
  async generate_mdt_opinion(sleep_date, activity_date, patient_question, round_a_opinions) — MDT-участие через Claude API.
  run(sleep_date, activity_date) — Возвращает текст брифа если данные есть, None если нет.

safety_net.py  # safety_net.py — детерминированный safety net.
  check_lab_alerts(reference_date) — Проверяет последние лабораторные данные против порогов.
  check_lab_trends(reference_date) — Проверяет направленные тренды в лабораторных данных.
  check_lifestyle_alerts(target) — Проверяет lifestyle-метрики против абсолютных и относительных пор
  person_urgent_message(urgent, max_level, data_doubt) — Срочное сообщение человеку: заголовок, строки показателей, что де
  run_safety_net(target) — Запускает все проверки.
  cannot_judge_open(conn, today) — Открытые «судить нечем» тенанта по его соединению (read-only годи

```

### ЗАДАЧИ И ЧЕКИНЫ
```

task_agent.py
  extract_tasks_from_report(report_text, source, source_date, report_date) — Парсит GP отчёт, извлекает задачи, сохраняет в DB.
  format_tasks_message(tasks) — Форматирует список задач для Telegram.
  format_open_tasks_message(tasks) — Форматирует список открытых задач (для weekly follow-up).
  resolve_task_by_number(task_list, number, resolved_text) — Закрывает задачу по порядковому номеру в списке.
  process_gp_report(report_text, report_type, report_date) — Полный цикл: извлечь задачи → создать ремайндеры → форматировать 
  summary_tasks(tasks)
  get_weekly_followup() — Для weekly follow-up в воскресенье:
  create_macos_reminder(task) — Создаёт задачу в macOS Reminders (список Health).
  create_reminders_for_tasks(tasks) — Создаёт ремайндеры для списка задач. Возвращает количество создан
  record_answer(task_id, answer_text, source) — Принять ответ на задачу-вопрос: закрыть задачу и доставить ответ 
  addressed_to_patient(items) — Единый судья «это вопрос К ЧЕЛОВЕКУ»: {id: {verdict, reason, ru}}
  promote_memory_questions(limit) — Поднимает вопросы из памяти в задачи, которые реально задаются че
  should_ask_again(fingerprint) — Задавать ли вопрос заново, если на него уже отвечали.
  complete_macos_reminder(task_id) — Помечает ремайндер в macOS как выполненный по task_id в теле заме
  esc(s)

checkin_agent.py
  generate_opening_question() — Генерирует первый вечерний вопрос с учётом контекста дня.
  continue_checkin(conversation) — Продолжает разговор чекина.
  finalize_checkin(conversation) — Извлекает структурированные данные из завершённого разговора.
  class CheckinState
  start(opening_question)
  add_user(text)
  add_assistant(text)
  should_force_end()
  is_stale(ttl_hours) — True если чекин завис без завершения дольше ttl_hours часов.
  reset()

reminders_sync.py  # reminders_sync.py — синхронизация macOS Reminders → SQLite.
  reminders_list_name() — Owner keeps Health; other tenants use their data-directory suffix
  get_completed_task_ids() — Читает выполненные напоминания из списка текущего тенанта через A
  sync_completed_reminders()

```

### ИМПОРТ ДАННЫХ
```

import_oura.py  # Импорт данных Oura Ring через API v2.
  get_token()
  oura_get(endpoint, start, end) — W5K-A2 (2026-05-14): follow next_token пагинацию до конца.
  parse_sleep(sessions, scores) — Возвращает dict[date_str → sleep_summary].
  parse_readiness(data)
  parse_activity(data)
  parse_spo2(data) — daily_spo2: spo2_percentage.average (число %), breathing_disturba
  parse_hrv(sessions) — HRV из ночных сессий — берём average_hrv из long_sleep.
  parse_stress(data) — daily_stress: day_summary, stress_high (сек), recovery_high (сек)
  parse_resilience(data) — daily_resilience: level + contributors.
  parse_workouts(data) — workout: список тренировок с датой.
  load_day(d)
  save_day(d, data)
  import_oura(start, end)

import_apple_health.py  # Health Auto Export JSON importer.
  historical_export() — Самая большая полная выгрузка HealthAutoExport в облачной папке у
  parse_hae_date(date_str) — Parse HAE date string '2026-01-01 08:00:00 +0000' → date object.
  aggregate_metric_by_day(metric_name, entries, daily) — Aggregate metric entries into daily buckets.
  process_hae_json(filepath) — Parse a Health Auto Export JSON file.
  save_daily_summaries(daily, mode) — Save daily summaries to HEALTH_DATA/YYYY-MM-DD.json.
  import_historical() — Import the historical JSON export.
  import_daily_new_automation(archive) — Import new daily JSON files from HAE New Automation folder.
  print_summary() — Print recent daily summaries to verify import.

import_hr_activity.py  # Импортирует heart_rate и active_energy из HealthAutoExport JSON в daily_metrics.
  load_metric(metrics, name)
  aggregate_by_day(data_points, value_keys) — Агрегирует точки по дате (среднее), пробует разные ключи для знач
  run()

import_medical_docs.py  # Импортирует клинические данные из прочитанных PDF в health.db:
  save_consultations() — Сохраняет исторические консультации в медкарту (events + encounte
  run()

import_all.py  # WellAlly-Health: полный рекурсивный импорт всей папки health/
  is_financial(path)
  pdf_to_images(pdf_path)
  ocr_image(img_path)
  ocr_pdf(pdf_path)
  extract_text(pdf_path) — Извлекает текст из PDF. Для digital PDF использует fitz (быстро, 
  classify(path, text)
  parse_date(text, filename) — Дата документа — от надёжного признака к угадыванию:
  parse_lab_values(text)
  is_synevo_format(text) — Детектирует многострочный формат Synevo по грузинским символам в 
  parse_synevo_values(pdf_path) — Парсер Synevo-PDF через fitz blocks.
  save(path, data)
  make_record(pdf_path, text, date, doc_type)
  consult_conclusion(text, limit) — Вывод документа приёма для медкарты: от раздела «Summary/Impressi
  save_clinical_to_db(doc_type, date, pdf_path, rec) — Сохраняет клинически значимые документы приёмом в медкарту (event
  apply_confirmed_type(source_file, doc_type) — Применяет подтверждённый тип документа из Telegram doc-review.
  main()

calendar_client.py  # calendar_client.py — читает события из JSON-кэша Google Calendar.
  is_birthday(e)
  format_calendar_context(days) — Форматированный блок событий для AI-контекста.
  get_travel_events(days)
  parse_events(raw)

```

### АВТОДОКУМЕНТАЦИЯ
```

doc_agent.py  # doc_agent.py — авто-документирование изменений в Larry Health OS.
  notify_telegram(text) — Совместимый вход для сведений: одна строка в понедельничную сводк
  get_last_commit_diff(max_lines) — (commit_message, diff_text) для последнего коммита.
  thread_changelog_row(slug, subjects, day, version) — Строка таблицы журнала за нить. None — в нити не было работы кода
  bump_version(version) — 15.28 → 15.29: шаг последнего разряда, как в журнале с июля.
  record_thread_merge(merge_sha, slug, root) — Записать строку журнала за слитую нить и застейджить журнал. Возв
  get_current_version()
  get_changelog_tail(n) — Последние N строк таблицы CHANGELOG.md для контекста.
  get_last_sec_number()
  analyze_diff(commit_msg, diff, description) — Вызывает Claude и получает структурированное описание изменений.
  apply_changelog_entry(entry, new_version, dry_run)
  apply_security_entry(entry, dry_run)
  install_hook()
  regenerate_intent_page(entry_id, dry_run) — Регенерит docs/explanation/<id>.md из записи реестра. G2 (status-
  translate_intent_page(entry_id, dry_run, notify) — Пишет <page>.en.md рядом с русской тёплой страницей. Русскую меня
  translate_page(page) — Общий путь перевода: форма и stamp прежние; статус замысла провер
  regenerate_install_page(page, dry_run) — Исправляет только противоречия install_facts; проверяет ответ ДО 
  translate_all_pages(dry_run) — Переводит тёплые страницы, у которых перевода нет или он устарел.
  regenerate_stale_pages(dry_run) — Регенерит все страницы с устаревшим провенансом (реестр ушёл впер
  main()
  extract(tag)

run_checks.sh

integrity_tests.py  # integrity_tests.py — проверка целостности системы Larry Health OS.
  check(label, fn, critical, repo_only) — repo_only (docker-install, этап 6): предмет проверки — РЕПОЗИТОРИ
  warn(label, detail)
  fail_(label, detail) — Прямой FAIL без обёртки в check().
  check_data_freshness()
  check_db_integrity() — PRAGMA integrity_check per-tenant — B-tree corruption и out-of-or
  check_data_window()
  check_steps_visible_via_get_day() — Консистентность шагов: если steps есть в flat daily_metrics.steps
  check_core_metrics()
  check_stats_non_empty()
  check_gp_reports()
  check_report_content()
  check_problem_list()
  check_problem_plain_summary() — Открытая проблема несёт описание простыми словами (27.09, волна 4
  check_protocols()
  check_tasks_pipeline()
  check_recent_task_activity()
  check_question_answer_integrity() — Вопрос, закрытый без ответа; недоставленный вопрос; ответ без сле
  check_question_queue_drains() — Очередь вопросов обязана СЛИВАТЬСЯ за время, которое сама себе на
  check_proposals_delivered() — Предложение GP по списку проблем ждёт решения человека, но до нег
  check_question_candidates_not_discarded() — Кандидат выпал за окно, НИ РАЗУ не побывав у судьи.
  check_llm_answer_parsed() — Ответ модели, который не удалось разобрать ВООБЩЕ, обязан быть ви
  check_reco_repeats_fresh_lab() — Текст советует сдать анализ, который уже сдан СВЕЖЕЕ окна монитор
  check_absence_surface_guarded(root) — Тракт с ПОВЕРХНОСТЬЮ для утверждения об отсутствии либо под сторо
  check_question_answers_reach_doctor() — Квитанция в точке потребления: свежий ответ ВИДЕН в собранном GP-
  watched_missing_from_review(rows, ctx) — Проблемы со статусом наблюдения, которых нет строкой в СВОЕЙ секц
  check_watched_problems_reach_review(rows, ctx) — Каждая проблема со статусом наблюдения видна в контексте недельно
  check_build_context()
  check_lab_freshness() — Анализы не старше LAB_ALERT_DAYS (9 мес = тревога, 6 мес = предуп
  check_lab_canon_health() — Канон lab_results: нет физиологически невозможных значений.
  check_lab_single_writer() — Инвариант единственного писателя канона (BL-LAB-CANON-1, Primary-
  promotion_backlog(canon_rows, staging_rows, today_) — Чистая логика датчика затора: сколько строк ждут промоута и сколь
  check_promotion_backlog_stale() — Труба распознавания не должна забиваться на ВЫХОДЕ молча.
  disjoint_reference_ranges(rows) — Чистая: ключи (имя, единица, материал), под которыми лежат НЕПЕРЕ
  check_loinc_decisions_possible() — Записанное решение не смеет ссылаться на код, невозможный для это
  check_lab_names_not_glued() — Разные аналиты под одним именем — тихая порча тренда.
  unitless_offenders(rows, fence) — Чистая логика датчика: строки канона МОЛОЖЕ фенса, где число есть
  ref_scale_mismatch(rows) — Чистая логика: значение в одной шкале, а его референс — в другой.
  check_lab_ref_scale() — Референс обязан быть в той же шкале, что значение.
  check_canon_rows_have_a_result() — В каноне нет строк без результата — ни числа, ни текста. Оба тена
  multidate_offenders(rows) — Чистая часть: [(источник, имя, материал, дата)] → ключи с более ч
  check_canon_one_date_per_analyte_in_doc() — Внутри ОДНОГО документа один аналит в одном материале не может им
  check_lab_unit_present() — Число без шкалы непригодно для клинического вывода — а канон его 
  specimen_provenance_offenders(rows) — Чистая часть датчика (как `unitless_offenders` и `ref_scale_misma
  check_staging_specimen_provenance() — Материал БЕЗ своей ступени неотличим от назначенного — а именно э
  censored_value_offenders(rows) — Чистая часть: (value, value_op) → нарушения. Оператор без числа б
  check_censored_values_coherent() — Оператор сравнения и число живут ТОЛЬКО вместе.
  check_reference_tables_not_in_canon() — У справочника LOINC ОДИН дом — общий `loinc.db`. Копия в каноне —
  check_specialized_lab_health() — specialized_lab_results (BL-LAB-CANON-2): целостность спец-панеле
  specialized_canon_waiting(rows, canon_keys) — Канон-ждущие строки спец-слоя по идентичности ИМЯ+МАТЕРИАЛ+РАЗМЕР
  check_specialized_canon_waiting() — Спец-слой — комната ожидания канона: строка с вердиктом дом=канон
  canon_naming_debt(rows, canonicals) — Имена строк канона из ДОКУМЕНТОВ, которых канон не знает. Чистая 
  check_canon_naming_debt() — Назывной долг канона не растёт: строка из документа живёт под сво
  check_unrepeated_draw_not_swamping() — Исторический блок не перерастает текущую лабораторную картину.
  literature_partner_gate(approved, partner_plist_exists) — Чистая логика гасителя: пора ли возвращаться к партнёрскому крану
  partner_tap_present(agents_dir, repo_launchd, in_container, name) — Заведён ли партнёрский кран. Установленный плист — где служба исп
  check_literature_partner_gate() — Отложенное решение владельца возвращается по СОБЫТИЮ, а не по сро
  electrophoresis_offenders(rows, tol) — Чистая логика: электрофорез белка обязан сходиться сам с собой.
  check_electrophoresis_sums() — Электрофорез сходится сам с собой (см. `electrophoresis_offenders
  check_promote_conflicts() — Измерения, которые промоут ОТКАЗЫВАЕТСЯ класть в канон, обязаны б
  check_lab_no_billing_rows() — Канон lab_results НЕ содержит биллинг-мусор из инвойсов (#67, 202
  check_pgs_reference() — Reference-БД полигенных весов (pgs_catalog+pgs_weights) вынесена 
  check_genome_update() — genome_update_agent не старше GENOME_UPDATE_ALERT_DAYS.
  check_daemons_alive() — KeepAlive-демоны должны иметь живой PID. Закрывает класс «сервис 
  check_stderr_watch_blindness() — Не ослеп ли сам датчик stderr. ОБЯЗАН стоять ДО check_stderr_erro
  check_stderr_errors() — Ошибки, ДОПИСАННЫЕ в stderr-логи джоб с прошлой проверки.
  check_deploy_restart_completeness() — Ратчет полноты рестарта: множество долгоживущих джоб == множество
  check_tenant_count_vs_silence_decision() — Счётчик под решением «молчание человека признаком не считать» (§1
  check_plist_env_consistency() — WARN: owner-джоба с HEALTH_DATA_DIR в плисте, но БЕЗ него в загру
  check_launchd_inventory() — WARN: копия плиста в репозитории разошлась с живой, либо джоб без
  check_arbiter_liveness() — WARN: экстрактор памяти (арбитр) отстал от СВЕЖИХ сообщений. Клас
  check_reschedule_liveness() — WARN: 12ч-job пересчёта местного пояса брифа (reschedule_local) м
  check_consolidation_delivery_liveness() — WARN: делверинг-джоб disagree/supersede (run_nightly_consolidatio
  check_promote_questions_liveness() — WARN: подъёмник кандидатов в вопросы мог тихо умереть.
  check_watchdog_liveness() — WARN: §14 terminus — сам uncommitted_watchdog мог тихо умереть (l
  check_macbook_uncommitted() — НЕЗАКОММИЧЕННАЯ РАБОТА НА MacBook — судится отсюда, по снимку, а 
  check_macbook_head_deployed() — КОД С MacBook НЕ ДОЕХАЛ ДО STUDIO — и об этом никто не сказал (27
  threads_in_snapshot(threads_field) — Разбор поля `threads=` снимка MacBook — ОДИН дом формата для двух
  thread_copies_missing(threads_field, have, now_ts, grace_h) — Чистая/тестируемая: какие ветки нитей старше grace_h НЕ доехали с
  check_thread_work_has_a_second_copy() — РАБОТА НИТИ В ОДНОМ ЭКЗЕМПЛЯРЕ — вопрос ПОСТРАДАВШЕГО, не строите
  threads_without_index_row(threads_field, index_text, now_ts, grace_h) — Чистая/тестируемая: нити, чья ветка жива дольше grace_h, а строки
  check_threads_have_index_row() — НИТЬ-СИРОТА: ветка жива, а в реестре нитей её нет — её не видит н
  check_captured_files() — ЗАХВАТ ЧУЖОГО ФАЙЛА В КОММИТ — вопрос человеку, не вердикт машины
  check_git_hooks_executable() — Активный хук без бита исполнения = ВСЕ гейты выключены разом, и м
  check_dispgate_liveness() — §14 для гейта одноразовости (§15): он мог перестать защищать двум
  check_intentgate_liveness() — §14 для гейта квитанции замысла (нить intent-receipts): два спосо
  check_disposability_causes() — §15/§13: одна и та же причина отрицательного вердикта, всплывшая 
  check_memory_facts_growth() — WARN (Ф4.3 tripwire, план «ПЕРЕСБОРКА ФАЗЫ 4»): активные ЭПИЗОДИЧ
  check_memory_facts_invariants() — FAIL: инварианты memory_facts (E, долг из аудита 5 июля).
  check_temporal_class_coverage() — WARN: active state с temporal_class IS NULL — путь записи обошёл 
  check_clinical_kb_populated() — FAIL: clinical_kb ПУСТ → medical_frame вырождается в standard-рам
  check_clinical_kb_replica_fresh(conn) — FAIL: clinical_kb в БД тенанта РАЗОШЁЛСЯ с git-источником (yaml с
  check_frozen_relative_not_current() — WARN: active state с «сегодня/вчера» в ТЕКСТЕ, но НЕ классифициро
  check_db_row_regression() — Таблица упала >50% против пика последних бэкапов → тихое стирание
  check_visual_orphans() — Целостность visual-intake: фото без кейса / кейс без фото / hande
  check_stale_visual_cases() — Застрявший elicitation-диалог: кейс open >72ч без движения (liven
  check_visual_verdict_rate() — ЗАПАЗДЫВАЮЩИЙ сигнал качества elicitation: доля symptom_intake-ги
  check_symptom_prompt_discipline() — Guard безопасной позы: промпт elicitation не потерял запрет успок
  check_lab_history_regression() — FAIL (health-критично): lab_results резко усохла vs последний бэк
  check_effect_allele_coverage() — Покрытие strand-резолюции effect_allele (genome strand-fix Ф7).
  check_carrier_status_allele_coupling() — Сцепление carrier-статус ⟹ effect_allele NOT NULL (инвариант
  check_cbcr_methodology_present() — CBCR-методология load-bearing (manifest = system-prompt генерации
  check_validation_gate_methodology_present(root) — Методология валидационного гейта load-bearing (спека = замысел по
  check_lab_path_scope(db_path, base) — Механизм авто-возврата lab-пути при достаточном покрытии.
  check_fdr_qualification_drift(db_path, base) — M1-обёртка: читает канон (read-only) + манифест-ядро, считает дре
  check_weekly_digest_delivered() — Нить weekly-digest (2026-09-05). Понедельник: файл дайджеста ПРОШ
  check_tenant_dbs_reachable() — Мета-датчик против ТИХОЙ УСЕЧЁНКИ (RST, partner-integrity 2026-07
  check_canon_normal_flag_matches_reference() — «Норма» не противоречит напечатанному референсу. Оба тенанта (вол
  check_partner_epochs_ready() — Готовность СИБЛИНГ-тенанта к пер-тенант стратификации A/D.
  check_family_names_resolve(db_path, base) — Каждое объявленное имя семьи валид-гейта должно разрешаться в ЖИВ
  check_threshold_names_reach_data(db_path) — Каждый лабораторный порог обязан ВСТРЕЧАТЬ данные: имя = канон la
  check_norm_review_overdue() — Активная норма с прошедшим next_review — красное у владельца. Это
  check_norm_vs_lab_reference() — Внешний свидетель нормы. Красное — только для norm_kind='referenc
  check_norm_coverage() — Покрытие нормой: аналит с ≥norm.coverage_min_n измерений без норм
  check_norm_kind_unclassified() — Остаток без вердикта о виде нормы — WARN-счётчик владельцу. Не кр
  check_norm_documents_fresh() — Норма — реплика внешнего документа; единственная ось согласованно
  check_schedule_vs_guideline() — Назначение врача (lab_monitoring_schedule, source encounter:*) — 
  check_threshold_source_is_document() — Инвариант threshold_derived_from_document (реестр norm_from_docum
  check_hae_arrivals_have_owner() — WARN: у метрики, которую прибор реально присылает, нет хозяина (р
  check_hae_raw_archive_compressed() — FAIL: в архиве сырья HAE лежат несжатые выгрузки старше 15 дней (
  scan_hollow_constitutions(db_paths) — Чистая функция: для каждой БД возвращает пометку, если у неё есть
  check_constitutions_not_hollow() — FAIL, если конституции сгенерированы на ПУСТОМ effect_allele (пол
  check_constitutions_trigger_alive() — Пульс пересборки конституций по новым вводным (решение владельца 
  check_safety_net_can_judge() — «Судить нечем» у предохранителя — долг системы в инженерную очере
  check_public_genotype_profile() — Профиль генотипа тенанта в публичной зоне (2026-09-25, нить genot
  check_single_canonical_db() — R1/R2 (split-brain prevention, 2026-06-18): ровно один health.db,
  scan_cross_tenant(cur_db_path, sibling_paths, cand) — Ищет идентичные Oura-отпечатки между БД cur и siblings.
  check_cross_tenant_contamination() — Belt-and-suspenders к fail-closed secrets_dir() (2026-07-03).
  check_one_document_one_event() — Разбор документов создаёт ровно одно событие на файл (нить intake
  check_longitudinal_freshness() — longitudinal_analysis запускается по расписанию `com.larry.health
  check_longitudinal_gate_applied() — В свежем longitudinal-саммари статистический гейт обязан быть при
  check_gate_run_receipt(artifact, belief_day) — Отказ гейта — событие с читателем, а не «строки просто нет».
  check_belief_fresh_vs_data(conn) — Вера — это КЭШ, посчитанный из `daily_metrics`. У кэша не было ин
  check_sleep_stage_provenance(auto_repair) — Фальшивые стадии сна не должны существовать — а если появились, ч
  check_sleep_device_mixing() — Сон от одного прибора, ВСР от другого — в одной строке веры. Ратч
  check_quarantine_stuck(conn) — Карантин без вердикта дольше QUARANTINE_STUCK_DAYS — затор, а не 
  check_mc_gap(artifact) — MC-зазор: любой прошедший BY-вердикт лёг в ±2·MCSE от своей линии
  check_passset_flicker(artifact) — Мерцание pass-set: членство прошедших пар изменилось между двумя 
  check_passset_history_depth(artifact) — История снимков короче календаря → её подчистили или файл пересоз
  check_gate_artifacts_liveness(receipt, artifacts) — После УСПЕШНОГО прогона гейта обязательные артефакты обязаны суще
  check_triage_delivery_liveness(base, now) — ЖИВА ЛИ САМА РЕЛЬСА ДОСТАВКИ warn (integrity → triage → Telegram)
  check_tenant_triage_alive(base) — Утренний разбор ЧЕЛОВЕКА-ТЕНАНТА жив (28.09, решение владельца «п
  check_gp_schedule() — GP monthly ≤35д.
  check_review_dates() — Проблемы и протоколы с просроченным review_date.
  check_periods_expiry() — Клинические периоды: фазы watchful_waiting с истёкшей датой без n
  treatment_date_disagreements(periods, problems, tol_days) — ЧИСТАЯ функция (тестируема без БД). periods: dict(name,type,start
  check_treatment_dates_agree() — Даты терапий в `periods` (primary) против `problem_list` (копии в
  check_treatment_history_extracted() — Лечение — производное из документов (medications), не ручная стро
  check_no_hardcoded_diagnosis() — FAIL: снятый онко-литерал вернулся в промпт-билдер (нить diagnosi
  check_doc_classifier_seeded() — WARN: tenant_doc_patterns.yaml есть, но его паттерны НЕ в doc_pat
  check_cpic_canon_consistent() — FAIL: канон CPIC (3 проекции) не засеян или рассогласован по seed
  check_pubmed_egress_neutral() — FAIL: egress-guard пропускает сырую специфику диагноза в PubMed, 
  check_labs_freshness_per_tenant() — FAIL: онкомаркеры в НЕЙТРАЛЬНОЙ базе лаб-свежести → навязаны всем
  check_doc_structure() — Проверяет что ключевые doc-файлы существуют после Diataxis-рестру
  check_oura_column_completeness() — W5K-#169 (2026-05-14): свежесть Oura-колонок в daily_metrics.
  check_translations_fresh() — Английский перевод отстал от русского оригинала (28.09.2026, реше
  check_assessments_freshness() — Каждый инструмент — последнее заполнение не старше cadence_days +
  check_pro_score_unit_single(db_path) — PRO-балл под одним именем хранится в ОДНОЙ размерности (§16 клауз
  check_literature_freshness() — Последний literature_search покрывает последний плановый запуск п
  check_literature_freshness_partner() — Поиск литературы партнёра покрывает свой последний плановый запус
  check_survivorship_agent_freshness() — Последний survivorship_analysis не старше 18 дней.
  check_pending_proposals_ageing() — problem_list_proposals со status='pending' старше 21 дня.
  check_ecg_nonsinus() — Записи ЭКГ с тревожным ритмом (AFib / High HR) за последние 48ч →
  check_open_hypotheses_ageing() — Гипотезы со status='open' старше 60 дней без перехода в testing.
  check_unresolved_evaluations() — HV-6: гипотезы в testing, лабные данные есть, outcome отсутствует
  check_recommendation_engine_health() — evaluate_domain_need не должен молча падать в error-path.
  check_constitution_conflicts_unresolved() — memory(category='constitution_conflict') со status='open' старше 
  check_pharmaco_genome_sync() — Фармакогеномика синхронизирована с последним импортом генома.
  check_traits_genome_sync() — Детерминированные черты (Phase F) синхронизированы с последним им
  check_wellness_genome_sync() — Wellness-геномика (Phase G) синхронизирована с последним импортом
  check_prs_genome_sync() — PRS (Wave 4, Phase H) синхронизированы с последним импортом геном
  scan_backup_staleness(db_paths, now) — Чистая/тестируемая: свежесть ежедневного бэкапа по тенантам.
  check_backup_freshness() — Свежайший ежедневный бэкап КАЖДОГО тенанта покрывает последний пл
  check_db_size()
  check_logs_all_listed() — Слепое пятно ротации: лог РАСТЁТ, а в конфиге ротации его нет.
  check_fault_journal() — Решение владельца 2026-09-28: сбои идут в ночной цикл, не в Teleg
  check_logrotate_liveness() — Ротатор жив И отработал чисто (§14, находка producer_registry 01.
  check_secret_scope_matches_reality() — Объявленный класс секрета против того, что реально лежит в катало
  check_llm_tracts_guarded() — Число LLM-трактов БЕЗ secret_guard не растёт (§19, решение владел
  check_llm_direct_constructors(root) — Клиент Anthropic конструируется ТОЛЬКО в llm_client — там гард се
  check_llm_guard_blocks_reported() — Блокировки гарда не молчат: счётчик за сутки виден человеку.
  collect_ungrounded(window_days) — Находки «`r=0.X`, которого нет в ПРИНЯТОЙ вере»
  check_correlations_grounded() — UC-B-09: каждое `r=0.X` в свежих отчётах агентов подтверждено ПРИ
  check_ungrounded_corr_ratchet() — ЧИТАТЕЛЬ журнала — читает СОДЕРЖИМОЕ, а не длину.
  check_correlation_producers_declared(root, declared) — WARN: КТО в периметре считает корреляцию — объявлен либо находка.
  check_heldout_ready() — Гаситель нити validation-gate-repair (2026-08-08): оживить held-o
  check_security_sensors() — SECURITY.md «Квартальный чеклист» → код (security_sensors.py).
  check_changelog_freshness(changelog) — BL-DOCAGENT-DEAD-1: у каждой слитой нити с работой кода есть стро
  check_arch_map_fresh(root) — Карта проекта (ARCH_SNAPSHOT ru/en) пересобрана после последнего 
  check_consilium_freshness() — monthly_consilium пишет agent_report('monthly_consilium'). Молчан
  scan_calendar_staleness(db_paths, current_path, error_age_h) — Чистая/тестируемая: находки про кэш календаря по тенантам.
  check_calendar_freshness() — calendar_cache.json кормит GP-контекст; fetcher hourly. Молчание 
  check_tenant_biometrics_freshness() — Биометрия свежа У КАЖДОГО тенанта, а не только у владельца.
  check_producer_registry() — context_gate_orphan: каждый scheduled-производитель покрыт датчик
  check_lab_intake_pulse() — §14 для вотчера входа: доказываем, что ЦИКЛ крутится, а не что пр
  blind_spot_ages(candidates, seen, decided, watermark) — Чистое ядро датчика слепоты: (имя, mtime) → возрасты застрявших, 
  check_lab_intake_blind_spot() — Вторая половина предиката (§17): пульс есть, а работы нет.
  check_lab_review_queue_movement(paths) — Очередь человека движется. Непустая очередь без движения = потеря
  check_staging_status_coverage(paths) — РАТЧЕТ: ни одна строка staging не лежит в статусе, который никто 
  check_glossary_targets_known() — Цель подтверждённого алиаса обязана быть ИЗВЕСТНЫМ каноническим и
  check_unit_conversion_coverage(conn) — WARN burn-in (plan_consilium_under_manifest 2026-07-08 Фаза 1): а
  check_urine_name_convention(conn) — Конвенция имён мочевого домена — Urine_* (решение владельца 2026-
  check_morning_brief_gate_liveness() — Гейт анти-повтора жив: context_cards пополняется. Если гейт тихо 
  retell_score(first_sentence, owner_messages) — ЧИСТАЯ доля слов первого предложения брифа, пришедших из реплики 
  check_brief_does_not_retell_owner() — WARN: утренний бриф открывается пересказом того, что владелец ска
  check_profile_reconciler_fresh() — WARN: patient_profile разошёлся с живым источником — reconciler о
  check_time_contract() — Контракт единого времени: прод-код читает часы через _time_inject
  check_probe_liveness() — §14 для НОСИТЕЛЯ ПРОБ: у датчика, на котором стоят обещания реест
  check_suite_freshness() — §14 для САМОГО НАГРУЖЕННОГО датчика проекта — набора тестов.
  check_nightly_suite_liveness(rows, now, la_dir, host) — §14 для НОЧНОЙ задачи тестов (`com.larry.health.test-suite`, 00:0
  check_night_cycle_liveness() — §14 для движка ночного цикла: тихо умерший движок = падения ночью
  check_doorbell_liveness() — §14 для КОЛОКОЛА, поведенческий: молчание = launchd owner_nag мёр
  check_domain_boundary() — Граница двух домов лабораторных данных стережётся (работа B, 2026
  check_lab_class_verdicts_complete() — Каждый класс, который РЕАЛЬНО есть в данных, имеет вердикт челове
  epoch_of(ts)

check_contracts.py  # check_contracts.py — проверка целостности межмодульных связей.
  check(label, condition, detail)
  warn(label, detail)

smoke_tests.py  # Phase 0.5 — Smoke tests для Health OS.
  check(name, fn)
  test_save_retire_protocol()
  must_exist(p)
  test_update_exp()

gen_blueprint.py  # gen_blueprint.py — автообновление МОДУЛЬНОГО РЕЕСТРА в ARCH_SNAPSHOT.md.
  class DescriptionText
  blueprint_descriptions() — Full Russian first lines, before the 80/65-character rendering li
  update_arch_snapshot(lang) — Перезаписывает раздел МОДУЛЬНЫЙ РЕЕСТР в ARCH_SNAPSHOT.md между G
  main(argv)
  text(source) — Collect and translate the full source, then apply the rendering l
  cell(source) — Escape table separators after lookup, without changing the memory

```

### ИНТЕРФЕЙС
```

telegram_bot.py  # telegram_bot.py — thin entry-point для launchd plist com.larry.health.bot.

```

<!-- GEN:MODULE_REGISTRY:END -->

<!-- GEN:GRAPH:START -->
## ГРАФ ЗАВИСИМОСТЕЙ

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

-- ВСЕ МОДУЛИ → health_db (единственная точка данных)
-- health_db НЕ импортирует ничего внутреннего
```
<!-- GEN:GRAPH:END -->

---

<!-- GEN:KEY_PATHS:START -->

## КЛЮЧЕВЫЕ ПУТИ
<!-- АВТОГЕНЕРИРУЕТСЯ gen_key_paths.py — не редактировать вручную. -->
<!-- Источник: реальная FS Studio + health_db._STUDIO_LOCAL_DIR. -->

### Studio (canonical, через Tailscale; адрес — private/infra.yaml)

```
~/health/                                                   -- HEALTH_DATA_DIR (canonical после P1-1 hostname-aware)
~/health/data/health.db                                     -- SQLite БД, 27 таблиц (single-primary, 2026-04-24)
~/health/daily_metrics/                                     -- JSON по дням от import_oura.py ⚠️ отсутствует
~/health/reports/                                           -- Markdown GP отчёты YYYY-MM-DD.md
~/health/genome/                                            -- raw 23andMe TSV (импортируется в raw_snps)
~/health/biochemical/                                       -- PDF лабораторных анализов
~/health/backups/                                           -- Ежедневные sqlite3 .backup (ротация 30д, com.larry.health.backup)
~/health/logs/                                              -- api.log, bot logs, per-machine
~/health/.claude/specialists/                               -- Промпты MDT-специалистов (oncology.md и др.) ⚠️ отсутствует
~/health_scripts/                                           -- Production-код, git canonical после миграции 2026-05-09
~/health_scripts/scripts/git-hooks/pre-commit               -- Pre-commit hook (P1-4, INV-DOC enforcement)
~/health_scripts/scripts/install_hooks.sh                   -- Установка hook через symlink (Studio-only)
~/health_scripts/scripts/pre_destructive_check.sh           -- Manual guard перед reset/checkout/merge
~/health_scripts/doc_inventory.yaml                         -- SSOT классификации .md (LIVE/archive)
~/.health_secrets/anthropic_key                             -- Claude API key
~/.health_secrets/oura_token                                -- Oura Ring API v2 token
~/.health_secrets/telegram_token                            -- Telegram Bot API token
~/.health_secrets/telegram_chat_id                          -- Целевой chat для отчётов
~/.health_secrets/sync_token                                -- X-Sync-Token для bot→Studio FastAPI
~/Library/LaunchAgents/com.larry.health.bot.plist           -- Telegram бот (KeepAlive)
~/Library/LaunchAgents/com.larry.health.api.plist           -- FastAPI :8000 (KeepAlive) ⚠️ отсутствует
~/Library/LaunchAgents/com.larry.health.backup.plist        -- Daily sqlite3 backup 03:00
~/Library/LaunchAgents/com.larry.health.test-suite.plist    -- Nightly test pyramid 00:00
~/Library/LaunchAgents/com.larry.health.watcher.plist       -- fswatch → import
~/Library/LaunchAgents/com.larry.health.reminders-sync.plist-- macOS Reminders sync каждые 3ч
```

### MacBook (working copy)

```
~/health_scripts/                                           -- Рабочая копия, полный клон: единственный писатель, коммиты только здесь (CLAUDE.md §C)
~/health_scripts/scripts/pre_destructive_check.sh           -- Pre-destructive (правило #4)
~/Library/Mobile Documents/com~apple~CloudDocs/health/      -- облачная папка установки: реплика только для чтения (НЕ canonical, отстаёт)
~/.infrastructure.md                                        -- Manifest инфраструктуры (узлы, порты, Tailscale, FileVault)
~/.health_secrets/                                          -- Локальная копия секретов
~/Library/LaunchAgents/com.larry.health.backup.plist        -- 03:00 снимок дерева в refs/backups/daily-<дата> → push на Studio; main не трогает (CLAUDE.md §1)
```

<!-- GEN:KEY_PATHS:END -->

---

## КЛЮЧЕВЫЕ ПАТТЕРНЫ И СОГЛАШЕНИЯ

```
1. Всё через health_db.py — прямые sqlite3 запрещены (кроме genome_parser bulk insert)
2. Гипотезы в memory table (category='hypothesis') как JSON blob
   Статусы: open → testing → confirmed | rejected
   resolution_type: self_managed|needs_specialist (LLM ставит, Telegram уведомляет при needs_specialist)
3. Problem list statuses: active | active_monitoring | watchful_waiting | resolved
   (НЕ 'monitoring' — именно 'active_monitoring' и 'watchful_waiting')
4. Временная зона: данные в UTC, отчёты и расписание по местному времени тенанта (<пояс>)
5. Studio auth: X-Sync-Token header (НЕ Telegram initData — это только Mini App браузер)
6. Studio URL: https://<tailnet_hostname> (Tailscale Funnel)
   MacBook → Studio rsync: <studio_ssh>
7. doc_agent не коммитит — только стейджит файлы; backup.sh коммитит в 03:00
8. post-commit hook пропускает "auto: daily backup" коммиты (рекурсия)
9. Regex с re.DOTALL на BLUEPRINT.md → catastrophic backtracking. Только line-by-line!
10. get_day() возвращает JSON с вложенной структурой (sleep.totalSleep).
    daily_metrics — плоские колонки (sleep_total, hrv). Не путать.
11. Lifestyle-агенты получают геномный контекст через build_lifestyle_genome_block(domain)
12. MDT/wellally_consult инжектирует build_genetic_context_block() в data_package
13. Caffeinate: morningwake стартует 06:45 local, 7200с покрывает до 08:45.
    При поясе UTC+4 и восточнее — проверить покрытие APScheduler.
14. CheckinState.is_stale(ttl_hours=4) сбрасывает зависший чекин.
    Автозапуск 20:00 по местному времени снят 2026-08-17 — единственный вход теперь
    handlers/meta.cmd_checkin (ручная команда /checkin).
15. Категории resilience_level определяются по данным через GROUP BY.
    Не хардкодить категории и не предполагать совпадение со словарём поставщика.
16. generate_constitutions.py: модель claude-opus-4-7, max_tokens=8000.
    Источники промпта: genetic_variants + periods + agent_reports.raw_output.
    НЕ используются: promethease_variants (0 строк), lifestyle-отчёты, 30/90д агрегаты.
    Читать raw_output, НЕ findings (findings — краткая выжимка, теряет longitudinal-детали).
17. longitudinal_analysis.py: читает agent_reports WHERE agent_type='longitudinal_analysis'.
    Отдаёт ключевые корреляции и многолетние тренды метрик тенанта (числа — в БД, не здесь).
```

---

## АЛЕРТ-ЛОГИКА (recommendation_engine v2, 2026-04-21)

> Объяснение архитектуры и доказательная база: [recommendation_engine.md](recommendation_engine.md).
> Как добавить новый домен: [docs/how-to/add_domain.md](docs/how-to/add_domain.md).

### DOMAIN_SIGNALS

Сигналы домена задаются конфигурацией `system_config.domain_signals.*`
(авто-блок GEN:DOMAIN_SIGNALS ниже). Для допуска проверяются сила связи, число пар и устойчивость.
Независимо выдуманный пример структуры конфигурации; поля и числа не взяты из данных тенанта:

```python
DOMAIN_SIGNALS = {
    "example_domain": [
        {"metric": "example_metric", "label": "example_outcome", "r": 0.42, "threshold_pct": 20},
    ],
}
```

Направление сигнала задаёт интерпретацию перцентиля: для инвертируемой метрики
большее значение соответствует меньшему перцентилю.

### Абсолютные полы (Слой 2)

Срабатывают независимо от домена и 90-дневного перцентиля.
Источник: `threshold_analysis.py` — порог = исторический нижний перцентиль (p10) метрики на всём ряду тенанта
или на последних двух годах. Значения порогов — в таблице порогов в БД тенанта, не в документе.

| Метрика | Порог | Как выбран |
|---------|-------|------------|
| hrv | < p10 тенанта, мс | p10 по всему ряду (порог поднимался после пересчёта) |
| readiness | < p10 тенанта | p10 за последние 2 года |
| sleep_deep | < p10 тенанта, ч | p10 за последние 2 года |

### Urgency

- `recommended` — 1 сигнал, перцентиль 10–30%
- `required` — 2+ сигнала **или** перцентиль < 10%

### Baseline и активные протоколы

Сравнение «база против периода» и активные протоколы с ограничениями — данные тенанта:
они живут в его БД (`threshold_analysis.py`, таблицы протоколов и `patient_constraints`),
а не в этом документе (до 2026-09-26 здесь лежали числа и протоколы владельца).

> ⚠️ При изменении конституций (`constitutions/*.md`) — перегенерировать протоколы.

---

## ТЕСТОВАЯ ИНФРАСТРУКТУРА (2026-05-08, v3.0)

> Подробности: `TEST_ARCHITECTURE.md`, `USE_CASES.md`,
> `docs/explanation/test_architecture.md`.

### Канонические документы

| Файл | Тип Diataxis | Назначение |
|---|---|---|
| `USE_CASES.md` | Reference + правила | Каталог 52 UC по 12 группам с lifecycle status × confirmation. |
| `uc_index.yaml` | Reference (machine) | Машиночитаемый индекс confirmed UC: modules/data/tests/oracle/risk. |
| `TEST_ARCHITECTURE.md` | Reference + Explanation | 7-слойная пирамида + consistency-specific слой. |
| `EXTERNAL_DEPENDENCIES.md` + `external_dependencies.yaml` | Reference | Реестр upstream-артефактов. |
| `ROADMAP.md` (закрытый документ) | Status-tracking | Внутренний план работ и статусы; исключён из публичного экспорта, не требуется для установки. |
| `docs/explanation/test_architecture.md` | Explanation | Почему именно такая архитектура. |
| `docs/how-to/run_tests.md` | How-to | Команды запуска. |
| `docs/how-to/add_new_uc.md` | How-to | Процесс UC-J-02. |
| `docs/how-to/handle_test_failure.md` | How-to | Что делать при ночном fail. |
| `tests/charters/CH-*.md` | Reference (procedures) | Риск-маршруты для не-автоматизируемых UC. |

### Новые модули кода

| Модуль | Назначение |
|---|---|
| `_time_inject.py` | Централизованная инъекция времени (get_now/get_today/set_test_clock) |
| `propose_uc.py` | UC-J-01: git diff → proposal в `tests/plans/proposed/` (НЕ пишет в USE_CASES.md) |
| `generate_test.py` | UC-J-02: skeleton-генератор с проверкой `confirmation=confirmed` |
| `validate_uc_index.py` | UC-J-03: валидатор `uc_index.yaml` |
| `test_failure_handler.py` | 3 уровня обработки ночных failures (Triage A + Haiku diagnosis B; Repair C не реализован — норма CLAUDE.md §13, с 2026-08-03 сужен, а не запрещён) |
| `morning_test_summary.py` | Резюме suite в `agent_reports type='test_summary'` |
| `monthly_api_report.py` | Месячный TG-отчёт API-трат на diagnosis |
| `run_full_test_suite.sh` | Оркестратор pyramid |

### Новые launchd-агенты на Studio

| Label | Расписание | Что |
|---|---|---|
| `com.larry.health.test-suite` | 00:00 ежедневно (ExitTimeOut=21600) | `run_full_test_suite.sh` + `test_failure_handler.py` + `morning_test_summary.py` |
| `com.larry.health.test-api-report` | 1-го 09:30 | `monthly_api_report.py` |

### Изменённые модули (Tier-1 clock-рефактор)

`gp_agent`, `morning_report`, `integrity_tests`, `triage_agent`, `health_db`,
`import_oura`, `import_apple_health`, `import_all`, `safety_net`,
`checkin_agent`, `task_agent`, `hai_reports`, `hai_core`, `wellally_consult`,
`genome_update_agent` — все используют `_time_inject.get_now()` / `get_today()`
в logic-коде. CLI-блоки `__main__` намеренно не тронуты.

### Семантические изменения

- **`health_db.get_conn()`** — single-primary guard через SQLite `mode=ro`
  на не-Studio. Override: `ALLOW_WRITE_NONPRIMARY=1`. UC-I-07 `implemented`.
- **`run_checks.sh`** экспортирует `ALLOW_WRITE_NONPRIMARY=1` (BACKLOG:
  перенести pre-commit smoke на Studio).
- **`morning_report.py`** содержит секцию «Тесты»: regression в начало,
  expected_gap в конец как INFO.

### Покрытие на 2026-05-08

```
301 PASSED · 9 SKIPPED · 4 DESELECTED (requires_anthropic_key) · 2 XFAILED
```

XFAIL — известные баги, документированы как `partial`:
- `gp_agent.py:478,389,527-528` — NULL → 0 в f-string (UC-I-03).
- `import_apple_health.py:248` — shallow merge с null затирает Oura (UC-A-03).

---

## BACKLOG

> Задачи и технический долг → см. журнал долга `BACKLOG.md` (ведётся в закрытой части проекта).


## ПРАВИЛА ОБНОВЛЕНИЯ ЭТОГО ФАЙЛА

```
ПОСЛЕ ЛЮБОГО ИЗМЕНЕНИЯ КОДА:
1. Если добавлена/изменена публичная функция → обновить раздел МОДУЛЬНЫЙ РЕЕСТР
2. Если добавлена/изменена таблица в health_db → обновить раздел DB SCHEMA
3. Если добавлен новый модуль → добавить в РЕЕСТР и в ГРАФ ЗАВИСИМОСТЕЙ
4. Если изменился паттерн/соглашение → обновить КЛЮЧЕВЫЕ ПАТТЕРНЫ
5. Если что-то запланировано/сделано → обновить ЧТО ЗАПЛАНИРОВАНО

ПЕРЕД ЛЮБОЙ РАБОТОЙ С ПРОЕКТОМ:
1. Прочитать этот файл целиком
2. Убедиться что нужная функция не существует уже
3. Убедиться что нужная таблица/поле не существует уже
4. Только потом предлагать новый код
```

<!-- КОНЕЦ ФАЙЛА. Строк: ~350. Дата генерации: 2026-04-08. -->
<!-- Следующая регенерация: при добавлении нового модуля или таблицы. -->

## ЛОГ АРХИТЕКТУРНЫХ ИЗМЕНЕНИЙ

<!-- GEN:ARCH_LOG:START -->
- `2026-09-30` — новый модуль `night_repair` (зависит от: infra_config, secrets_paths)
- `2026-09-30` — `food_genome` + зависимость: region_pack
- `2026-09-30` — `reminders_sync` + зависимость: notify
- `2026-09-30` — `integrity_tests` + зависимость: finding_identity; `parked_decisions` + зависимость: config_db
- `2026-09-30` — удалён модуль `morning_report`
- `2026-09-30` — новый модуль `diagnosis_guard` (зависит от: pii_census)
- `2026-09-29` — `import_all` + зависимость: config_db; `import_coordinator` + зависимость: config_db; `import_medical_events` + зависимость: config_db
- `2026-09-29` — `daemon_liveness` + зависимость: plist_env_liveness; `dashboard` + зависимость: daemon_liveness; новый модуль `import_watchdog` (зависит от: health_db, import_status_db, notify); `lab_intake_watcher` + зависимость: daemon_liveness, plist_env_liveness (+7 изменений)
- `2026-09-29` — `genome_intake` + зависимость: config_db
- `2026-09-29` — `vcf_import_pipeline` + зависимость: genome_intake
- `2026-09-29` — `cpic_reference_db` + зависимость: i18n; `longitudinal_analysis` + зависимость: i18n; новый модуль `traits_pipeline` (зависит от: i18n); новый модуль `wellness_pipeline` (зависит от: i18n, traits_pipeline)
- `2026-09-29` — `clinical_kb` + зависимость: i18n; `food_genome` + зависимость: i18n; `food_profile` + зависимость: i18n; `food_quarterly` + зависимость: clinical_kb, i18n (+2 изменений)
- `2026-09-29` — `consult_prep` + зависимость: i18n; `food_rule_review` + зависимость: i18n; `hypothesis_consilium_eval` + зависимость: i18n
- `2026-09-29` — новый модуль `desc_translation` (зависит от: hai_core, llm_client); `gen_arch_blocks` + зависимость: gen_blueprint; новый модуль `gen_blueprint` (зависит от: desc_translation); новый модуль `gen_testing_contracts` (зависит от: gen_blueprint)
- `2026-09-29` — `assessment_importer` + зависимость: i18n; новый модуль `dashboard_views` (зависит от: i18n); `labs_db` + зависимость: i18n; `treatment_summary` + зависимость: i18n
- `2026-09-29` — `generate_constitutions` + зависимость: doc_translation
- `2026-09-29` — `health_db` + зависимость: i18n; `symptom_intake` + зависимость: i18n
- `2026-09-29` — новый модуль `generate_test` (зависит от: doc_translation); `test_failure_handler` + зависимость: doc_translation; новый модуль `validate_uc_index` (зависит от: doc_translation)
- `2026-09-29` — `night_investigator` + зависимость: llm_client
- `2026-09-29` — `genome_update_agent` + зависимость: _fmt_helpers
- `2026-09-29` — `generate_constitutions` + зависимость: i18n; `hai_core` + зависимость: i18n
- `2026-09-29` — `gp_context` + зависимость: i18n
- `2026-09-28` — `weekly_digest` + зависимость: i18n
- `2026-09-28` — `night_cycle` + зависимость: _fmt_helpers
- `2026-09-28` — `genome_update_agent` + зависимость: i18n; `gp_agent` + зависимость: i18n; `literature_curator` + зависимость: i18n; `survivorship_curator` + зависимость: i18n
- `2026-09-28` — `triage_agent` + зависимость: task_agent
- `2026-09-28` — `assessment_bot_handlers` + зависимость: _fmt_helpers; `assessment_dialog` + зависимость: _fmt_helpers; `assessment_scheduler` + зависимость: _fmt_helpers, i18n; `owner_nag` + зависимость: _fmt_helpers (+3 изменений)
- `2026-09-28` — `assessment_bot_handlers` + зависимость: notify; `assessment_bot_handlers` − зависимость: hai_hypotheses; `assessment_dialog` + зависимость: notify; `check_wellally_updates` + зависимость: i18n, notify (+26 изменений)
- `2026-09-28` — `integrity_tests` + зависимость: doc_translation
- `2026-09-28` — `integrity_tests` + зависимость: assessment_scheduler
- `2026-09-28` — `gp_agent` + зависимость: hai_hypotheses
- `2026-09-28` — `doc_agent` + зависимость: doc_translation
- `2026-09-28` — `labs_db` + зависимость: safety_net
- `2026-09-28` — `integrity_tests` + зависимость: safety_net
- `2026-09-28` — `belief_contract` + зависимость: secrets_paths
- `2026-09-28` — `assessment_dialog` + зависимость: assessment_scheduler; `assessment_importer` + зависимость: assessment_scheduler; `assessment_scheduler` + зависимость: secrets_paths; `hypothesis_semantic_check` + зависимость: assessment_scheduler (+1 изменений)
- `2026-09-28` — `reminders_sync` + зависимость: secrets_paths; `task_agent` + зависимость: reminders_sync
- `2026-09-28` — `assessment_bot_handlers` + зависимость: i18n; `assessment_dialog` + зависимость: i18n; новый модуль `i18n` (зависит от: profile_db)
- `2026-09-27` — удалён модуль `correlation_analysis`
- `2026-09-27` — `problems_db` + зависимость: api_spend_log, hai_core, llm_client
- `2026-09-27` — `pubmed_client` + зависимость: rules_db
- `2026-09-27` — `hai_core` + зависимость: region_pack; `import_hr_activity` + зависимость: import_apple_health; `import_hr_activity` − зависимость: infra_config
- `2026-09-27` — `lab_specialized` + зависимость: lab_promote
- `2026-09-27` — `hai_context` + зависимость: gp_context
- `2026-09-27` — `tasks_db` + зависимость: gp_context
- `2026-09-27` — `plist_env_liveness` + зависимость: secrets_paths
- `2026-09-26` — `profile_db` + зависимость: treatment_summary
- `2026-09-26` — `hae_checker` + зависимость: _time_inject; новый модуль `log_rotate` (зависит от: hae_checker, secrets_paths)
- `2026-09-26` — `hae_checker` + зависимость: import_apple_health, metrics_db; `integrity_tests` + зависимость: hae_checker, metrics_db; новый модуль `signal_family` (зависит от: _time_inject)
- `2026-09-25` — `generate_constitutions` − зависимость: patient_context
- `2026-09-25` — `generate_constitutions` + зависимость: config_db; `integrity_tests` + зависимость: generate_constitutions
- `2026-09-25` — `longitudinal_analysis` + зависимость: notify
- `2026-09-25` — `longitudinal_analysis` + зависимость: metrics_db
- `2026-09-24` — `consult_prep` + зависимость: infra_config; `hae_checker` + зависимость: infra_config; `hai_reports` + зависимость: infra_config; `import_all` + зависимость: infra_config (+9 изменений)
- `2026-09-24` — `integrity_tests` + зависимость: import_medical_events
- `2026-09-24` — `lab_intake_watcher` + зависимость: link_fetch; новый модуль `link_fetch` (зависит от: genome_intake, notify)
- `2026-09-24` — `assessment_dialog` + зависимость: genome_intake; новый модуль `genome_intake` (зависит от: genome_pipeline, health_db, infra_config, notify); `import_medical_events` + зависимость: notify, problems_db, treatment_db; `lab_intake_watcher` + зависимость: genome_intake, import_medical_events
- `2026-09-24` — `patient_context` + зависимость: problems_db
- `2026-09-24` — новый модуль `brief_cards` (зависит от: hai_analysis, metrics_db); `hai_analysis` + зависимость: metrics_db; `hai_context` + зависимость: metrics_db
- `2026-09-24` — `generate_constitutions` + зависимость: infra_config
- `2026-09-23` — `assessment_dialog` + зависимость: config_db, problems_db, profile_db, secrets_paths, treatment_db
- `2026-09-23` — `integrity_tests` + зависимость: proposals_db
- `2026-09-23` — новый модуль `daemon_liveness` (зависит от: infra_config); `db_regression` + зависимость: infra_config; `gen_arch_blocks` + зависимость: infra_config; новый модуль `gen_key_paths` (зависит от: infra_config) (+6 изменений)
- `2026-09-23` — `triage_agent` + зависимость: owner_nag, parked_decisions
- `2026-09-23` — `assessment_scheduler` + зависимость: assessment_importer; `integrity_tests` + зависимость: assessment_importer; `labs_db` + зависимость: clinical_kb; `pubmed_client` + зависимость: clinical_kb
- `2026-09-23` — `checkin_agent` + зависимость: region_pack; `env_context` + зависимость: region_pack; новый модуль `env_sources` (зависит от: region_pack); `owner_nag` + зависимость: region_pack (+2 изменений)
- `2026-09-23` — `night_cycle` + зависимость: owner_nag; `owner_nag` + зависимость: plist_env_liveness
- `2026-09-23` — `check_contracts` + зависимость: infra_config; `google_calendar_fetcher` + зависимость: infra_config; `health_db` − зависимость: location_signal; `integrity_tests` + зависимость: pii_census (+4 изменений)
- `2026-09-23` — `integrity_tests` + зависимость: agent_reports_db, morning_test_summary; `morning_test_summary` + зависимость: plist_env_liveness; `night_cycle` + зависимость: morning_test_summary
- `2026-09-23` — `integrity_tests` + зависимость: correlation_gate
- `2026-09-21` — `consult_prep` + зависимость: labs_db; `hai_context` + зависимость: lab_canon; `hypothesis_consilium_eval` + зависимость: labs_db
- `2026-09-14` — `hai_hypotheses` + зависимость: gp_context; `lifestyle_agents` + зависимость: gp_context
- `2026-09-14` — `checkin_agent` + зависимость: gp_context; `hai_chat` + зависимость: gp_context; `hai_context` + зависимость: labs_db; `hai_reports` + зависимость: gp_context (+1 изменений)
- `2026-09-14` — `night_cycle` + зависимость: agent_reports_db
- `2026-09-14` — `night_cycle` + зависимость: weekly_digest
- `2026-09-13` — `night_cycle` + зависимость: notify, owner_gate
- `2026-09-13` — `integrity_tests` + зависимость: stderr_watch
- `2026-09-13` — `night_cycle` + зависимость: finding_identity
- `2026-09-13` — `triage_agent` + зависимость: finding_identity
- `2026-09-13` — `gp_agent` + зависимость: labs_db
- `2026-09-13` — `wellally_consult` + зависимость: labs_db
- `2026-09-12` — `task_agent` + зависимость: lab_canon, labs_db
- `2026-09-12` — `integrity_tests` + зависимость: gp_context; `task_agent` + зависимость: config_db, memory_facts_db
- `2026-09-07` — `doc_agent` + зависимость: git_facts; `integrity_tests` + зависимость: git_facts
- `2026-09-07` — `integrity_tests` + зависимость: triage_agent
- `2026-09-05` — `weekly_digest` + зависимость: notify
- `2026-09-05` — `weekly_digest` + зависимость: _time_inject
- `2026-09-05` — `weekly_digest` + зависимость: secrets_paths; `weekly_digest` − зависимость: integrity_tests
- `2026-09-05` — `integrity_tests` + зависимость: weekly_digest; новый модуль `weekly_digest` (зависит от: config_db, diagnosis_guard, hai_core, integrity_tests, llm_client)
- `2026-09-03` — `norm_documents` + зависимость: lab_canon
- `2026-09-03` — `monthly_consilium` + зависимость: gp_context
- `2026-09-02` — новый модуль `norm_documents` (зависит от: _time_inject)
- `2026-09-02` — `field_reviews_db` + зависимость: lab_canon
- `2026-09-02` — `integrity_tests` + зависимость: norm_documents
- `2026-09-02` — `health_db` + зависимость: norm_documents; `safety_net` + зависимость: config_db, labs_db, norm_documents
- `2026-09-02` — `health_db` + зависимость: lab_canon; `safety_net` + зависимость: lab_canon
- `2026-09-02` — `cbcr_hypothesis` + зависимость: llm_client; `checkin_agent` + зависимость: llm_client; `consult_prep` + зависимость: llm_client; `correlation_analysis` + зависимость: llm_client (+15 изменений)
- `2026-09-01` — `integrity_tests` + зависимость: log_rotate
- `2026-09-01` — `food_rule_generator` + зависимость: clinical_kb
- `2026-09-01` — `problems_db` + зависимость: clinical_kb; `proposals_db` + зависимость: problems_db
- `2026-09-01` — `food_rule_generator` + зависимость: food_profile
- `2026-08-30` — `hypothesis_resolution` + зависимость: hypothesis_consilium_eval
- `2026-08-30` — `gp_context` + зависимость: lab_canon
- `2026-08-12` — `triage_agent` + зависимость: health_db
- `2026-08-12` — `integrity_tests` − зависимость: triage_agent
- `2026-08-12` — `integrity_tests` + зависимость: triage_agent; `night_investigator` + зависимость: hai_core
- `2026-08-11` — `brief_pipeline` + зависимость: _time_inject
- `2026-08-11` — `longitudinal_analysis` + зависимость: git_facts
- `2026-08-08` — `integrity_tests` + зависимость: heldout_readiness, signal_family
- `2026-08-08` — `integrity_tests` + зависимость: parked_decisions
- `2026-08-06` — `brief_state` + зависимость: config_db
- `2026-08-05` — `integrity_tests` + зависимость: profile_reconciler
- `2026-08-04` — `doc_agent` + зависимость: llm_client; новый модуль `llm_client` (зависит от: secret_guard)
- `2026-08-04` — новый модуль `api_spend_log` (зависит от: _time_inject); `assessment_bot_handlers` + зависимость: _time_inject; `assessment_dialog` + зависимость: _time_inject; `assessment_importer` + зависимость: _time_inject (+125 изменений)
- `2026-08-03` — новый модуль `api_spend_log` (зависит от: _time_inject); `assessment_bot_handlers` + зависимость: _time_inject; `assessment_dialog` + зависимость: _time_inject; `assessment_importer` + зависимость: _time_inject (+125 изменений)
- `2026-08-02` — новый модуль `service_trouble` (зависит от: health_db)
- `2026-07-29` — новый модуль `loinc_match` (зависит от: health_db)
- `2026-07-29` — новый модуль `loinc_loader` (зависит от: health_db)
- `2026-07-29` — `lab_intake_watcher` + зависимость: import_all
- `2026-07-28` — новый модуль `doc_triage` (зависит от: hai_core)
- `2026-07-26` — новый модуль `quarantine_db` (зависит от: health_db)
- `2026-07-26` — новый модуль `belief_contract` (зависит от: health_db)
- `2026-07-22` — `symptom_intake` + зависимость: hai_hypotheses
- `2026-07-22` — новый модуль `symptom_intake` (зависит от: hai_core)
- `2026-07-22` — новый модуль `visual_db` (зависит от: health_db)
- `2026-07-17` — `integrity_tests` + зависимость: pubmed_client
- `2026-07-17` — новый модуль `cpic_reference_db` (зависит от: health_db)
- `2026-07-17` — `hai_core` − зависимость: treatment_summary
- `2026-07-17` — `assessment_bot_handlers` − зависимость: telegram_bot
- `2026-07-16` — `hai_hypotheses` + зависимость: patient_context
- `2026-07-16` — `publication_reader` + зависимость: patient_context
- `2026-07-16` — новый модуль `clinical_kb` (зависит от: health_db)
- `2026-07-15` — `gp_agent` + зависимость: env_context
- `2026-07-15` — новый модуль `food_rule_review` (зависит от: cbcr_hypothesis)
- `2026-07-15` — новый модуль `food_rule_generator` (зависит от: hai_core)
- `2026-07-15` — новый модуль `generated_food_rules` (зависит от: health_db)
- `2026-07-14` — новый модуль `food_floor` (зависит от: health_db)
- `2026-07-14` — новый модуль `food_quarterly` (зависит от: health_db)
- `2026-07-14` — новый модуль `food_profile` (зависит от: health_db)
- `2026-07-14` — `brief_pipeline` + зависимость: calendar_client
- `2026-07-14` — новый модуль `location_signal` (зависит от: health_db)
- `2026-07-14` — `gp_agent` + зависимость: brief_validator
- `2026-07-13` — `brief_pipeline` + зависимость: env_context
- `2026-07-13` — новый модуль `env_context` (зависит от: brief_cards, env_sources)
- `2026-07-13` — `gp_agent` + зависимость: brief_pipeline, brief_state
- `2026-07-13` — `brief_pipeline` + зависимость: brief_gate
- `2026-07-13` — новый модуль `brief_pipeline` (зависит от: brief_cards, genome_context, health_ai, health_db, safety_net)
- `2026-07-13` — `monthly_consilium` + зависимость: hypothesis_semantic_check
- `2026-07-13` — новый модуль `brief_state` (зависит от: brief_gate)
- `2026-07-12` — новый модуль `backfill_hae_coverage` (зависит от: hae_checker, health_db)
- `2026-07-12` — новый модуль `import_fitdays` (зависит от: health_db)
- `2026-07-09` — новый модуль `beliefs` (зависит от: health_db)
- `2026-07-06` — новый модуль `memory_truthcheck` (зависит от: health_db)
- `2026-07-06` — новый модуль `backfill_critical_flag` (зависит от: health_db); `checkin_agent` + зависимость: patient_context; новый модуль `consilium_roster` (зависит от: health_db); `consult_prep` + зависимость: patient_context (+26 изменений)
- `2026-06-28` — новый модуль `agent_reports_db` (зависит от: health_db); новый модуль `alerts_db` (зависит от: health_db); новый модуль `assessments_db` (зависит от: health_db); новый модуль `checkins_db` (зависит от: health_db) (+40 изменений)
- `2026-06-27` — новый модуль `fix_palindromic_het` (зависит от: health_db); новый модуль `genome_weights` (зависит от: health_db); новый модуль `longitudinal_analysis` (зависит от: health_db); новый модуль `model_health_check` (зависит от: hai_core) (+8 изменений)
- `2026-06-18` — новый модуль `treatment_extractor` (зависит от: hai_core, health_db); новый модуль `treatment_summary` (зависит от: health_db)
- `2026-06-18` — новый модуль `check_contracts` (зависит от: health_db)
- `2026-06-18` — удалён модуль `swot_analysis`; удалён модуль `update_problem_list`
- `2026-06-17` — новый модуль `patient_context` (зависит от: health_db)
- `2026-06-17` — `backfill_effect_alleles` + зависимость: genome_annotator
- `2026-06-17` — `consult_prep` + зависимость: hai_core; `generate_constitutions` + зависимость: hai_core; `genome_context` + зависимость: hai_core; `import_medical_events` + зависимость: hai_core (+3 изменений)
- `2026-06-17` — `checkin_agent` + зависимость: hai_core; новый модуль `doc_agent` (зависит от: hai_core); `genome_update_agent` + зависимость: hai_core; новый модуль `lab_extractor` (зависит от: hai_core) (+2 изменений)
- `2026-06-17` — `gp_agent` + зависимость: hai_core
- `2026-06-17` — удалён модуль `messages`
- `2026-06-17` — новый модуль `messages` (зависит от: assessment_bot_handlers, checkin_agent, health_ai, health_db)
- `2026-06-15` — удалён модуль `main`
- `2026-06-15` — удалён модуль `callbacks`; удалён модуль `scheduled`
- `2026-06-15` — новый модуль `callbacks` (зависит от: assessment_bot_handlers, health_db, import_all); новый модуль `scheduled` (зависит от: checkin_agent, gp_agent, hae_checker, health_ai, health_db, lab_fuzzy, task_agent, wellally_consult)
- `2026-06-05` — DB recovery: workouts B-tree corruption → dump/restore; workouts backfill за всю историю тенанта
- `2026-06-05` — `morning_report.py` METRICS_DIR: HEALTH_DATA_DIR env вместо хардкода iCloud; `db.get_day()`: flat steps всегда авторитетен (Oura primary); `generate_daily_report` max_tokens 700→1500
- `2026-06-05` — `integrity_tests.py` +2: check_db_integrity (PRAGMA integrity_check), check_steps_visible_via_get_day
- `2026-06-01` — рефактор «один модуль — одна функция»: `hae_checker/run_check`, `lab_fuzzy/top_candidates`, `import_coordinator/coordinate_import`; `build_alert_text` → `jobs.scheduled._build_hae_alert_text`
- `2026-06-01` — Apple Health Variant B: `raw["apple_health"]` + COALESCE fallback для hrv/sleep/resting_hr при Oura=NULL; guard: Oura-owned ключи не перезаписываются
- `2026-06-01` — новые колонки `daily_metrics`: exercise_min, cycling_km, walking_speed_avg, walking_step_length_avg, walking_asymmetry_avg, stand_min, met_avg
- `2026-06-01` — новая таблица `hae_metric_registry`; EDEADLK-фикс: brctl download retry; данные 28-30.05 восстановлены
- `2026-06-01` — новый модуль `hae_checker` (зависит от: health_db); `import_all` + зависимость: import_coordinator; новый модуль `lab_fuzzy` (зависит от: health_db)
- `2026-06-01` — новый модуль `import_coordinator` (зависит от: health_db)
- `2026-05-24` — новый модуль `hypothesis_consilium_eval` (зависит от: hai_core, health_db, lifestyle_agents, wellally_consult); новый модуль `hypothesis_lab_linker` (зависит от: health_db); новый модуль `hypothesis_resolution` (зависит от: hai_hypotheses, health_db)
- `2026-05-23` — удалён модуль `telegram_bot`
- `2026-05-23` — `telegram_bot` − зависимость: wellally_consult
- `2026-05-23` — `telegram_bot` − зависимость: genome_update_agent
- `2026-05-23` — `telegram_bot` − зависимость: consult_prep
- `2026-05-22` — новый модуль `dashboard_db` (зависит от: health_db)
- `2026-05-22` — `assessment_bot_handlers` + зависимость: assessment_dialog; `assessment_dialog` + зависимость: assessment_importer; `correlation_analysis` + зависимость: cbcr_hypothesis; `hai_hypotheses` + зависимость: cbcr_hypothesis (+6 изменений)
- `2026-05-22` — новый модуль `assessment_bot_handlers` (зависит от: hai_hypotheses, health_db, telegram_bot); новый модуль `assessment_dialog` (зависит от: health_db); новый модуль `assessment_importer` (зависит от: health_db); новый модуль `assessment_scheduler` (зависит от: health_db) (+16 изменений)
- `2026-05-09` — новый модуль `morning_test_summary` (зависит от: health_db)
- `2026-05-08` — новый модуль `import_all` (зависит от: health_db)
- `2026-04-25` — новый модуль `calendar_sync` (зависит от: health_db); новый модуль `import_apple_health` (зависит от: health_db); `integrity_tests` − зависимость: health_ai
- `2026-04-22` — `checkin_agent` − зависимость: calendar_client
### 2026-04-20 — Domain-based Recommendation Engine (ADR-001)
- `health_db.py`: новая таблица `patient_constraints`, колонка `protocols.domain`,
  функции `save_constraint()`, `get_active_constraints()`, `get_active_protocols(domain)`
- `telegram_bot.py`: `evaluate_nurosym_need()` → `evaluate_domain_need(domain)`,
  `check_nurosym_scheduled()` → `check_recommendations_scheduled()` (итерирует домены)
- `patient_constraints` поддерживает привязанные к протоколу ограничения с условиями применения.
- Oura stress backfill: вся доступная история stress-данных тенанта

- `2026-04-20` — `telegram_bot` + зависимость: consult_prep
- `2026-04-20` — новый модуль `backfill_effect_alleles` (зависит от: health_db); новый модуль `checkin_agent` (зависит от: calendar_client, health_db); новый модуль `constitution_analysis` (зависит от: health_db, promethease_context); новый модуль `consult_prep` (зависит от: genome_context, hai_hypotheses, health_db) (+29 изменений)
<!-- GEN:ARCH_LOG:END -->

---

<!-- GEN:TEST_COVERAGE:START -->

## ТЕСТОВОЕ ПОКРЫТИЕ (последний ночной прогон)
<!-- АВТОГЕНЕРИРУЕТСЯ gen_arch_blocks.py из tests/reports/<последний>/summary.json. -->

_Нет данных_ — `tests/reports/<YYYY-MM-DD>/summary.json` не найден.

<!-- GEN:TEST_COVERAGE:END -->

---

<!-- GEN:XFAIL_LIST:START -->

## XFAIL — известные регрессии под наблюдением
<!-- АВТОГЕНЕРИРУЕТСЯ gen_arch_blocks.py: AST-сканер @pytest.mark.xfail в tests/. -->

**Всего:** 10

| Тест | Причина |
|---|---|
| `tests/unit/test_import_all_exit_status.py:106` | F-03: `main()` не имеет ни одного return — успех неотличим от любого исхода. Зелёным станет в Фазе C — тогда XPASS(stric |
| `tests/unit/test_import_all_exit_status.py:119` | F-03: per-file ошибка посчитана и напечатана, но не доехала до кода возврата. Зелёным станет в Фазе C. |
| `tests/unit/test_import_all_exit_status.py:178` | F-03: `__main__` зовёт `main()` без SystemExit — shell всегда видит 0. Зелёным станет в Фазе C. |
| `tests/unit/test_import_medical_events_cli.py:83` | F-03: `main()` не возвращает код — успешный прогон отдаёт None. Зелёным станет в Фазе C. |
| `tests/unit/test_import_medical_events_cli.py:98` | F-03: отказ обработчика не доезжает до кода возврата процесса. Зелёным станет в Фазе C. |
| `tests/unit/test_lab_name_homes.py:88` | F-09: словарь распознавателя собирается из `_VOCAB` ∪ общего `lab_vocab`, а `lab_name_aliases` (единственный дом с подтв |
| `tests/unit/test_lab_promote_glossary.py:46` | F-14: `_canon` принимает глоссарий аргументом и не читает его ни разу — подтверждение человека отключено структурно. Зел |
| `tests/unit/test_lab_promote_glossary.py:71` | F-14: глоссарий запрашивается константой `format_id=0`, а id форматов непредсказуемы (synevo=1, imd_berlin=4242). Зелёны |
| `tests/unit/test_save_clinical_idempotence.py:69` | F-19: ключ дубля консультаций не включает `source_file` — два РАЗНЫХ документа одного типа за одну дату схлопываются в о |
| `tests/unit/test_save_clinical_idempotence.py:94` | F-03 внутри функции: отказ записи гаснет в `except Exception: print(...)` и наружу уходит None — вызывающий не может отр |

<!-- GEN:XFAIL_LIST:END -->

---

<!-- GEN:EXTERNAL_INTEGRATIONS:START -->

## ВНЕШНИЕ ИНТЕГРАЦИИ
<!-- АВТОГЕНЕРИРУЕТСЯ gen_arch_blocks.py. Маркер ⚠️ — секрет не найден физически. -->

### Сервисы

| Сервис | Секрет | Назначение | Модуль |
|---|---|---|---|
| Anthropic Claude API | `~/.health_secrets/anthropic_key` | AI, отчёты, чат | `hai_core, gp_agent` |
| Oura Ring API v2 | `~/.health_secrets/oura_token` | сон, ВСР, активность | `import_oura` |
| Telegram Bot API | `~/.health_secrets/telegram_token` | интерфейс пользователя | `telegram_bot` |
| Telegram chat-id | `~/.health_secrets/telegram_chat_id` | целевой chat (OWNER fail-closed) | `telegram_bot` |
| Studio X-Sync-Token | `~/.health_secrets/sync_token` | bot→Studio FastAPI авторизация | `telegram_bot, main` |
| PubMed E-utilities | `—` | медицинская литература (публичный) | `pubmed_client` |
| MyVariant.info | `—` | аннотация геномных вариантов (публичный) | `genome_annotator` |
| GWAS Catalog | `—` | trait-ассоциации (публичный) | `genome_context` |
| Apple Health Export | `(iCloud XML)` | шаги, ЧСС, вес | `import_apple_health` |
| macOS Calendar | `(icalbuddy CLI)` | события, поездки | `calendar_client` |
| macOS Reminders | `(osascript)` | задачи пользователя | `task_agent, reminders_sync` |

### Tailscale endpoints

| Точка | URL | Назначение |
|---|---|---|
| Private Serve :443 | https://<tailnet_hostname>/ | соседний проект Flask :5001 (tailnet-only); маршрут /→:8000 снят 2026-07-06, TD-09 |
| Public Funnel :10000 | https://<tailnet_hostname>:10000/mm | соседний проект public gateway → :9001 (allow-list reverse proxy) |

<!-- GEN:EXTERNAL_INTEGRATIONS:END -->

---

<!-- GEN:DOMAIN_SIGNALS:START -->

## DOMAIN_SIGNALS (источник: `system_config.domain_signals.*`)
<!-- АВТОГЕНЕРИРУЕТСЯ gen_arch_blocks.py --only domain_signals. -->
<!-- Источник правды — БД тенанта; r (корреляция по его ряду) в документ не выносится. -->

### vagal_activation

| metric | label | threshold_pct |
|---|---|---:|
| `hrv` | ВСР завтра | 30 |
| `recovery_high_min` | ВСР завтра | 25 |
| `stress_high_min` | ЧСС покоя завтра | 30 |
| `resting_hr` | стресс-нагрузка | 30 |

### stress

| metric | label | threshold_pct |
|---|---|---:|
| `stress_high_min` | ЧСС покоя завтра | 30 |
| `resting_hr` | стресс-нагрузка | 30 |
| `recovery_high_min` | баланс стресс/восст. | 25 |

### activity

| metric | label | threshold_pct |
|---|---|---:|
| `activity_score` | REM-сон ночью | 30 |
| `readiness` | ресурс для активности | 30 |

### sleep

| metric | label | threshold_pct |
|---|---|---:|
| `sleep_score` | readiness завтра | 25 |
| `sleep_rem` | когнитивное восст. | 25 |
| `sleep_efficiency` | recovery завтра | 25 |

### nutrition

| metric | label | threshold_pct |
|---|---|---:|
| `stress_high_min` | адаптация питания | 25 |
| `resting_hr` | воспалит. прокси | 30 |

<!-- GEN:DOMAIN_SIGNALS:END -->
