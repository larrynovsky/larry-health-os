[English](USE_CASES.en.md) · **Русский**

# USE_CASES.md — Прагматики Larry Health OS

**Версия:** 0.3 (финальная рабочая версия)  
**Дата:** 2026-05-08

Этот документ описывает **что система должна делать правильно**. Он не равен
списку уже реализованных тестов и не притворяется, что все медицинское качество
можно проверить `assert`-ами.

Главный принцип: **сначала подтверждаем, что именно тестируем, потом пишем тест**.

---

## 1. Назначение

Larry Health OS — персональная медицинская система владельца (owner-тенант):
импортирует данные из Oura, Apple Health, медицинских документов и генома;
строит ежедневные, еженедельные и месячные медицинские выводы; ведет задачи,
чекины, гипотезы, problem list и консультации Council/MDT.

Use case здесь — это не “шаги теста”. Это **прагматика**: наблюдаемое поведение,
которое имеет пользовательскую или медицинскую ценность.

---

## 2. Правила Документа

### 2.1 Статус UC

Каждый use case имеет статус:

| Статус | Значение | Что делает тестовая система |
|---|---|---|
| `implemented` | Поведение уже есть в коде | Можно создавать regression tests |
| `partial` | Часть поведения есть, часть gap | Создаем тесты на реализованное + gap-report |
| `intended` | Желаемое поведение, еще не реализовано | Не regression. Помечаем как expected gap |
| `speculative` | Идея требует решения | Не генерируем тесты |

### 2.2 Подтверждение

Каждый UC имеет confirmation:

| Confirmation | Значение |
|---|---|
| `proposed` | Предложено системой или человеком, еще не подтверждено |
| `confirmed` | Подтверждено: “да, именно это проверяем” |
| `rejected` | Не тестируем / не нужно |

**Тесты генерируются только для `confirmed`.**

### 2.3 Типы Проверок

| Тип | Значение |
|---|---|
| `check` | Детерминированная проверка модуля или инварианта |
| `integration` | Проверка связи нескольких модулей через данные |
| `e2e_mock` | Сквозной сценарий с моками внешних систем |
| `llm_review` | LLM-as-judge как вспомогательная проверка, не источник истины |
| `manual_charter` | Риск-ориентированный маршрут ревью человеком |
| `meta` | Самообновление каталога и тестов |

### 2.4 Оракулы

Используем RST-разделение checking/testing:

- `B` — negative invariants: что система НЕ должна делать.
- `E` — cross-check с детерминированной правдой: БД, контекст, JSON, API mock.
- `C` — snapshot структуры, где структура важнее точного текста.
- `D` — LLM-as-judge с явными вопросами. D никогда не является единственным оракулом для P0.
- `H` — Human charter: риск-ориентированное ревью человеком по списку вопросов.
  Используется там, где автомат не работает: тон, онкоконтекст, «не приговор».
  H никогда не заменяет B/E для тестируемой части UC и никогда не отдаёт
  ложно-зелёный/красный — его результат всегда заметки и решения.

### 2.5 Тематические алиасы

Часто упоминаемые UC имеют второе имя — тематический префикс. Это
читается с первого взгляда («что это вообще»). Структурный ID (`UC-A-01`)
остаётся первичным; алиас работает как cross-reference.

| Структурный ID | Алиас | Что значит |
|---|---|---|
| `UC-I-01` | `UC-LANG-001` | Наружный язык русский |
| `UC-I-02` | `UC-SEC-001` | Telegram fail-closed |
| `UC-I-03` | `UC-NULL-001` | NULL ≠ 0 |
| `UC-I-08` | `UC-ONCO-001` | Онкоконтекст во всех выводах |
| `UC-I-09` | `UC-GENOME-FRAME-001` | Геном — рамка, не приговор |
| `UC-A-01` | `UC-LABS-001` | Lab-документ → Council полная цепочка |
| `UC-A-04` | `UC-OURA-001` | Сон по дате пробуждения |
| `UC-B-09` | `UC-CORR-001` | Корреляции без выдуманных r |
| `UC-D-05` | `UC-LABS-AGE-001` | Устаревшие labs/genome → пометка даты |
| `UC-G-01` | `UC-TASK-001` | GP → задачи → Reminders с dedup |
| `UC-H-01` | `UC-MDT-001` | Council Round A/B |
| `UC-J-01` | `UC-SELFTEST-001` | Diff кода → proposal UC |
| `UC-J-02` | `UC-SELFTEST-002` | Подтверждение перед тестом |
| `UC-K-01` | `UC-UPSTREAM-PROMPT-001` | Specialist prompts upstream tracking |

### 2.6 Expected Gaps

Если `status=intended` или `partial`, красный результат означает не “тест сломан”,
а “код не соответствует подтвержденному намерению”. Такие проверки идут в gap-report,
а не в блокирующий regression suite.

### 2.7 Конфликт USE_CASES и TESTING_CONTRACTS

Если `USE_CASES.md` и `TESTING_CONTRACTS.md` конфликтуют, система создает
`needs_review`. До подтверждения человеком новый тест не генерируется.

---

## 3. Метаданные UC

Машиночитаемая версия будет жить в `uc_index.yaml`. Для каждого UC:

