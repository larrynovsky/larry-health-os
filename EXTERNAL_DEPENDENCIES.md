[English](EXTERNAL_DEPENDENCIES.en.md) · **Русский**

# EXTERNAL_DEPENDENCIES.md — Реестр внешних артефактов

**Версия:** 0.1 (черновик) | **Дата:** 2026-05-08

> Этот файл — единственная точка истины о внешних зависимостях системы.
> Дополнение к `USE_CASES.md` §7 и группе K (Upstream Tracking).
> Связан с UC-K-01..K-06 и UC-X-01..X-03.

---

## 1. Назначение

Larry Health OS использует артефакты, которые меняются независимо от нашего кода:
specialist prompts, ClinVar/MyVariant API, PubMed, Oura/Apple schemas, Anthropic
models, Telegram API, macOS Reminders/Calendar, медицинские guideline-окна.

Если эти артефакты обновляются, и мы не замечаем — медицинские выводы дрейфуют
без явного коммита. Реестр существует, чтобы:

1. Знать, что мы используем извне.
2. Знать, какая версия применена и одобрена.
3. Видеть diff между upstream и локалью.
4. Применять обновления только осознанно (особенно `impact=medical`).
5. Связывать обновление с UC, которые могут быть затронуты.

---

## 2. Поля записи

Каждая зависимость — запись в `external_dependencies.yaml` со следующими полями:

| Поле | Тип | Назначение |
|---|---|---|
| `name` | str | Идентификатор зависимости |
| `category` | enum | `prompt` / `api_schema` / `model` / `os_integration` / `guideline` |
| `source` | str/url | Где живёт upstream (URL, путь к репозиторию, имя сервиса) |
| `local_path` | str | Локальный файл/snapshot (если применимо) |
| `language` | str | `zh`/`ru`/`en`/`-` для не-текстовых |
| `approved_sha256` | hex | SHA-256 версии, последней одобренной человеком |
| `current_sha256` | hex | SHA-256 текущей локальной версии |
| `last_checked` | iso8601 | Когда последний раз ходили в upstream |
| `last_applied` | iso8601 | Когда последний раз применили upstream → локаль |
| `last_check_ok` | bool | Последний check прошёл успешно (upstream доступен) |
| `local_override` | bool | Если `true` — upstream не должен затирать локаль молча |
| `impact` | enum | `medical` / `behavior` / `infra` / `cosmetic` |
| `owner` | str | Файл/модуль/человек, отвечающий за интеграцию |
| `impacted_uc` | list | Список UC ID, которые могут быть затронуты при обновлении |
| `notes` | str | Свободные заметки |

**Правило impact:**

- `medical` — изменение может изменить медицинское поведение системы (specialist prompts, ClinVar, guideline windows). Обновление **никогда** не применяется автоматически.
- `behavior` — изменение влияет на UX/тон/процесс, но не на медицинские выводы (lifestyle prompts, model routing).
- `infra` — техническая интеграция (Telegram API, launchd, file paths).
- `cosmetic` — формат вывода, отображение, без логики.

---

## 3. Минимальный реестр (стартовый)

Ниже — список, который ожидается в `external_dependencies.yaml`. Конкретные SHA/URL
заполняются по мере подтверждения.

### 3.1 Specialist Prompts (китайские)

```yaml
specialist_prompts_oncology:
  category: prompt
  source: https://github.com/huifer/WellAlly-health  # подтверждено 2026-05-08
  local_path: .claude/specialists/oncology.md
  language: zh
  approved_sha256: TBD
  current_sha256: TBD
  last_checked: null
  last_applied: null
  last_check_ok: null
  local_override: false
  impact: medical
  owner: wellally_consult.py
  impacted_uc: [UC-H-01, UC-I-01, UC-I-08, UC-K-01]
  notes: |
    Часть Council v2. Обновление prompt может изменить медицинские выводы
    онколога в Round A/B. Применять только вручную через /upstream_apply.
```

Аналогичные записи для каждого из 9 врачебных промптов:
`gastroenterology, cardiology, hematology, nephrology, nutrition, endocrinology,
psychiatry, pulmonology` — по той же схеме.

### 3.2 Lifestyle Prompts (русские)

