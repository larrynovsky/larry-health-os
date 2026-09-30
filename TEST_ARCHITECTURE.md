[English](TEST_ARCHITECTURE.en.md) · **Русский**

# TEST_ARCHITECTURE.md — Архитектура и план работ автотестов Larry Health OS

**Версия:** 0.1 | **Дата:** 2026-05-08

> Этот файл описывает **как именно** мы реализуем тесты, проектированные в
> `USE_CASES.md`. Опирается на терминологию consistency моделей (глава 7
> Таненбаума) — потому что в системе есть реальные распределённые свойства,
> которые тесты обязаны учитывать.

---

## 1. Распределённые свойства Health OS, влияющие на тесты

Прежде чем писать архитектуру тестов — зафиксируем, **что в нашей системе вообще распределённое**. Без этого тесты будут писаться «как для монолита» и пропускать целый класс багов.

| Место | Концепт (по Таненбауму) | Что это значит для тестов |
|---|---|---|
| TC §1 свежесть данных (oura ≤26ч, etc.) | **Conit** + **staleness deviation** | Тесты генерируют данные с контролируемой давностью и проверяют реакцию |
| `pending_labs/` → approval → `lab_results` | **Tentative-запись** | Tentative — отдельный наблюдаемый объект; тест проверяет переход tentative → committed |
| MacBook ↔ Studio rsync кода | **Lazy replication** | Тест должен фиксировать, что прод-логика читается на Studio, не на MacBook |
| iCloud-синк `~/health/` | **Lazy replication** + **Eventual consistency** | UC-A-01: watcher на Studio триггерится после iCloud propagation, не мгновенно |
| БД на Studio как primary | **Single-primary** (Primary-Backup) | UC-I-07: попытка записи с MacBook = fail. Тест проверяет origin |
| Tasks ↔ Reminders каждые 3ч | **Eventual consistency** + **Pull-обновление** | Тест принимает sync window, не «мгновенно» |
| Council Round B видит Round A | **Causal consistency** | Round B нельзя запустить до Round A — нарушение причинного порядка |
| `_build_gp_context()` читает много источников | **Read consistency** (потенциально) | Если контекст собран из источников разной давности — тест должен это замечать |
| Checkin (вечер) → GP daily (утро следующий день) | **Read-your-writes (RYW)** для одного пользователя | UC-B-11: GP видит мой checkin. Тест: записал → прочитал |
| Approval лаб-данных → утренний отчёт | **Read-your-writes (RYW)** | После ✅ значения видны в `daily_metrics` к 08:00 |

**Главная импликация:** mock «у нас одна БД, одна память» неправилен. Тесты для UC-A-01, UC-G-02, UC-H-01 должны эмулировать **временной разрыв** между записью и чтением, а не предполагать атомарность.

---

## 2. Слои тестирования (test pyramid)

Снизу вверх — от быстрых и многочисленных к медленным и редким.

### Слой 0 — Static checks (вне pyramid)
Это **уже есть** в `check_contracts.py`. Не трогаем — работает как pre-commit gate.

### Слой 1 — Unit (`check`)
- Изолированный модуль, mock зависимостей.
- Быстрые (<100мс на тест), запускаются на каждый коммит.
- Покрывают: `B` (negative invariants), `E` (cross-check) для ОДНОГО модуля.
- **Примеры:**
  - работа системы не кладётся в список задач человека (UC-B-04; UC-B-03 снят 28.09)
  - `classify()` возвращает `lab` при ≥3 сигналах (UC-A-01 шаг 2)
  - `is_financial()` фильтрует bills (UC-A-07)
  - language detector на наружном тексте (UC-I-01)
  - `OWNER_CHAT_ID` фильтр (UC-I-02)