```yaml
UC-I-01:
  title: "Наружный язык всегда русский"
  status: implemented
  confirmation: confirmed
  priority: P0
  testability: check
  oracle: [B, E]
  modules:
    - telegram_bot.py
    - wellally_consult.py
    - hai_core.py
  data:
    - ".claude/specialists/*.md"
  tests: []
  risk: high
  notes: "Китайские specialist prompts допустимы только внутри."
```

UC без записи в `uc_index.yaml` считается **не покрытым**, даже если он описан
в этом файле.

---

## 4. Каталог UC

### A. Импорт и Распознавание Данных

| ID | Прагматика | P | Тип | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-A-01` | Лаб PDF/JPEG → распознавание → подтверждение → запись в БД | P0 | e2e_mock | partial | proposed |
| `UC-A-02` | Не-лаб PDF классифицируется в правильный домен и не запускает Council | P0 | check | partial | proposed |
| `UC-A-03` | Apple Health / HAE → `daily_metrics`, merge без затирания Oura | P0 | integration | implemented | confirmed |
| `UC-A-04` | Oura: сон по дате пробуждения, активность/HRV по правильной дате | P0 | check | implemented | confirmed |
| `UC-A-05` | Withings BP freshness и тревога при рассинхроне | P1 | check | intended | proposed |
| `UC-A-06` | Calendar/KAYAK trips → `periods`, manual periods не перезаписываются | P1 | integration | partial | proposed |
| `UC-A-07` | Финансовые документы не попадают в медицинские данные | P1 | check | implemented | proposed |

### B. Аналитика и Отчеты

| ID | Прагматика | P | Тип | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-B-01` | Утренний deterministic report: числа в тексте = числа в БД | P0 | check | partial | proposed |
| `UC-B-02` | GP daily видит контекст 7/14/30/90 дней, labs, problem list, genome | P0 | integration | partial | proposed |
| `UC-B-03` | `_triage_metric` — снят 28.09: тренды идут в разбор, не в задачи | P2 | check | intended | rejected |
| `UC-B-04` | Работа системы не кладётся в список задач человека | P0 | check | implemented | confirmed |
| `UC-B-05` | Lifestyle agents молчат без доменных данных | P0 | check | implemented | proposed |
| `UC-B-06` | Lifestyle agents получают domain-specific genome/promethease block | P1 | integration | implemented | proposed |
| `UC-B-07` | GP weekly синтезирует MDT, labs, problem list, tasks, genome, freshness | P0 | integration | partial | proposed |
| `UC-B-08` | GP monthly дает стратегию 30/90 дней и пересмотр problem list | P1 | integration | partial | proposed |
| `UC-B-09` | Корреляции трекеров не содержат выдуманных r-значений | P0 | check | partial | proposed |
| `UC-B-10` | Evening checkin: 1 контекстный вопрос, до 3 turns, structured extraction | P1 | e2e_mock | partial | proposed |
| `UC-B-11` | Checkin следующего утра виден GP daily | P0 | integration | partial | proposed |

### C. Геном

| ID | Прагматика | P | Тип | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-C-01` | 23andMe TSV полностью парсится, double-tab не теряется | P0 | check | implemented | proposed |
| `UC-C-02` | Annotator + ClinVar + FUNCTIONAL_WHITELIST | P0 | integration | implemented | proposed |
| `UC-C-03` | Genome update monthly: изменения ClinVar логируются, significant upward объясняется | P0 | e2e_mock | partial | proposed |
| `UC-C-04` | Genome context для AI domain-specific, без нерелевантного шума | P1 | check | implemented | proposed |

### D. Алерты и Safety

| ID | Прагматика | P | Тип | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-D-01` | `safety_net.lab_alerts` детерминированно ловит свежие lab-флаги | P0 | check | partial | proposed |
| `UC-D-02` | `evaluate_domain_need`: percentile + floors + constraints | P0 | check | implemented | proposed |
| `UC-D-03` | Срочный safety alert отправляется до основного отчета | P0 | e2e_mock | partial | proposed |
| `UC-D-04` | `triage_agent` чинит WARN, не лечит FAIL, идемпотентен по дню | P0 | e2e_mock | implemented | confirmed |
| `UC-D-05` | Устаревшие labs/genome явно помечаются датой и сниженной уверенностью | P0 | check | partial | confirmed |

### E. Constitutions

| ID | Прагматика | P | Тип | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-E-01` | 5 конституций: двухшаговая генерация text + diff, без SNP/longitudinal не стартует | P1 | integration | implemented | proposed |
| `UC-E-02` | Источник SNP = `genetic_variants`, не пустая `promethease_variants` | P0 | check | implemented | proposed |
| `UC-E-03` | `_run_alert_review()` после генерации 5 конституций поднимает критичные находки | P1 | check | implemented | proposed |

### F. Гипотезы и Эксперименты

| ID | Прагматика | P | Тип | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-F-01` | Drift streak рождает гипотезу `mechanism + prediction + test` | P1 | check | partial | proposed |
| `UC-F-02` | Гипотеза проходит lifecycle: open → testing → confirmed/rejected | P1 | check | partial | proposed |

### G. Задачи и Reminders

