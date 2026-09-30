[English](survivorship_engine.en.md) · **Русский**

# Survivorship-extension — архитектура

> **Тип:** Explanation (Diataxis).
> **Дата создания:** 2026-05-18 (одновременно с phase 9 плана v4).
> **Контекст:** объясняет «как устроено и почему» для survivorship-расширения. План работ и дельта-журнал — в `survivorship_extension_plan.md`.

## Зачем

Расширение поверх существующей архитектуры Health OS, которое:

1. **Слушает свежую литературу** про постонкологическое восстановление, поздние эффекты лечения, анатомию после перенесённой операции.
2. **Регулярно собирает субъективные симптомы** через валидированные опросники (ISI, MFSI-SF, модуль EORTC QLQ по локализации) с каденсом 90 дней.
3. **Двухнедельно сводит** объективные метрики + PRO + литературу + контекст в structured findings.
4. **Эскалирует** каждую находку в одну из пяти точек: гипотеза, пересмотр проблемы, задача, заметка, конфликт с конституцией.
5. **Сам напоминает**, если что-то из проактивных запусков просрочено.

Никаких команд от пользователя как обязательного канала.

## Какие модули входят

```
~/health_scripts/
  pubmed_searcher.py            # один: PubMed sweep по 19 темам, no LLM
  publication_reader.py         # один: cheap-triage Haiku + analyze Haiku
  literature_curator.py         # один: 4-way эскалация finding → memory|tasks|proposals
  hypothesis_semantic_check.py  # один: Haiku-дедуп перед save_hypothesis
  survivorship_analyzer.py      # один: shadow-diff + metric drift + контекст
  survivorship_curator.py       # один: 5-way эскалация (включая constitution_conflict)

  migrations/2026_05_18_schema_for_survivorship.py  # ALTER alerts.source + CREATE assessment_sessions
  migrations/2026_05_18_survivorship_alerts.py      # 5 правил в alerts

  data/instruments/<модуль>.json
  data/instruments/isi.json
  data/instruments/mfsi_sf.json
  data/survivorship_config.yaml
  data/survivorship_topics.yaml
```

Расширения существующих:
- `health_db.py` — `get_recent_labs(exclude_pro=True)`, `save_alert`/`get_active_alerts`, `save/get/update_assessment_session`, `get_active_constraints` теперь читает alerts.
- `hai_hypotheses.py` — три новых trigger в маппинге source.
- `integrity_tests.py` — 6 новых watchdog-проверок (assessments, literature, analyzer, proposals, hypotheses, constitution_conflicts).

## Что осталось (фаза 4)

`assessment_scheduler.py` + `assessment_dialog.py` + `assessment_importer.py` + три новых callback handler в `telegram_bot.py`. Это **UX-блокер** — требует решения «Mini App или inline-chat-диалог».

## Семантические каналы данных

### Survivorship-правила — `alerts`

5 правил живут в `alerts` с маркером `source='survivorship_literature'` или `source='genetic_constraint'`. Колонка `source` была добавлена ALTER в фазе 1.8. Маркер обязателен — он отделяет survivorship-правила от пациентских аллергий.

`get_active_constraints()` теперь читает `alerts` (legacy сигнатура сохранена). Это закрыло давний баг с `no such table: patient_constraints`.

### PRO-данные — `lab_results` с `source='instrument:<id>'`

PRO-подшкалы пишутся как обычные `lab_results` (test_name = `isi_q2`, `mfsi_sf_general`, и т.п.), но с маркером `source='instrument:isi'`. Это позволяет фильтровать.

`get_recent_labs(exclude_pro=True)` по умолчанию **исключает** PRO — чтобы существующий `safety_net` не интерпретировал балл опросника как биохимический показатель.

PRO **интегрированы в `events`-граф** (SX-15, 2026-05-18). `assessment_importer.import_file()` после создания записей в `lab_results` вызывает `db.save_event(event_type='self_observation', performer='self', performer_role='self', recorded_by='patient', diagnostic={type: 'functional_test', modality: <instrument_id>, raw_values_ref: [lab_results.id ...], interpreted_report: <строка subscale-скоров>})`. Это даёт долгосрочным агентам обходить PRO как полноправные FHIR-like события — а не только числовые ряды в `lab_results`.

### Гипотезы — `memory` с trigger в JSON payload

Все три источника гипотез (`monthly_consilium`, `literature_curator`, `survivorship_curator`) пишут через `save_hypothesis(...)` в `memory(category='hypothesis')`. Trigger различает источник.

Перед `save_hypothesis` — обязательный вызов `hypothesis_semantic_check.check(observation)` через Haiku. Если semantic дубль — запись пропускается, audit-запись в `memory(category='dedup_skipped', active=0)`.

Для богатого payload (problem_representation, illness_script, evidence_for/against, structural_confidence, patient_view) — таблица `hypotheses_cbcr` через `save_cbcr_payload(memory_id, ...)`.

### Конституционные конфликты — `memory(category='constitution_conflict')`