### Слой 2 — Integration (`integration`)
- Несколько модулей через данные.
- В первую очередь — связь через `health.db` (in-memory SQLite или временный файл).
- Покрывают: связи между импортом и контекстом, между БД и агентом.
- **Эмулируют consistency-разрывы:** specifically — staleness deviation через timestamps в фикстурах.
- **Примеры:**
  - HAE → `daily_metrics` → `_build_gp_context` (UC-A-03 + UC-B-02)
  - checkin → `context_events` → GP daily (UC-B-11, RYW для одного пользователя)
  - safety_net flagged labs → утренний отчёт ⊇ список (UC-D-01)

### Слой 3 — E2E с моками (`e2e_mock`)
- Полный пайплайн от триггера до выхода.
- Моки внешних: Telegram API (telegrabber-style mock), Anthropic (snapshot или scripted), ClinVar/MyVariant (fixture JSON), Oura (fixture), PubMed (fixture).
- **Эмулируют тайминг и tentative-состояния** — не мгновенно, а с задержкой между шагами.
- **Примеры:**
  - PDF → fswatch → import_all → lab_extractor → pending → approve → lab_results → Council → TG (UC-A-01)
  - genome_update_agent с mock ClinVar diff → narrative → TG (UC-C-03)
  - integrity_tests FAIL → triage_agent → автозапуск (UC-D-04)

### Слой 4 — Consistency-specific (отдельный класс)
**Это то, чего обычно нет в test pyramid.** Тесты на распределённые свойства:
- **Read-your-writes:** записал в `lab_results` → следующий вызов `_build_gp_context` видит запись.
- **Tentative → committed:** `pending_labs/` существует, но не в `lab_results`; после approve — оба согласованы.
- **Single-primary:** mock-write с MacBook origin → reject.
- **Staleness:** генерируем `daily_metrics` с `last_updated` 30ч назад → утренний отчёт уходит с warning.
- **Causal:** Round B вызван до Round A → код возвращает ошибку.
- **Eventual sync window:** Reminders sync прошёл — tasks status согласован; до этого может расходиться.

### Слой 5 — Snapshot (`C`)
- Эталонные JSON / структуры для критичных мест.
- Регенерация с manual approve (не auto).
- Хранятся в `tests/snapshots/`.
- **Примеры:**
  - `lab_extractor` JSON для PDF лабораторий A и B
  - Структура GP daily для эталонного дня
  - Council финал для mock-сессии
  - Constitutions заголовки/секции (не содержание)

### Слой 6 — LLM-judge (`D`)
- Отдельный haiku-вызов с явными вопросами на ответ агента.
- Не источник правды — вспомогательная проверка.
- Запускается на ту же выборку, что Слой 5, но проверяет фактологические инварианты.
- **Примеры:**
  - «утверждения координатора подтверждены `<context>`?»
  - «упоминается ли источник старше 6 мес без даты?»

### Слой 7 — Charter (`H`)
- Не код. Документы-маршруты + календарь обязательного запуска.
- Хранятся в `tests/charters/`.
- **Примеры:** `CH-ONCO-01.md`, `CH-DAILY-01.md`, `CH-GENOME-01.md`.

**Test pyramid в числах** (порядок величины, не точные доли):

```
              Charter (H)               5 шт
            Snapshot (C)                10 шт
          LLM-judge (D)                 8 шт
        Consistency-specific            12 шт
      E2E_mock                          15 шт
    Integration                         30 шт
  Unit                                  ~80 шт
```

---

## 3. Структура каталогов