| ID | Прагматика | P | Тип | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-G-01` | GP report → tasks → Reminders, dedup по fingerprint | P0 | e2e_mock | partial | proposed |
| `UC-G-02` | `reminders_sync.py` каждые 3ч закрывает выполненные задачи | P1 | integration | implemented | proposed |
| `UC-G-03` | Problem list управляемый: GP предлагает, человек approve/reject | P1 | integration | partial | proposed |
| `UC-G-04` | Active protocols + patient_constraints реально влияют на рекомендации | P1 | integration | implemented | proposed |

### H. `/consult` v2 — Deliberative Council

| ID | Прагматика | P | Тип | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-H-01` | `/consult <Q>` → 13 участников, Round A/B, coordinator | P0 | e2e_mock | implemented | proposed |
| `UC-H-02` | `user_qa.append()` строго до `_build_data_package()` и до раундов | P0 | check | implemented | confirmed |
| `UC-H-03` | Round B действительно использует мнения Round A | P1 | manual_charter | partial | proposed |
| `UC-H-04` | Многошаговый consult сохраняет user_qa между поворотами; рестарт сбрасывает явно | P1 | integration | partial | proposed |

### I. Кросс-Функциональные Инварианты

| ID | Прагматика | P | Тип | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-I-01` | Наружный язык всегда русский; китайские промпты только внутри | P0 | check | implemented | confirmed |
| `UC-I-02` | Telegram fail-closed: только `OWNER_CHAT_ID` | P0 | check | implemented | confirmed |
| `UC-I-03` | NULL != 0 во всем слое отчетов | P0 | check | implemented | confirmed |
| `UC-I-04` | `integrity_tests --json` блокирует morning report при FAIL | P0 | e2e_mock | partial | proposed |
| `UC-I-05` | Backup не ломает SQLite/WAL и не создает split-brain | P0 | check | partial | proposed |
| `UC-I-06` | `send_long` режет Telegram-сообщения по абзацам без обрезаний | P1 | check | implemented | proposed |
| `UC-I-07` | Single-primary: production DB пишет только Studio | P0 | manual_charter + check | implemented | confirmed |
| `UC-I-08` | Онкоконтекст присутствует во всех LLM-выводах | P0 | manual_charter | partial | proposed |
| `UC-I-09` | Геном — рамка интерпретации, не приговор | P1 | manual_charter | partial | proposed |

### J. Meta — Самоподдержание Каталога

| ID | Прагматика | P | Тип | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-J-01` | Diff кода/доков → proposal UC, без автозаписи тестов | P0 | meta | intended | confirmed |
| `UC-J-02` | Перед генерацией теста — явное подтверждение “что именно тестируем” | P0 | meta | intended | confirmed |
| `UC-J-03` | Каждый confirmed UC связан с modules/data/tests/oracle/risk в YAML | P0 | meta | intended | proposed |

### K. Upstream Tracking — Внешние Артефакты

Система использует внешние или полу-внешние элементы, которые меняются независимо
от нашего кода: китайские specialist prompts, lifestyle prompts, Anthropic models,
ClinVar/MyVariant, PubMed, Oura/Apple schemas, Telegram API, macOS Reminders/Calendar,
guideline-пороговые окна.