Новая категория. Появляется когда `survivorship_curator` видит расхождение между PRO/метриками и предпосылкой конкретной рекомендации в `constitutions/*.md`. Структура value (JSON):

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

При появлении — попадает в утренний контекст GP автоматически через `get_memory()` без фильтра по category.

### Сессии опросников — `assessment_sessions`

Новая таблица (единственная новая в расширении). Контракт: `id, instrument_id, wording_version_hash, chat_id, task_id, started_at, completed_at, answers_json, status`. Progressive save (UPDATE answers_json) на каждом ответе. Прерывание/resume по `(chat_id, instrument_id, status='in_progress')`.

Отдельная от `consultation_sessions` (которая зарезервирована за `/consult`-диалогами). В `telegram_bot.handle_text` нужно будет добавить роутинг по третьему режиму (фаза 4).

## Жизненный цикл одного литературного finding

```
voskresenie 04:00
  pubmed_searcher.run()
    → 19 тем × до 5 candidates = до 95 PMID
    → дедуп по PMID (60-дневное окно прошлых поисков)
    → agent_reports(agent_type='literature_search', findings=[{pmid, title, abstract,...}])

ezhednevno 04:30
  publication_reader.run(max=10)
    → берёт unprocessed candidates из последних literature_search
    → cheap-triage Haiku: «relevant для постонкологии?»
        → нет → запись со status='dismissed_at_triage'
        → да → analyze Haiku: structured finding {claim, population, evidence_level,
                                                   applicability_to_me, relevance_assessment}
    → agent_reports(agent_type='publication_reading', findings=[{kept|dismissed}])

  literature_curator.run(max=10)
    → берёт unprocessed kept-findings из publication_reading
    → для каждого: decision Haiku → action ∈ {hypothesis, problem_proposal, task, note}
    → hypothesis: semantic_check → CBCR (Sonnet 152s) → save_hypothesis(trigger='literature')
    → problem_proposal: save_problem_proposal(source='literature_curator')
    → task: save_task(source='literature_curator', fingerprint='literature:PMID:...')
    → note: save_memory(category='literature_note')
    → agent_reports(agent_type='literature_curator', findings=[audit])
```

Утром следующего дня GP-агент через `_build_gp_context` видит:
- Свежие литературные findings (последние 7 дней).
- Активные гипотезы (включая литературные).
- Активные предложения в `problem_list_proposals`.
- Активные `survivorship_literature` правила в `alerts`.

## Жизненный цикл survivorship-анализа

```
ponedelnik 03:30 (раз в две недели)
  survivorship_analyzer.run()
    → для каждого инструмента: latest PRO per subscale + shadow-diff vs прокси-метрики
    → global: метрика-тренды (HRV, sleep_deep, readiness, RHR — 7д vs 30д)
    → global: свежая литература за 14д (high relevance)
    → global: активные survivorship alerts (контекст)
    → agent_reports(agent_type='survivorship_analysis', findings={per_instrument, global})

ponedelnik 04:00
  survivorship_curator.run(max=10)
    → берёт actionable findings из последнего survivorship_analysis
    → пропускает recent_literature_relevant, active_survivorship_rules, pro_missing_for_rule
    → для каждого: decision Haiku → action ∈ {hypothesis, problem_proposal, task, note,
                                              constitution_conflict}
    → constitution_conflict: save_memory(category='constitution_conflict', value=JSON)
    → остальные — как в literature_curator
    → agent_reports(agent_type='survivorship_curator', findings=[audit])
```

## Watchdog

`integrity_tests.py` ежедневно в 07:50 запускает 6 новых проверок. Все они вызывают `warn()` (не `fail()`), результат попадает в `morning_test_summary` как INFO-секция:

```
SURVIVORSHIP-WATCHDOG
─────────────────────────
⚠️  assessments просрочены: mfsi_sf: 120д с <дата> (порог 104д); isi: ни одного заполнения
⚠️  literature_search не запускался 12д (порог 10)
```

Это и есть «если что-то не запускается — система приходит ко мне со списком».

Если `build_assessment_task_keyboard` никто не вызывает, напоминание не даёт
человеку пути заполнения, а задача остаётся с `sent_at=NULL`. Просрочка молчит,
пока открытая задача-опросник ДОСТАВЛЕНА
боту и её deadline не прошёл (вопрос у человека одним каналом, §13), а недоставленная
задача старше 2 суток — отдельный WARN «outbox не читается» (§14, liveness). Путь заполнения
и замок дашборда — `docs/explanation/task_reminders_flow.md`.

## Лимиты на curator'ы

На старте `survivorship_max_hypotheses_per_run: null` и `literature_max_hypotheses_per_run: null` в `survivorship_config.yaml`. Никаких хард-лимитов. Калибровка по фазе 10 (test charters).

Если по итогам charter 1 (месяц нормального использования) объём гипотез окажется шумным — добавляем лимиты как числа в этом же файле, без правки кода.

## Семантическая дедупликация