```
health_scripts/
├── tests/
│   ├── conftest.py              # общие fixtures
│   ├── fixtures/
│   │   ├── db.py                # in-memory SQLite + sample data builders
│   │   ├── telegram.py          # mock TG API + chat capture
│   │   ├── anthropic.py         # scripted Anthropic responses
│   │   ├── clinvar.py           # fixture для MyVariant.info
│   │   ├── oura.py
│   │   ├── pubmed.py
│   │   └── time_travel.py       # контроль текущего времени для staleness тестов
│   ├── unit/
│   │   ├── test_uc_i_01_lang.py     # language detector
│   │   ├── test_uc_i_02_sec.py      # OWNER_CHAT_ID
│   │   ├── test_uc_b_04_no_system_work_in_person_list.py
│   │   └── ...
│   ├── integration/
│   │   ├── test_uc_a_03_hae.py
│   │   ├── test_uc_b_02_gp_context.py
│   │   ├── test_uc_b_11_checkin_to_gp.py    # RYW
│   │   └── ...
│   ├── e2e/
│   │   ├── test_uc_a_01_lab_to_council.py
│   │   ├── test_uc_c_03_genome_monthly.py
│   │   ├── test_uc_h_01_council_full.py
│   │   └── ...
│   ├── consistency/                 # ← ОТДЕЛЬНЫЙ слой
│   │   ├── test_ryw_lab_to_morning.py
│   │   ├── test_tentative_pending_labs.py
│   │   ├── test_single_primary_db.py
│   │   ├── test_staleness_oura_26h.py
│   │   ├── test_causal_council_rounds.py
│   │   └── test_eventual_tasks_reminders.py
│   ├── snapshots/
│   │   ├── lab_extractor/
│   │   │   ├── lab_a_2026-04-15.json
│   │   │   └── lab_b_2026-04-20.json
│   │   ├── gp_daily/
│   │   │   └── reference_2026-04-22.md
│   │   └── council/
│   │       └── reference_session.json
│   ├── llm_judge/
│   │   ├── test_council_facts_grounded.py
│   │   └── test_stale_source_dated.py
│   └── charters/
│       ├── CH-ONCO-01.md
│       ├── CH-DAILY-01.md
│       ├── CH-GENOME-01.md
│       ├── CH-MDT-01.md
│       └── CH-CORR-01.md
├── conftest.py                      # pytest root config
└── pyproject.toml / pytest.ini
```

---

## 4. Ключевые fixtures (что строим в первую очередь)

### 4.1 `db` — in-memory SQLite с реальной схемой
- Берёт `CREATE TABLE` из реального `health.db` (через introspection или dump).
- Заполняется через builders: `make_daily_metrics(date, hrv=NULL, sleep_total=7.5, ...)`.
- Каждый тест получает чистую БД (per-test fixture, не per-session).

### 4.2 `time_travel` — контроль `today`/`now`
- `freezegun` или handmade `Clock` объект.
- Все агенты, которые делают `date.today()` или `datetime.now()`, должны принимать `clock` параметр (рефактор).
- Без этого тесты на staleness 26ч/30 дней нереализуемы.
- **Это технический долг текущего кода** — выявится при первой попытке написать UC-D-05 тест.

### 4.3 `telegram_mock`
- Подменяет `bot.send_message`, `send_photo`, `InlineKeyboardMarkup`.
- Capture mode: тест видит, что было отправлено, кому, в каком порядке.
- Inject mode: тест отправляет «update» от имени user или левого chat (для UC-I-02).

### 4.4 `anthropic_mock`
- Два режима:
  - **Scripted** — для unit/integration: возвращает фикс-JSON по prompt-pattern.
  - **Recorded** — для snapshot-тестов: первый запуск пишет, последующие воспроизводят.
- НЕ делает живых вызовов в CI.

### 4.5 `clinvar_fixture`
- JSON-фикстуры для FUNCTIONAL_WHITELIST rsids + 5-10 эталонных Pathogenic.
- Mock с искажённой схемой для UC-K-04.

### 4.6 `oura_fixture`, `hae_fixture`
- Минимальные JSON-семплы за 7д + 90д.
- Edge cases: пустой день, NULL поле, `summary_date` не совпадает с ожидаемым target.

### 4.7 `pending_labs_dir`
- Tmp-папка с подкаталогами по `visit_key`.
- Fixture для tentative-write тестов.

---

## 5. Стратегии для consistency-сценариев

### 5.1 Тест на read-your-writes (RYW)
**Сценарий:** UC-B-11 (checkin → GP daily следующего утра).