| ID | Прагматика | P | Тип | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-K-01` | Китайские specialist prompts отслеживаются по upstream diff, apply только вручную | P0 | e2e_mock | partial | confirmed |
| `UC-K-02` | Lifestyle prompts отслеживаются отдельно, с hash и diff | P1 | check | intended | proposed |
| `UC-K-03` | Anthropic model routing и deprecation мониторятся | P1 | check | intended | proposed |
| `UC-K-04` | ClinVar/MyVariant schema drift не маскируется как “нет данных” | P0 | check | partial | proposed |
| `UC-K-05` | PubMed E-utilities degradation помечает PMID verification как degraded | P1 | check | intended | proposed |
| `UC-K-06` | WellAlly upstream weekly check показывает diff, не применяет молча | P1 | e2e_mock | partial | proposed |

---

## 5. Развернутые P0 UC

Ниже P0-контракты. Для `intended`/`partial` это не блокирующий regression test,
а acceptance target + gap-report.

### UC-A-01 — Лаб PDF/JPEG → Распознавание → Подтверждение → Запись в БД

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `import_all.py`, `lab_extractor.py`, `health_db.py`, `telegram_bot.py`

**Прагматика:** я кладу анализ, система достаёт цифры, показывает мне карточку
для подтверждения и сохраняет в БД. После этого данные доступны GP/Council
по расписанию и через ручной `/consult`.

**Given:**
- watcher запущен;
- документ похож на лабораторный PDF/JPEG;
- есть или отсутствуют предыдущие labs — оба случая должны корректно обрабатываться;
- `genetic_variants` может быть пуст или непуст, но система не должна выдумывать геном.

**When:** новый lab PDF/JPEG появляется в health-папке.

**Then:**
1. `import_all.py` классифицирует документ как lab только при достаточных сигналах (≥3 маркеров).
2. Извлекается структурированный JSON: `{name, value, unit, ref_low, ref_high, flagged, confidence, raw_line}`.
3. Низкая уверенность (`confidence="low"`) требует user approval и показывает `raw_line` в карточке.
4. После approval данные попадают в `lab_results` без дублей (idempotency по content_hash).
5. `set_import_status("oncology_pdf")` обновляется только после успешного импорта.

**B:**
- <3 lab-сигналов → lab import не запускается, документ классифицируется в другой домен (UC-A-02).
- повторный файл по content hash не создаёт дубль.
- approval отвергнут (`❌`) → запись в `lab_results` не делается.

**E:**
- Значения в `lab_results` совпадают с OCR/raw_line после нормализации десятичных.
- После approval `safety_net.lab_alerts()` видит новые значения.

**Self-consistency check** (вместо snapshot, 2026-05-08): двойной прогон `lab_extractor` на эталонном PDF от каждой лаборатории; ключевые поля `{name, value, unit, flagged}` должны совпадать бит-в-бит между запусками. Расхождения = нестабильность extractor-а, не «эталон неактуален».

**Не входит в этот UC, отложено в BACKLOG:** автоматический запуск Council после
approval. См. BACKLOG.md → «Автоматический Council после import анализов» (2026-05-08).
Сейчас: после approval Council запускается либо по расписанию (вс 23:00 MDT), либо
вручную через `/consult`. Тестируется отдельно через UC-H-01.

---

### UC-A-02 — Не-лаб PDF Классифицируется и Не Идет в Council

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `import_all.py`

**Then:**
- discharge/pathology/biopsy/PET/nutrition guide сохраняются в свой домен;
- hospital bills и financial docs пропускаются;
- Council не запускается.

**B:** OCR error не должен создавать пустой “валидный” медицинский JSON.  
**E:** `index.json.imported_files` не должен ссылаться на несуществующие data-файлы.

---

### UC-A-03 — Apple Health / HAE → Daily Metrics

**Status:** `partial`  
**Confirmation:** `confirmed`  
**Owner:** `import_apple_health.py`, `health_db.py`

**Then:**
- HAE/Apple Health данные за последние дни читаются и merge'ятся в `daily_metrics`;
- существующие Oura-поля не затираются;
- `set_import_status("apple_health")` обновляется только после успешного импорта;
- отсутствующий BP остается `NULL`, не превращается в 0.

**B:** iCloud-conflicted/evicted файлы не валят весь импорт.  
**E:** шаги и ключевые метрики в БД совпадают с источником в допустимой толерантности.

**Подтверждённый риск (валидация 2026-05-08):**
- `import_apple_health.py:248` — `existing.update(summary)` это shallow merge.
  Если HAE прислал JSON с `{"hrv": null, "sleep_total": 7.5}`, а существующий
  файл содержит `{"hrv": 25, "steps": 8000}` (от Oura) — после `update()` получим
  `{"hrv": null, "sleep_total": 7.5, "steps": 8000}`. **Oura HRV перезаписан на null.**
- Нужен фильтр: «новое значение перезаписывает только если `not None`».
- Тест: pre-fill `hrv=25` → import HAE без `hrv` → `hrv` остаётся `25`.

**Дополнительно (не критично):** `import_daily_new_automation()` (строка 282) использует
папку `New Automation/`, хотя правило известно: «**НЕ** `New Automation/` — та папка
не используется». Возможно legacy-код, требует ревью на удаление.

---

### UC-A-04 — Oura: Дата Сна и Дата Активности

**Status:** `implemented`  
**Confirmation:** `proposed`  
**Owner:** `import_oura.py`, `gp_agent.py`, `health_db.py`

**Then:**
- сон записывается на дату пробуждения;
- daily report использует `sleep_date=today`;
- activity/steps/readiness/stress берутся с той даты, где источник действительно завершен;
- отсутствие данных не интерпретируется как реальный 0.

**B:** “0 часов сна” или “HRV 0” в нарративе запрещены, если источник отсутствует.  
**E:** `daily_metrics[today].sleep_total` соответствует Oura sleep summary, заканчивающемуся сегодня.

---

### UC-B-01 — Deterministic Morning Report: Числа = БД

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `morning_report.py` — модуль ретайрен 13.07 и удалён 30.09.2026; сценарий снят вместе с ним, утренний текст пишет GP-бриф

**Then:**
- deterministic report не вызывает LLM;
- числа в тексте берутся из `daily_metrics` или явно названного окна;
- `triage_agent.run_triage()` выполняется до генерации;
- отчет сохраняется в `data/reports/YYYY-MM-DD.md`.

**B:** NULL-метрика → “нет данных”, а не ноль.  
**E:** парсер чисел из отчета сопоставляет каждое число с БД/окном.

---

### UC-B-02 — GP Daily Видит Контекст 7/14/30/90

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `gp_agent.py`, `health_db.py`, `genome_context.py`

**Then:**
- контекст GP содержит текущие метрики, 7/30/90 статистику, labs freshness,
  problem list, protocols/constraints, genome block, workouts/stress/checkins при наличии;
- GP не утверждает выводы по отсутствующей секции;
- устаревшие labs/genome помечаются датой.

**B:** `has_findings=1` без evidence/PubMed там, где контракт требует PubMed, идет в warning/fail.  
**E:** числовые утверждения в GP-тексте должны быть восстановимы из переданного контекста.

---

### UC-B-03 — `_triage_metric` (снят)

**Status:** `intended`  
**Confirmation:** `rejected`  

Снят 28.09 решением владельца: классификатор тренда нужен был только для автозадач (UC-B-04
в прежней редакции) и путал направление — «ухудшается» ставил и при росте показателя.
Тренды 7/14/30/90 дней идут в контекст еженедельного разбора (`gp_context._build_trends_block`).

---

### UC-B-04 — Работа Системы Не Кладётся в Список Человека

**Status:** `implemented`  
**Confirmation:** `confirmed`  
**Owner:** `gp_agent.py`, `jobs/scheduled.py`

**Then:** ни один производственный вызов `save_task` не создаёт задачу типа `analysis`/`review`
(«разобрать изменения», «пересмотреть гипотезы») — у таких задач нет исполнителя-человека.
Решение владельца 28.09: в списке человека — только то, что он может сделать сам.

**B:** подложенный вызов с типом `analysis` сторож находит.

---

### UC-B-05 — Lifestyle Agents Молчат Без Данных

**Status:** `implemented`  
**Confirmation:** `proposed`  
**Owner:** `lifestyle_agents.py`

**Then:**
- агент возвращает `None`/пустой результат, если нет доменных данных;
- genome block сам по себе не создает бриф;
- GP получает список missing agents.

**B:** отсутствующие сенсоры не превращаются в “плохой сон/низкую активность”.  
**E:** непустой brief требует наличия минимальных доменных данных.

---

### UC-B-07 — GP Weekly

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `gp_agent.py`, `wellally_consult.py`, `task_agent.py`

**Then:**
- weekly использует MDT synthesis при наличии;
- если MDT отсутствует, weekly явно помечает gap и работает на доступном контексте;
- задачи из отчета извлекаются и дедуплицируются;
- weekly не является суммой daily reports.

**E:** недельные числовые утверждения совпадают с реальным окном.

---

### UC-B-09 — Корреляции Без Выдуманных r

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `longitudinal_analysis.py`, `gp_agent.py`

**Then:**
- если текст содержит числовое `r=...`, оно должно быть в `agent_reports.raw_output`,
  пересчитано на данных или отсутствовать;
- без источника можно говорить о “возможном паттерне”, но не о точной корреляции.

**B:** запрещены точные r-значения “на глаз”.  
**E:** `r` проверяется с толерантностью на том же окне и тех же метриках.

---

### UC-B-11 — Checkin Видим GP

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `checkin_agent.py`, `health_db.py`, `gp_agent.py`

**Then:** вечерний checkin сохраняется в `checkins`/`context_events` и виден
следующему GP daily.

**E:** если `COUNT(checkins WHERE date=yesterday) > 0`, контекст GP содержит checkin-секцию.

---

### UC-C-01 — 23andMe TSV Полностью Парсится

**Status:** `implemented`  
**Confirmation:** `proposed`  
**Owner:** `genome_parser.py`

**Then:** `raw_snps` содержит все data-строки TSV после исключения `#`-комментариев.