```yaml
lifestyle_prompts_sleep:
  category: prompt
  source: TBD                                  # вероятно local-only, не из upstream wellally
  local_path: .claude/specialists/lifestyle_sleep.md
  language: ru
  approved_sha256: TBD
  current_sha256: TBD
  last_checked: null
  last_applied: null
  last_check_ok: null
  local_override: false
  impact: behavior
  owner: lifestyle_agents.py
  impacted_uc: [UC-B-05, UC-B-06, UC-K-02]
  notes: |
    Промпт коуча сна. Изменения влияют на тон и состав briefing,
    но не на медицинские диагнозы.
```

Аналогично для `lifestyle_movement, lifestyle_stress, lifestyle_energy`.

### 3.3 ClinVar / MyVariant API

```yaml
clinvar_via_myvariant:
  category: api_schema
  source: https://myvariant.info/v1/
  local_path: null
  approved_sha256: null                       # для API не SHA, а спецификация полей
  expected_fields:
    - clinvar.rcv.clinical_significance
    - dbsnp.alleles
    - dbsnp.chrom
    - dbsnp.gene
  last_checked: null
  last_check_ok: null
  impact: medical
  owner: genome_annotator.py
  impacted_uc: [UC-C-02, UC-C-03, UC-K-04]
  notes: |
    Изменение схемы ответа = upstream_drift. Не маскировать как "нет данных".
```

### 3.4 PubMed E-utilities

```yaml
pubmed_eutils:
  category: api_schema
  source: https://www.ncbi.nlm.nih.gov/books/NBK25500/
  local_path: null
  expected_fields:
    - PubmedArticleSet.PubmedArticle.MedlineCitation.PMID
    - PubmedArticleSet.PubmedArticle.MedlineCitation.Article.ArticleTitle
  last_checked: null
  last_check_ok: null
  impact: behavior
  owner: pubmed_client.py
  impacted_uc: [UC-B-07, UC-K-05]
  notes: |
    Изменение API → PMID-верификация переходит в degraded mode.
    GP weekly/monthly помечают has_findings=1 без strong PMID как warning.
```

### 3.5 Anthropic Models

```yaml
anthropic_models:
  category: model
  source: https://docs.claude.com/en/docs/about-claude/models
  models_in_use:
    - claude-sonnet-4-6   # chat, MDT coordinator, GP daily/weekly/monthly
    - claude-haiku-4-5    # arbiter, task extraction, hypothesis generation
    - claude-opus-4-7     # constitutions
  last_checked: null
  last_check_ok: null
  impact: behavior
  owner: hai_core.py
  impacted_uc: [UC-K-03]
  notes: |
    При deprecation любой модели — алерт, не silent fallback.
    Проверка раз в месяц (можно вручную или через RSS Anthropic если появится).
```

### 3.6 Oura API

```yaml
oura_api:
  category: api_schema
  source: https://cloud.ouraring.com/v2/docs
  local_path: null
  expected_fields:
    - sleep.summary_date
    - sleep.total_sleep_duration
    - sleep.deep_sleep_duration
    - daily_activity.steps
    - daily_readiness.score
    - daily_stress.stress_high
  last_checked: null
  last_check_ok: null
  impact: medical
  owner: import_oura.py
  impacted_uc: [UC-A-04, UC-D-02]
  notes: |
    Сон по дате пробуждения (target=today), активность/HRV — yesterday.
    Изменение наименования полей = upstream_drift.
```

### 3.7 Apple Health Export (HAE)

```yaml
apple_health_hae:
  category: api_schema
  source: https://www.healthautoexport.app/    # third-party app, формат меняется реже Oura API
  local_path: ~/Library/Mobile Documents/iCloud~com~ifunography~HealthExport/Documents/Health/
  expected_fields:
    - HKQuantityTypeIdentifierStepCount
    - HKQuantityTypeIdentifierHeartRateVariabilitySDNN
    - HKQuantityTypeIdentifierBloodPressureSystolic
    - HKCategoryTypeIdentifierSleepAnalysis
  last_checked: null
  impact: medical
  owner: import_apple_health.py
  impacted_uc: [UC-A-03, UC-A-05]
  notes: |
    BP через JSON работает только если включён в HAE Settings → Metrics.
    Иначе — через бинарь .hae.
```

### 3.8 Telegram Bot API