```python
def test_checkin_visible_in_gp_next_morning(db, clock):
    # Day N evening
    clock.set("2026-05-08 21:00")
    save_checkin(db, mood=4, energy=3, stress="manageable")

    # Day N+1 morning
    clock.advance(hours=11)  # 08:00 next day
    ctx = build_gp_context(db, target=clock.today())

    assert ctx["recent_checkins"], "checkin from yesterday must be visible"
    assert ctx["recent_checkins"][0]["mood"] == 4
```

### 5.2 Тест на tentative → committed
**Сценарий:** UC-A-01 lab_extractor pipeline.

```python
def test_pending_lab_not_in_lab_results_until_approved(db, pending_labs_dir):
    # Tentative state
    extract_labs_to_pending(pdf="tests/fixtures/sample_lab_a.pdf",
                           output_dir=pending_labs_dir)
    assert (pending_labs_dir / "2026-05-01_lab_a.json").exists()
    assert db.execute("SELECT COUNT(*) FROM lab_results WHERE date='2026-05-01'").fetchone()[0] == 0

    # Commit
    import_from_pending(db, pending_labs_dir / "2026-05-01_lab_a.json")
    assert db.execute("SELECT COUNT(*) FROM lab_results WHERE date='2026-05-01'").fetchone()[0] > 0
```

### 5.3 Тест на single-primary
**Сценарий:** UC-I-07 — попытка записи с MacBook origin → fail.

Вариант 1 (явный): добавить колонку `origin_host` в таблицу `_audit_log`. Тест с mock host = MacBook → write reject.

Вариант 2 (косвенный): тестировать, что путь к БД на MacBook = read-only iCloud копия, а на Studio — operational.

Я склоняюсь к **варианту 1** — явный аудит, который сам по себе становится тестируемым инвариантом.

### 5.4 Тест на staleness
**Сценарий:** UC-D-05 — устаревшие labs → пометка даты.

```python
def test_stale_labs_marked_with_date(db, clock):
    # условный пример: аналит, значение и даты выдуманы
    save_lab_result(db, date="2025-01-01", name="ANALYTE_X", value=1.0)
    clock.set("2025-10-01")  # 9 месяцев спустя

    output = gp_agent.daily_report(db, clock=clock)
    assert "2025-01-01" in output, "must mention source date"
    assert any(marker in output.lower()
               for marker in ["требует обновления", "устарел", "9 месяцев"])
```

### 5.5 Тест на causal-порядок (Round B после Round A)
**Сценарий:** UC-H-01 — попытка запустить Round B до Round A.

```python
def test_round_b_before_round_a_raises(consultation_session):
    # Симулируем нарушение порядка
    with pytest.raises(CausalOrderError):
        consultation_session.run_round_b()  # Round A не вызван
```

### 5.6 Тест на eventual sync window
**Сценарий:** UC-G-02 reminders_sync.

```python
def test_completed_reminder_syncs_within_3h(db, reminders_mock, clock):
    create_task(db, fingerprint="lab:ANALYTE_X")
    create_reminder(reminders_mock, task_id=1)

    # Mark complete in Reminders, но не sync ещё
    reminders_mock.complete(task_id=1)
    assert db.task_status(1) == "open"  # ещё не синкнулось

    # Run sync (имитация cron каждые 3ч)
    reminders_sync.run(db, reminders_mock)
    assert db.task_status(1) == "done"
```

---

## 6. CI/runner стратегия

| Слой | Когда запускается | Время |
|---|---|---|
| Static + Unit | каждый коммит (post-commit hook) | <10с |
| Integration | каждый коммит | <30с |
| E2E_mock | pre-push + nightly | 1-3 мин |
| Consistency | nightly | 30с-1мин |
| Snapshot | при touched-file matches + nightly | разное |
| LLM-judge | nightly + manual on-demand | дорого по API |
| Charter | по календарю (см. ниже) | человеко-часы |

**Расписание charter-ов** (в integrity_tests добавить как `charter_due` warning):
- `CH-ONCO-01` — ежемесячно (1-е число)
- `CH-DAILY-01` — раз в 2 недели
- `CH-GENOME-01` — после каждого genome_update narrative
- `CH-MDT-01` — после крупного изменения promпт-структуры
- `CH-CORR-01` — после каждого `longitudinal_analysis` запуска