**B:** double-tab строки не теряются; `genotype="--"` не выбрасывается как ошибка.  
**E:** count в БД = count строк источника.

---

### UC-C-02 — Genome Annotator + Whitelist

**Status:** `implemented`  
**Confirmation:** `proposed`  
**Owner:** `genome_annotator.py`

**Then:**
- FUNCTIONAL_WHITELIST попадает в `genetic_variants`;
- ClinVar pathogenic/risk варианты попадают независимо от whitelist;
- domain tags заполнены.

**B:** API failure не должен писать полу-валидные данные как “benign”.  
**E:** genotype в `genetic_variants` совпадает с `raw_snps` для эталонных rsid.

---

### UC-C-03 — Genome Update Monthly

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `genome_update_agent.py`, `telegram_bot.py`, `triage_agent.py`

**Then:**
- проверяются значимые варианты;
- все изменения логируются;
- significant upward movement создает понятный русский нарратив;
- если API не отвечает, успешный `run_date` не обновляется.

**B:** отсутствие изменений не спамит пользователя.  
**E:** changed variants в БД соответствуют `genome_update_log`.

---

### UC-D-01 — Safety Net Labs

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `safety_net.py`

**Then:** свежие lab flags попадают в deterministic alert list и доступны GP/Council.

**B:** NULL не считается флагом.  
**E:** alerts ⊆ flagged lab rows.

---

### UC-D-02 — Domain Need: Percentile + Floors + Constraints

**Status:** `implemented`  
**Confirmation:** `proposed`  
**Owner:** `telegram_bot.py`, `recommendation_engine.md`, `health_db.py`

**Then:** domain recommendation объясняется через percentile signals,
absolute floors и active constraints/protocols.

**B:** без baseline нельзя уверенно говорить о percentile.  
**E:** для каждого алерта можно восстановить причины из БД.

---

### UC-D-03 — Срочный Safety Alert До Отчета

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `gp_agent.py`, `telegram_bot.py`, `safety_net.py`

**Then:** urgent/critical safety message отправляется до обычного daily report.

**B:** safety_net failure не должен молча исчезать.  
**E:** timestamp safety message < timestamp report.

---

### UC-D-04 — Triage Agent