```yaml
telegram_bot_api:
  category: api_schema
  source: https://core.telegram.org/bots/api
  expected_fields:
    - update.effective_chat.id
    - update.message.text
    - update.message.photo
    - InlineKeyboardMarkup
  last_checked: null
  impact: infra
  owner: telegram_bot.py
  impacted_uc: [UC-A-01, UC-G-01, UC-I-02, UC-I-06]
  notes: |
    Изменения API ловятся через python-telegram-bot версии.
    Major bump библиотеки → перепроверить InlineKeyboard и send_long.
```

### 3.9 macOS Reminders / Calendar / launchd

```yaml
macos_integration:
  category: os_integration
  source: macOS system frameworks
  components:
    - osascript                # Reminders read/write
    - icalbuddy                # Calendar read
    - launchd                  # scheduled tasks
  last_checked: null
  impact: infra
  owner: task_agent.py, calendar_client.py, calendar_sync.py
  impacted_uc: [UC-G-01, UC-G-02, UC-A-06]
  notes: |
    macOS major upgrade может сломать osascript синтаксис или launchd plist формат.
    Проверять при обновлении OS.
```

### 3.10 Medical Guideline Windows

```yaml
guideline_freshness:
  category: guideline
  source: TESTING_CONTRACTS.md §1
  windows:
    lab_results_reminder_days: 180
    lab_results_alert_days: 270
    genome_update_alert_days: 30
    gp_weekly_max_age_days: 8
    gp_monthly_max_age_days: 35
  last_reviewed: 2026-04-24
  impact: medical
  owner: integrity_tests.py, TESTING_CONTRACTS.md
  impacted_uc: [UC-D-05, UC-A-05, UC-B-08]
  notes: |
    Окна свежести — не код, а медицинское решение.
    Пересматриваются ежегодно или при смене лечащего врача/протокола.
```

---

## 4. Жизненный цикл записи

```
            ┌─────────────────────┐
            │ Новая зависимость   │
            │ обнаружена          │
            └──────────┬──────────┘
                       │
                       ▼
            ┌─────────────────────┐
            │ Запись в реестре    │
            │ status=proposed     │
            │ approved_sha256=null│
            └──────────┬──────────┘
                       │  ручной review
                       ▼
            ┌─────────────────────┐
            │ approved_sha256 =   │
            │ current_sha256      │
            │ last_applied = now  │
            └──────────┬──────────┘
                       │
                       ▼  weekly upstream check
            ┌─────────────────────┐       ┌──────────────┐
            │ current_sha256      │  yes  │ Diff в TG/   │
            │ изменился?          ├──────►│ proposal UC  │
            └──────────┬──────────┘       └──────┬───────┘
                       │ no                       │
                       ▼                          ▼
                  next check          ручной /upstream_apply
                                              │
                                              ▼
                                     approved_sha256 ←
                                     current_sha256
```

---

## 5. Связь с USE_CASES.md

| UC | Что покрывает |
|----|---------------|
| `UC-K-01` | Specialist prompts upstream tracking — основной поведенческий UC |
| `UC-K-02` | Lifestyle prompts |
| `UC-K-03` | Anthropic models routing |
| `UC-K-04` | ClinVar/MyVariant schema drift |
| `UC-K-05` | PubMed E-utilities |
| `UC-K-06` | WellAlly upstream weekly check |
| `UC-X-01` | Реестр существует и валиден |
| `UC-X-02` | Medical-зависимость не применяется молча |
| `UC-X-03` | После обновления — proposal затронутых UC |

---

## 6. Открытые вопросы

(копируются из `USE_CASES.md` §9, дополняются по мере прояснения)

1. ~~Где upstream-источник китайских specialist prompts?~~ ✅ закрыт 2026-05-08: `https://github.com/huifer/WellAlly-health`.
2. Где брать сигнал deprecation Anthropic models? RSS, ручной monthly чек, webhook?
3. Lifestyle prompts (русские) — есть ли отдельный upstream, или они твои собственные (`local_override: true` навсегда)?
4. Реестр в YAML как `external_dependencies.yaml` (отдельный файл) или как фронтматтер каждого UC в `USE_CASES.md`?

---

## 7. История изменений

| Дата | Версия | Изменения |
|------|--------|-----------|
| 2026-05-08 | 0.1 | Первый черновик — 10 минимальных записей реестра, схема полей, lifecycle |