Без записи в логе о выполнении charter за окно — `integrity_tests` поднимает WARN.

---

## 7. План работ (фазы)

### Фаза 0 — Инфраструктура (≈3 дня)
**Цель:** базовый pytest setup, fixtures, моки.
**Поставка:**
- `tests/conftest.py` + 7 fixture-модулей из §4
- `pytest.ini` с маркерами (`@pytest.mark.unit`, `@pytest.mark.consistency`, etc.)
- Хук в `run_checks.sh` для запуска unit+integration
- Документ `tests/README.md`

**Зависимости:** рефактор кода с добавлением `clock` параметра туда, где нужен time_travel. Это **отдельная задача**, может делаться параллельно.

### Фаза 1 — Первый пакет 11 UC (≈4 дня)
**Цель:** полный цикл UC-J-02 (план → подтверждение → код) на 11 confirmed UC из §8 USE_CASES.md.

Порядок (от простого к сложному):
1. `UC-I-02` (`UC-SEC-001`) — Telegram fail-closed. Самый простой unit. Образец процесса.
2. `UC-I-01` (`UC-LANG-001`) — language detector. Unit.
3. `UC-I-03` (`UC-NULL-001`) — NULL ≠ 0. Unit + integration.
4. `UC-A-04` (`UC-OURA-001`) — даты сна/активности. Unit + integration.
5. `UC-B-03` — снят 28.09 (тренды — в разбор).
6. `UC-B-04` — работа системы не кладётся в список человека. Unit.
7. `UC-D-04` — triage_agent WARN автофиксы. Integration + e2e_mock.
8. `UC-H-02` — `user_qa.append()` до раундов. Unit (порядок вызовов).
9. `UC-J-01` + `UC-J-02` — proposal + подтверждение. Meta-инфраструктура.
10. `UC-A-03` (`UC-APPLE-001`) — HAE merge. Integration.
11. `UC-K-01` (`UC-UPSTREAM-PROMPT-001`) — после получения upstream URL.

**Поставка:** 11 файлов тестов + 11 markdown-планов (UC-J-02) в `tests/plans/`.

### Фаза 2 — Consistency-specific слой (≈3 дня)
**Цель:** написать §5 сценарии (RYW, tentative, single-primary, staleness, causal, eventual).

**Зависимости:** для single-primary нужно добавить аудит-колонку в БД. Это миграция.

### Фаза 3 — Остальные P0 (~30 UC) (≈1.5 недели)
**Цель:** все P0 confirmed → тесты.

Параллельно: charter-ы пишутся как markdown-документы (это быстро).

### Фаза 4 — Snapshot + LLM-judge (≈4 дня)
**Цель:** установить эталоны, написать judge prompts.

**Главный риск:** snapshot для Council финала. Если структура часто меняется — поддержка дороже пользы. Возможно, ограничимся 1-2 snapshot-ами критичных мест.

### Фаза 5 — Meta + upstream (≈1 неделя)
- `UC-J-03` `uc_index.yaml` валидатор
- `UC-J-04` needs_review механизм
- `UC-K-01` после получения upstream URL
- `UC-X-01..03` реестр зависимостей

### Фаза 6 — Charter calendar + integration в integrity_tests (≈2 дня)
- Добавить `charter_due` WARN в `integrity_tests.py`
- Хук в triage_agent: WARN charter overdue → вопрос в TG.

---

## 8. Открытые архитектурные вопросы

1. **`clock` параметр в агентах** — большой рефактор. Делать сейчас или после первого пакета?
2. **`origin_host` audit column** — миграция БД. Когда?
3. **Snapshot — formaт хранения** — JSON / YAML / Markdown? Я склоняюсь к JSON для машинно-читаемых, MD для текстовых.
4. **LLM-judge стоимость** — сколько API calls в nightly run? Бюджет?
5. **Где живут тест-планы UC-J-02** — в `tests/plans/UC-A-01.md` или в `USE_CASES.md` как отдельная секция?
6. **iCloud propagation в e2e** — реальная iCloud-задержка или моки? (Подсмотрено: тестировать через локальную папку с искусственной задержкой.)