**Status:** `implemented`  
**Confirmation:** `proposed`  
**Owner:** `triage_agent.py`, `run_checks.sh`, `integrity_tests.py`

**Then:**
- WARN может запускать автофиксы или вопрос пользователю;
- FAIL не лечится автоматически как WARN;
- daily flag защищает от повторного спама.

**E:** каждое действие triage имеет лог результата.

---

### UC-D-05 — Устаревшие Источники Помечаются Датой

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** все агенты, использующие labs/genome

**Then:** выводы по labs/genome старше контракта содержат дату источника и ограничение уверенности.

**B:** запрещены уверенные медицинские выводы на stale source без пометки.  
**D:** LLM-review ищет stale source без даты.

---

### UC-E-02 — Constitutions Читают `genetic_variants`

**Status:** `implemented`  
**Confirmation:** `proposed`  
**Owner:** `generate_constitutions.py`

**Then:** SNP source = `genetic_variants`; пустая `promethease_variants` не используется как fallback.

**E:** SNP в constitution ⊆ SNP в `genetic_variants` с релевантным domain tag.

---

### UC-G-01 — GP Report → Tasks → Reminders

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `task_agent.py`, `health_db.py`, `reminders_sync.py`

**Then:**
- task extractor возвращает валидный JSON задач;
- задачи сохраняются с fingerprint;
- Reminders создаются в списке Health;
- дубли по fingerprint не плодятся.

**Важно:** текущий код сначала сохраняет task, потом создает Reminder. Если нужна атомарность,
это отдельный intended gap, а не текущий regression.

**B:** задача без content не сохраняется; абстрактные советы не превращаются в задачи.  
**E:** open task с reminder-required должен иметь `[task_id:N]` в Reminders или gap flag.

---

### UC-H-01 — Council Полный Цикл

**Status:** `implemented`  
**Confirmation:** `proposed`  
**Owner:** `wellally_consult.py`, `lifestyle_agents.py`, `telegram_bot.py`

**Then:**
- session сохраняет user question/answer;
- `_build_data_package()` видит историю;
- Round A независимый;
- Round B видит коллег;
- coordinator синтезирует на русском;
- китайские specialist prompts читаются как внутренний артефакт, но не протекают наружу.

**B:** пустой genome не позволяет делать genome claims.  
**E:** числовые утверждения финала должны быть в context.

---

### UC-H-02 — `user_qa.append()` До Раундов

**Status:** `implemented`  
**Confirmation:** `proposed`  
**Owner:** `wellally_consult.py`

**Then:** новый user answer попадает в session до `_build_data_package()` и до `asyncio.gather`.

**E:** unit-test с 3 поворотами проверяет, что data package видит свежий `user_qa`.

---

### UC-I-01 — Наружный Язык Всегда Русский

**Status:** `partial`  
**Confirmation:** `confirmed`  
**Owner:** `telegram_bot.py`, `wellally_consult.py`, `hai_core.py`, `.claude/commands/*`

**Then:** все пользовательские выходы — Telegram, отчеты, ошибки, `/consult`,
`/specialist`, `/query` — на русском.

**B:**
- CJK-текст не должен попадать в пользовательский output.
- Английские термины, rsID, названия моделей, единицы измерения допустимы как вставки.

**E:** language detector + CJK leakage detector по наружным сообщениям.

---

### UC-I-02 — Telegram Fail-Closed

**Status:** `implemented`  
**Confirmation:** `proposed`  
**Owner:** `telegram_bot.py`

**Then:** только `OWNER_CHAT_ID` проходит handlers.

**B:** unauthorized chat не получает персональные данные, кнопки approval или AI-ответы.  
**E:** mock update с чужим chat_id → 0 исходящих сообщений с данными пациента.

---

### UC-I-03 — NULL != 0

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** все report/agent modules

**Then:** NULL в БД = “нет данных”, не реальный ноль.

**B:** “спал 0 часов”, “HRV 0” и похожие фразы запрещены при missing source.  
**E:** mock context со всеми NULL не порождает нулевых утверждений.

**Подтверждённый баг (валидация 2026-05-08):**
- `gp_agent.py:478` — `int((stats.get('avg_deep') or 0)*60)` в f-string без guard:
  при NULL `avg_deep` за 7д вывод содержит «Deep: 0 / 50 / 60 / 70 мин» — медицински
  неверное утверждение про 0 минут глубокого сна.
- `gp_agent.py:389` — аналогично для `s.get('deep') or 0` в дневном табличном выводе.
- `gp_agent.py:527-528` — `_avg_s = _sr["avg_stress"] or 0` в строке про стресс/нагрузку.

`morning_report.py` защищён через `if hrv:` / `if sleep_t:` (строки 186, 201) — там
NULL не превращается в 0. Только `gp_agent.py` (Tier-1 исправлений) имеет дыры.

---

### UC-I-04 — Integrity Gate

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `run_checks.sh`, `integrity_tests.py`, launchd

**Then:** FAIL до morning report блокирует отчет и отправляет алерт; WARN не блокирует.

**E:** mock FAIL → report не отправлен, alert отправлен.

---

### UC-I-05 — Backup Без Split-Brain

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `backup.sh`, launchd, infrastructure rules

**Then:** backup делает атомарный SQLite snapshot и не создает вторую operational primary DB.

