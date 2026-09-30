[English](handle_test_failure.en.md) · **Русский**

# Что делать, когда ночной test suite упал

> **Тип документа:** How-to (Diataxis).
> Контекст: `docs/explanation/test_architecture.md`.
> Реализация: `test_failure_handler.py` + `triage_agent.py`.

---

## Утренний поток (нормальное поведение системы)

### 00:00 по местному времени — `com.larry.health.test-suite`

`run_full_test_suite.sh` запускает unit → integration → consistency →
e2e_mock → snapshot → llm_judge. Артефакты в `tests/reports/{date}/`:
junit.xml для каждого слоя + `summary.json`.

### 00:10 — `test_failure_handler.py`

Парсит junit.xml, классифицирует failures по уровням:

- **Уровень A — детерминированный triage.** Паттерны:
  - `database is locked` → retry один раз;
  - `Anthropic timeout` → пометить `flaky_today`;
  - `iCloud-evicted` → `flaky_today`.
- **Уровень B — Haiku diagnosis.** Для оставшихся CRITICAL — один Haiku
  вызов с фиксированным промптом → markdown в
  `tests/reports/{date}/{UC}_diagnosis.md`. Кэш по `test_id+commit_hash`.
- **Уровень C — Repair.** Не реализован. Норма — `CLAUDE.md §13` (в закрытой части):
  с поправкой владельца 2026-08-03 авто-применение красного **сужено, а не
  запрещено** (обратимость + независимый ревьюер + краснеющий вниз по потоку
  оракул). Механизма, пользующегося этим правом, в проекте нет.

### 00:15 — `morning_test_summary.py`

Сводит результаты в `agent_reports type='test_summary'`. `morning_report.py`
в 08:30 читает запись и добавляет секцию «Тесты» в утренний отчёт.

### 08:00–08:30 — утренний цикл видит результат

В Telegram приходит:

- **0 regression** → одна строка в утреннем отчёте: `Тесты: 301 pass`.
- **N regression > 0** → отдельный TG-алерт ещё до основного отчёта:
  ```
  ⚠️ Test regression: 1 fail(s)
  • unit/test_triage_delivery.py::test_person_questions...
  Diagnosis: tests/reports/2026-05-09/
  ```

### 10:00 — Reminder

Для каждого CRITICAL — отдельный Reminder в списке «Health» с готовым
промптом для копипаста. Открываешь Reminders → копируешь body → вставляешь
в Claude → разбираешься.

---

## Что делать руками: пошаговый план для regression

### Шаг 1: Прочитать diagnosis

```bash
ssh <studio_ssh> "ls ~/health_scripts/tests/reports/$(date +%Y-%m-%d)/"
ssh <studio_ssh> "cat ~/health_scripts/tests/reports/$(date +%Y-%m-%d)/{UC_id}_diagnosis.md"
```

Diagnosis — это гипотеза причины от Haiku. Не финальный ответ, а стартовая
точка.

### Шаг 2: Открыть Reminder

В macOS Reminders → список «Health». Тема: `Test failure: UC-X-NN`.
В body — готовый промпт. Скопировать.

### Шаг 3: Запустить Claude с этим промптом

```
[вставить body Reminder в Claude]
```

Claude прочитает diagnosis, контракт UC, recent diff и скажет что попробовать.

### Шаг 4: Воспроизвести локально

```bash
cd ~/health_scripts
python3.11 -m pytest tests/{layer}/test_uc_x_nn_*.py -v
```

Если воспроизводится — починить код **(не тест!)**. Запустить ещё раз.

### Шаг 5: Если xfail-кандидат — обоснованно сделать xfail

Если регрессия принята как «временно» (например, partial UC):

```python
@pytest.mark.xfail(reason="...", strict=True)
def test_x():
    ...
```

`strict=True` — если тест внезапно станет зелёным, pytest пожалуется.

---

## Сценарий: тест из expected_gap внезапно зелёный

Тест на `intended` UC проходит — это сигнал что код «догнал» намерение.

1. Прочитать `tests/reports/{date}/junit.xml` — найти `XPASS`.
2. Открыть UC в `USE_CASES.md` — поменять `status: intended → partial` или
   `partial → implemented`.
3. Снять `@pytest.mark.xfail` декоратор.
4. Прогнать → должен быть зелёный.
5. Обновить `uc_index.yaml`.

---

## Сценарий: handler сам не запустился

Симптомы: утром в TG **нет** ни алерта, ни секции «Тесты» в отчёте.

```bash
# Проверить запускался ли suite
ssh <studio_ssh> "ls -la ~/health_test_suite.log"
ssh <studio_ssh> "tail -30 ~/health_test_suite.log"

# Проверить launchd-агент
ssh <studio_ssh> "launchctl list | grep test-suite"
```

Если статус `launchctl` не показывает `com.larry.health.test-suite`:

```bash
ssh <studio_ssh> "launchctl unload ~/Library/LaunchAgents/com.larry.health.test-suite.plist; launchctl load ~/Library/LaunchAgents/com.larry.health.test-suite.plist"
```

Если suite зависает:

```bash
ssh <studio_ssh> "ps aux | grep run_full_test_suite"
ssh <studio_ssh> "kill -TERM <pid>"
```

`ExitTimeOut=21600` (6ч) защищает от подвисания дольше — после этого launchd
сам убьёт.

---

## Сценарий: бюджет API Haiku превышен

Месячный отчёт `monthly_api_report.py` (1-го числа 09:30) показывает
расходы за прошлый месяц. Если больше ожидаемого:

1. Проверить cache hit ratio — если низкий, кэш не работает.
2. Проверить топ-5 «дорогих» тестов — может, один тест каждую ночь падает
   с разным `commit_hash` и кэш не помогает.
3. Принять решение: пометить тест как `flaky_today` (Уровень A) или
   починить.

Жёсткий лимит сейчас не стоит (Q5 = C: «живём первый месяц с кэшированием,
потом решаем»). Если решишь — добавить в `test_failure_handler.py` проверку
суммы за день.

---

## Связанные документы

- `docs/explanation/test_architecture.md` — почему такая модель.
- `docs/how-to/run_tests.md` — как запускать.
- `TEST_ARCHITECTURE.md` §11–13 — оркестрация в деталях.
- `test_failure_handler.py` — реализация.
- `monthly_api_report.py` — отчёт по тратам.