---

## 9. Tradeoffs (по Таненбауму § replication for reliability vs performance vs scalability)

Health OS — это **replication for reliability** (single user, не для масштабируемости). Это значит:
- Тесты на масштабируемость (нагрузочные) **не нужны**.
- Тесты на отказоустойчивость (что происходит при падении Studio? при недоступности iCloud?) — **нужны**, но ограниченно.
- Главный фокус — **корректность медицинских выводов** и **отсутствие потери данных**.

Это влияет на приоритеты:
- High: B (negative), E (cross-check), single-primary, RYW.
- Medium: tentative, staleness, eventual sync.
- Low: высоконагрузочные сценарии, конкурентные конфликты двух пользователей (нет двух пользователей).

---

## 11. Расписание и оркестрация

### 11.1 Ежедневный запуск

`com.larry.health.test-suite` — launchd plist, **00:00 по местному времени Studio**.

Studio в той же часовой зоне, что и дом → `local time = home time`. Если Studio переедет — переписать plist.

Что запускается:

```
00:00  run_full_test_suite.sh
       ├── unit                    (~10с)
       ├── integration             (~30с)
       ├── e2e_mock                (1-3 мин)
       ├── consistency             (30-60с)
       ├── snapshot (touched only) (variable)
       └── llm_judge               (3-5 мин, дорого)
00:10  test_failure_handler.py    ← новый модуль
00:15  morning_test_summary.py    ← пишет резюме в agent_reports
```

**Почему 00:00, а не 03:00:** к 06:45 (`morningwake` caffeinate) и 08:00 (утренний цикл) результаты должны быть готовы и обработаны. 00:00 даёт буфер на повторы и diagnosis.

### 11.2 Различие severity по статусу UC

Падение теста интерпретируется через `status` UC из USE_CASES.md:

| status UC | Тест упал | Серьёзность |
|---|---|---|
| `implemented` | regression | **CRITICAL** — алерт в TG сразу + Reminder |
| `partial` (secured часть) | regression | **CRITICAL** |
| `partial` (gap часть) | expected_gap | INFO — gap-report, без алертов |
| `intended` | expected_gap | INFO — gap-report |
| `speculative` | тест не должен существовать | возможно ошибка в каталоге, WARN |

**Принцип:** `expected_gap` идёт в накопительный gap-report без шума. CRITICAL прерывает сон.

---

## 12. Test failure handler (что происходит при падении)

Новый модуль `test_failure_handler.py`. Вызывается после `run_full_test_suite.sh`.

### 12.1 Уровень A — Детерминированный triage

Без LLM. По патернам в логе:

| Паттерн | Действие | Идемпотентность |
|---|---|---|
| `OperationalError: database is locked` | retry 1× с задержкой 5с | один раз за день |
| `Anthropic API timeout` | пометить тест как `flaky_today`, не алертить | по дню |
| `iCloud-evicted` фикстура | re-fetch fixture, re-run | один раз |
| Snapshot drift с small change в timestamp/uuid | auto-regenerate с warning | требует confirm |

Если уровень A починил → запись в `tests/reports/{date}/auto_fixes.log`, no alarm.

### 12.2 Уровень B — Diagnosis через Claude Haiku

Для каждого оставшегося CRITICAL failure — один вызов Haiku c фиксированным prompt:

```
[system]
Ты — диагност автотестов Health OS. Читай контекст и пиши markdown
с гипотезой причины и 2-3 предложениями что попробовать. Не чини.
Не пиши код. Не используй эмодзи. Без воды.

[user]
Failing test: {uc_id} ({uc_alias})
Status UC: {status}
Confirmation: {confirmation}
Owner модули: {owner}

Logs:
{tail of pytest output}

UC контракт (Given/When/Then/B/E):
{relevant section from USE_CASES.md}

Recent git diff (24h):
{git log + diff for owner modules}

Вопросы:
1. Какая наиболее вероятная причина (1-2 предложения)?
2. Что проверить в первую очередь?
3. Это похоже на регрессию свежего коммита или на старый баг?
```