**B:** plain copy активной WAL-БД запрещен как production backup strategy.

---

### UC-I-07 — Single Primary

**Status:** `partial`  
**Confirmation:** `proposed`  
**Type:** `manual_charter` + минимальный guard в коде  
**Oracle:** `H` + `B` (для guard)  
**Owner:** все import/db writers, операционная дисциплина

**Прагматика:** в БД пишет только Claude через Cowork с правильной машины (Studio).
Других writer-ов нет. Защита нужна не от системы, а от случайной ошибки самого
Claude (например, забыть правило Studio-only).

**Защитные слои:**
1. `BLUEPRINT.md` правило «MacBook = разработка, Studio = production».
2. Память Claude (`feedback_health_scripts_protocol.md`).
3. **Guard в импортёрах** (тестируемый): в начале каждого скрипта, который
   пишет в `health.db`, стоит `if socket.gethostname() != STUDIO: raise`.
   При попытке запуска не на Studio импорт явно падает с понятным сообщением.

**Тестируется (B):** unit-тест на guard — mock `socket.gethostname()` →
non-Studio → expected `RuntimeError` с упоминанием BLUEPRINT.md.

**Charter (H) маршрут:** `tests/charters/CH-PRIMARY-01.md` —
если БД оказалась в плохом состоянии, проверить WAL audit, найти timestamp
последнего write, сверить с git log импорт-скриптов.

**Что НЕ делаем:** миграция БД с колонкой `origin_host` — overkill для системы с одним writer-ом.

---

### UC-I-08 — Онкоконтекст во Всех LLM-Выводах

**Alias:** `UC-ONCO-001`  
**Status:** `partial`  
**Confirmation:** `proposed`  
**Type:** `manual_charter`  
**Oracle:** `H`  
**Owner:** GP/Council/specialist prompts, `wellally_consult.py`, `gp_agent.py`

**Прагматика:** выводы должны учитывать диагноз, лечение и статус заболевания
ПАЦИЕНТА — из его данных (онко-статус, терапия, онкомаркеры, визуализация), не из
зашитого профиля. Для здорового человека и для пациента с онкологическим контекстом —
разные интерпретации одних и тех же чисел. (brief-neutralization: конкретные значения
метрик/дат берутся из карты пациента, в спеке не хардкодятся.)

**Review Questions** (запускать ежемесячно и после крупных изменений
prompts/Council/GP):

- Не звучит ли вывод как для здорового человека без коррекции на диагноз пациента?
- Аномальная ВСР трактуется как след лечения (из контекста пациента), или как патология без контекста?
- Сниженный глубокий сон объясняется с учётом лечения, или драматизируется?
- Онкомаркеры/визуализация упоминаются с датами и осторожно?
- Учитывается ли текущий статус заболевания пациента (ремиссия/активное — из его данных)?

**Красный флаг:** вывод как для здорового пациента без коррекции на
лечение и онкологический контекст пациента.

**Связанный обобщающий маршрут:** §6 `CH-ONCO-01` (когда запускаем).

---

### UC-I-09 — Геном Не Приговор

**Alias:** `UC-GENOME-FRAME-001`  
**Status:** `partial`  
**Confirmation:** `proposed`  
**Type:** `manual_charter`  
**Oracle:** `H`  
**Owner:** genome narrative, constitutions, GP/Council outputs

**Прагматика:** геномная информация — это рамка интерпретации, а не диагноз
и не прогноз. SNP с pathogenic клинической значимостью требует пояснения
вероятностного характера, а не «у вас точно будет X».

**Review Questions:**

- Используется ли вероятностный язык («может», «связано с»), а не категоричный?
- Не поднимается ли паника на functional/benign-варианте?
- Соответствует ли тон «информирование», а не «прогноз»?
- Если SNP действительно pathogenic — есть ли пояснение, что это
  относительный риск, а не неизбежность?

**Красный флаг:** «у вас точно будет X», «это означает что...» вместо
«может повышать риск».

**Связанный обобщающий маршрут:** §6 `CH-GENOME-01`.

---

### UC-J-01 — Diff → Proposal UC

**Status:** `intended`  
**Confirmation:** `confirmed`  
**Owner:** будущий `propose_uc.py`, `doc_agent.py`, git hook

**Then:** изменение кода/документов предлагает новые или измененные UC, но не пишет тесты
и не меняет confirmed-контракты без человека.

---

### UC-J-02 — Подтверждение Перед Тестом

**Status:** `intended`  
**Confirmation:** `confirmed`

**Then:** генератор тестов показывает: что проверяем, какой оракул, какие моки,
какой blast radius. Только после подтверждения пишет тест.

---

### UC-J-03 — YAML Индекс Покрытия

**Status:** `intended`  
**Confirmation:** `proposed`

**Then:** каждый confirmed UC имеет запись в `uc_index.yaml`, а coverage report показывает:
covered / not covered / expected gap / needs review.

---

### UC-K-01 — Specialist Prompts Upstream Tracking

**Status:** `intended`  
**Confirmation:** `confirmed`  
**Owner:** будущий `upstream_watcher.py`, `.claude/specialists/*.md`