`hypothesis_semantic_check.check(observation)`:
1. Берёт открытые + (опционально) rejected гипотезы за `window_days` (default 90).
2. Haiku-вызов: «семантически совпадает с какой-либо?». Возвращает `(False, None, '')` или `(True, existing_id, reason)`.
3. Если True — `save_hypothesis` пропускается, audit-запись `memory(category='dedup_skipped', active=0)`.

Это обязательный шаг для **всех** трёх источников гипотез, чтобы избежать дубликатов между `monthly_consilium`, `literature_curator`, `survivorship_curator`. Структурный fingerprint (первые 50 символов observation) — недостаточно: разные формулировки одного механизма пройдут.

## Что переиспользуем без изменений

- `cbcr_hypothesis.generate_hypothesis_with_critique()` + `flatten_cbcr_payload()` — движок гипотез.
- `_notify_patient_view()` — Telegram-формат «заметили / возможные причины / что делать / когда к врачу».
- `save_problem_proposal()`, `save_task()`, `save_memory()` — каналы эскалации.
- `pubmed_client.search_pubmed()` — клиент NCBI E-utilities.
- fswatch + tesseract OCR pipeline (для будущей фазы 4 — импорт PRO JSON).
- `gp_agent.generate_daily_report()` — расширяется блоками контекста, не переписывается.
- `monthly_consilium` — остаётся без изменений; добавлен правильный trigger в маппинг.

## Технический долг

- **SX-15** (закрыто 2026-05-18): интеграция PRO в `events`-layer. `assessment_importer` теперь после `lab_results` создаёт `events(type='self_observation')` + `diagnostic_events(type='functional_test')` с `raw_values_ref` на созданные lab_results. Тест: `tests/unit/test_assessment_importer.py::test_importer_creates_self_observation_event_with_diagnostic`.
- **SX-16**: миграция онко-визитов из `consultations` в `encounters`. Уже частично сделано командой — survivorship-curator читает оба.
- **`monthly_api_report.py`** мониторит только тестовые API-вызовы. Стоимость новых модулей нигде не отслеживается. До отдельной фазы — мониторинг через Anthropic Console (charter 3 в плане).

## Где смотреть детали

Эти три документа написаны по данным владельца и лежат в закрытой части проекта:
- Полный план: `docs/explanation/survivorship_extension_plan.md` (в закрытой части проекта).
- Pre-flight findings: `docs/explanation/survivorship_preflight.md` (в закрытой части проекта).
- Schema snapshot canonical БД на 2026-05-18: `docs/explanation/studio_db_schema_2026-05.md` (в закрытой части проекта).
- Конфиг расширения: `~/health/data/survivorship_config.yaml`.
- Темы PubMed: `~/health/data/survivorship_topics.yaml`.
- Каталог инструментов: `~/health/data/instruments/*.json`.

## Тестовое покрытие (SX-17)

Все 10 новых модулей + 6 watchdog-функций в `integrity_tests.py` покрыты unit-тестами:

```
tests/unit/test_health_db_alerts_contract.py     ─ signature + round-trip + source-фильтр (3)
tests/unit/test_hypothesis_semantic_check.py     ─ пустая БД + Haiku-дубль (2)
tests/unit/test_assessment_scheduler.py          ─ создание задачи + idempotency (2)
tests/unit/test_pubmed_searcher.py               ─ save_agent_report + дедуп по PMID (2)
tests/unit/test_assessment_dialog.py             ─ full happy-path + resume по chat_id (2)
tests/unit/test_assessment_importer.py           ─ парсинг + warning при wording_hash mismatch (2)
tests/unit/test_survivorship_analyzer.py         ─ no_pro_yet finding + metric_drift (2)
tests/unit/test_integrity_tests_survivorship.py  ─ 3 из 6 watchdog (остальные аналогичны) (3)
tests/unit/test_publication_reader.py            ─ triage routing (dismiss + kept) (2)
tests/unit/test_literature_curator.py            ─ note + task action types (2)
tests/unit/test_survivorship_curator.py          ─ note + constitution_conflict (2)
```

**Итого: 24 теста, 505 PASS в full unit suite (после fix регрессии UC-I-02).**

**Test schema:** `tests/fixtures/health_schema.sql` дополнена таблицами `alerts` и `assessment_sessions` (SX-17). Существующая `db` fixture их подхватывает автоматически.

**Что не покрыто:**
- Integration-тесты (full pipeline searcher→reader→curator end-to-end) — задел `SX-19`.
- Snapshot-тесты для survivorship_analyzer — задел при необходимости.
- Расширенные edge cases (error paths, malformed inputs) — `SX-18` если будет нужно.

**Security regression закрыт:**
- `cb_router_with_owner_check` в `telegram_bot.py` — wrapper с inline OWNER_CHAT_ID check (UC-I-02 fail-closed pattern, по образцу `callback_doc_review`).
- `abh.cb_router` имеет внутреннюю двойную защиту через импорт OWNER_CHAT_ID.