Ответ сохраняется в `tests/reports/{date}/{uc_id}_diagnosis.md`.

### 12.3 Уровень C — Repair (решает свод, не эта страница)

Норма об авто-применении красного живёт в `CLAUDE.md §13` (в закрытой части) — там же
конверт авто-ремонта и поправка владельца 2026-08-03, сузившая запрет до трёх
одновременных условий (обратимость, независимый ревьюер, краснеющий вниз по
потоку оракул). Здесь — **указатель, а не пересказ**.

Почему указатель: с 2026-08-03 по 2026-09-14 эта страница держала прежний текст
«не реализуем», уже неверный, и `test_failure_handler.py` в своём docstring
ссылался именно сюда. Пересказ нормы стареет отдельно от нормы — §18; второй
экземпляр вердикта не добавляет знания, он добавляет способ разойтись.

Практически на сегодня: уровень C в `test_failure_handler.py` не реализован,
механизма авто-применения (`fix_applier`) в проекте нет. Статус механизма — в
`subsystem_intent.yaml` (`auto_fix_applier_absent`), чтобы и он не завёл здесь
второй дом.

---

## 13. Вывод результатов

### 13.1 Файлы

```
tests/reports/{YYYY-MM-DD}/
├── summary.json              # машинное резюме
├── junit.xml                 # стандартный pytest формат
├── auto_fixes.log            # что починил уровень A
├── gap_report.md             # expected_gap-ы (накопительно)
├── {UC-A-01}_diagnosis.md    # diagnosis от Haiku, по одному на CRITICAL
├── {UC-B-02}_diagnosis.md
└── ...
```

`tests/gap_report.md` — **актуальный snapshot** всех expected_gap-ов. Обновляется каждое утро. Не растёт исторически — отражает текущее состояние «где код не догнал намерение».

### 13.2 Утренний отчёт (08:00)

В `morning_report.py` добавляется секция:

```
## Тесты

123 PASS · 4 expected_gap · 0 regression · 1 flaky_today · 2.4с
```

Если есть `regression` — секция в верху, не в конце.

### 13.3 Telegram

**Только CRITICAL** (regression). Формат:

```
⚠️ Regression: UC-B-02 GP daily контекст
Status: implemented → упал
Diagnosis: tests/reports/2026-05-09/UC-B-02_diagnosis.md
Reminder поставлен на 10:00.
```

**НЕ алертит** для expected_gap, flaky_today, infrastructure issues, charter overdue (они идут в morning report как INFO).

### 13.4 Reminder на 10:00 — готовый промпт для копипаста

Для каждого CRITICAL failure — отдельный Reminder в списке «Health» с `due=today 10:00`:

**Title:** `Test failure: UC-B-02 (GP daily контекст)`

**Body:**

```
Скопировать в Claude:
---
Health OS: упал тест UC-B-02 (GP daily контекст 7д + 4 горизонта).
Status UC: implemented · это регрессия, не expected_gap.

Контекст:
- diagnosis: ~/health_scripts/tests/reports/2026-05-09/UC-B-02_diagnosis.md
- контракт UC: ~/health_scripts/USE_CASES.md §UC-B-02
- лог теста: ~/health_scripts/tests/reports/2026-05-09/junit.xml
- recent diff: git log --since=2 days ago -- gp_agent.py health_db.py

Задача: прочитай diagnosis, оцени гипотезу, реши что делать.
Помни: не ослаблять ассерт ради зелёного теста.
```

**Fingerprint:** `test_fail:UC-B-02` (UC-G-01 dedup — повторное падение того же UC за день не плодит Reminders).

### 13.5 Расписание charter-ов

В `integrity_tests.py` добавляется проверка `charter_due`:

```python
def check_charter_overdue():
    for charter, max_age in {
        "CH-ONCO-01": 31,
        "CH-DAILY-01": 14,
        "CH-GENOME-01": 60,    # после каждого genome_update
        "CH-MDT-01": 90,
        "CH-CORR-01": 30,
    }.items():
        last = read_charter_log(charter)
        if last and (today - last).days > max_age:
            warn(f"Charter {charter} overdue", f"last: {last}")
```

WARN → triage_agent ставит вопрос в TG: «Запустить CH-ONCO-01 ревью?» с ссылкой на маршрут чартера (сам чартер — в закрытой части проекта: он написан по клиническому профилю владельца).

---

## 14. Что меняется в плане работ (§7) с учётом §11–13

Добавляется в **Фазу 0:**
- launchd plist `com.larry.health.test-suite`
- `test_failure_handler.py` (уровень A + B)
- `morning_test_summary.py`
- Расширение `morning_report.py` (секция «Тесты»)

Это +2 дня к Фазе 0. Итого Фаза 0 = ≈5 дней.

---

## 15. Открытые вопросы (дополнительно к §8)

1. **Бюджет Haiku-вызовов в diagnosis.** При 5 CRITICAL за ночь × ~5K токенов = ~$0.05. Приемлемо. Но если повторяющиеся падения — кэшировать diagnosis по test+commit hash.
2. **TZ при путешествиях.** launchd plist с фиксированным `Hour=0` следует local time машины. Если Studio в одном поясе, а ты в другом — это нормально (тесты идут на Studio). Если Studio переедет — переписать.
3. **Что делать с charter overdue, если ты в путешествии.** WARN не блокирует, но напоминание накапливается. Возможно, нужна команда «отложить charter на N дней».

---

## 15б. Что сознательно НЕ делается (решения владельца)

Переехало из BLUEPRINT (раздел «Стратегия тестирования») 2026-08-02: там это стояло в файле-объяснении и читалось как отчёт о состоянии, а не как принятое решение. Каждый пункт — закрытый выбор с названной причиной, а не долг.

- **Полноценной LLM-judge eval pipeline нет** (W3B отложено). Вместо неё — self-consistency double-run для критичных UC. Причина владельца: «я замечу по отчётам».
- **Авто-фикса по найденному падению нет — только Diagnose-don't-Repair** (reminder + готовый промпт). Норма живёт в `CLAUDE.md §13` (в закрытой части), ступень 1: упавший тест не даёт тотального машинно-проверяемого safe-предиката, поэтому конверт авто-ремонта на нём не смыкается. Прецедент: `triage_agent` спавнил агента `subprocess.Popen` внутри короткоживущего `morning_report` → launchd реапил группу → SIGKILL до записи; спавн убран 2026-06-29. **С 2026-08-03 запрет сужен, а не снят** (поправка владельца там же, в `CLAUDE.md §13` (в закрытой части)): авто-применение разрешено при обратимости + независимом ревьюере + краснеющем вниз по потоку оракуле. Механизма, который бы этим правом пользовался, в проекте нет — «нет авто-фикса» сегодня верно по факту, а не по запрету.
- **Charter-overdue механики нет.** Причина владельца: «я сам увижу». (Ср. §13.5 выше, где она описана как проект — это план, а не живой механизм.)
- **PubMed schema-drift watcher и Anthropic-deprecation watcher не построены** (W2B-2/3 в backlog).

## 16. История изменений

| Дата | Версия | Изменения |
|------|--------|-----------|
| 2026-05-08 | 0.1 | Первый черновик: 7-слойная пирамида + consistency-specific слой; план в 6 фаз; фикстуры; интеграция с TC и USE_CASES.md |
| 2026-05-08 | 0.2 | Добавлено §11 расписание (00:00 местного времени); §12 test_failure_handler 3 уровня (Triage/Diagnosis/Repair-rejected); §13 формат вывода (TG для regression, Reminder 10:00 с готовым промптом); §14 правка плана (+2д к Фазе 0) |