**Прагматика:** китайские specialist prompts — часть системы. Их обновления могут
изменить медицинское поведение, поэтому система должна отслеживать upstream,
показывать diff и применять только вручную.

**Then:**
1. Реестр external dependencies содержит source, local path, approved hash,
   current hash, last_checked, last_applied.
2. Weekly check сравнивает upstream и локальную копию.
3. Diff отправляется в Telegram/отчет.
4. Apply только после ручного подтверждения.
5. После apply запускаются связанные UC: язык наружу русский, Council, онкоконтекст.

**B:**
- prompt update не применяется молча;
- local override не затирается;
- upstream failure не обновляет `last_check_ok`.

**E:** local hash = approved hash или pending update явно зарегистрирован.

---

### UC-K-04 — ClinVar/MyVariant Schema Drift

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `genome_annotator.py`, `genome_update_agent.py`

**Then:** изменение схемы API не маскируется как “нет вариантов” или “benign”.

**B:** missing required fields → degraded/warn, не silent success.  
**E:** mock измененной схемы вызывает upstream_drift warning.

---

## 6. Charters

Charter — это тестирование, а не check. Его результат — заметки и решения, а не
ложный зеленый/красный автомат.

### CH-ONCO-01 — Онкоконтекст

Проверить GP daily/weekly/monthly и Council (для пациента с онко-контекстом — из его данных):

- учитывается ли диагноз и стадия пациента (из карты проблем);
- учитывается ли проведённая терапия и пост-химиотерапевтический контекст;
- аномальная ВСР не трактуется как «норма здорового человека» и не драматизируется;
- сниженный глубокий сон объясняется с учётом лечения;
- онкомаркеры/визуализация используются осторожно и с датами.

Запуск: ежемесячно и после крупных изменений prompts/Council/GP.

### CH-DAILY-01 — Не Драматизирует Единичный Выброс

Взять дни с единичным плохим HRV/sleep. Проверить, что GP отличает шум от тренда
и сохраняет спокойный медицинский тон.

### CH-GENOME-01 — Геном Не Приговор

Проверить genome narrative и constitutions: вероятностный тон, отсутствие паники,
нет “у вас точно будет X”.

### CH-MDT-01 — Round B Слышит Коллег

Сравнить Round A/B в нескольких consultations:

- есть ли согласие/несогласие с коллегами;
- меняются ли позиции;
- не повторяет ли Round B механически Round A.

### CH-CORR-01 — Корреляции Не Магия

Если есть r-число — его проверяет `UC-B-09`. Если числа нет — человек смотрит,
не является ли это cherry-picking совпадений.

---

## 7. External Dependencies

Будущий файл: `external_dependencies.yaml`.

```yaml
specialist_prompts:
  oncology:
    local_path: ".claude/specialists/oncology.md"
    upstream: "TBD"
    language: "zh"
    approved_sha256: "TBD"
    current_sha256: "TBD"
    last_checked: null
    last_applied: null
    local_override: false
    impacted_uc:
      - UC-H-01
      - UC-I-01
      - UC-I-08
      - UC-K-01
```

Минимальный набор upstream-зависимостей:

- `.claude/specialists/*.md` — китайские врачебные промпты;
- `.claude/specialists/lifestyle_*.md` — lifestyle prompts;
- Anthropic model routing;
- ClinVar/MyVariant API schemas;
- PubMed E-utilities;
- Oura API schema;
- Apple Health / HAE schema;
- Telegram Bot API;
- macOS Reminders / Calendar / launchd;
- guideline freshness windows.

---

## 8. Первый Пакет Подтверждения

Перед генерацией тестов подтвердить или отредактировать:

1. `UC-I-01` — наружу только русский.
2. `UC-I-02` — Telegram только owner.
3. `UC-A-04` — Oura дата сна/активности.
4. `UC-A-03` — Apple Health merge.
5. `UC-B-03` — фактический алгоритм `_triage_metric`.
6. `UC-B-04` — автозадачи по трендам.
7. `UC-D-04` — triage agent.
8. `UC-H-02` — `user_qa` до раундов.
9. `UC-J-01` / `UC-J-02` — proposal + подтверждение перед тестом.
10. `UC-K-01` — upstream tracking китайских prompts.

---

## 9. Открытые Вопросы

1. Где upstream-источник китайских specialist prompts?
2. Нужна ли атомарность `task -> Reminder -> DB`, или текущий best-effort приемлем?
3. Какой статус у lab approval pipeline: строим новый или фиксируем текущий OCR/import как interim?
4. Где брать сигнал deprecation Anthropic models: ручной monthly check или официальный источник?
5. Charters остаются здесь или выносятся в `CHARTERS.md`?

---

## 10. История Изменений

| Дата | Версия | Изменения |
|---|---|---|
| 2026-05-08 | 0.1 | Первый черновик UC |
| 2026-05-08 | 0.2 | Объединенный план: каталог, P0, charters, meta, upstream |
| 2026-05-08 | 0.3 | Финальная рабочая версия: status/confirmation, expected gaps, внешние зависимости, исправлены опасные расхождения с кодом |
| 2026-05-08 | 0.4 | Объединено с Codex: oracle `H` (Human charter); тематические алиасы (§2.5); встроенные Review Questions для UC-I-08/UC-I-09 |
