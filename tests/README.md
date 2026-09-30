[English](README.en.md) · **Русский**

# tests/ — Health OS Test Suite

Архитектура и стратегия: см. [TEST_ARCHITECTURE.md](../TEST_ARCHITECTURE.md).
Прагматики и контракты: см. [USE_CASES.md](../USE_CASES.md).
План работ и статусы: ведутся в `ROADMAP.md` (в закрытой части проекта).

---

## Быстрый старт

```bash
cd ~/health_scripts

# Все unit-тесты (быстро, <1с)
python3.11 -m pytest tests/unit/

# Все integration (требует in-memory SQLite fixture)
python3.11 -m pytest tests/integration/

# Полный прогон (без slow и без real Anthropic)
python3.11 -m pytest tests/

# Только consistency-тесты (RYW / staleness / tentative / ...)
python3.11 -m pytest -m consistency

# Только конкретный UC
python3.11 -m pytest tests/unit/test_uc_i_02_sec.py

# Включая медленные
python3.11 -m pytest -m "not requires_anthropic_key"

# С реальным Anthropic API (платно — будет тратить токены)
python3.11 -m pytest -m "requires_anthropic_key" --override-ini="addopts="
```

---

## Структура

```
tests/
├── conftest.py              ← root: sys.path setup + autoreset _time_inject
├── pytest.ini               (в корне health_scripts/) — маркеры, addopts
├── unit/                    Слой 1: изолированные модули (<100мс)
├── integration/             Слой 2: связка модулей через in-memory SQLite
├── e2e_mock/                Слой 3: сквозные сценарии с моками внешних
├── consistency/             Слой 4: RYW / staleness / tentative / single-primary / causal / eventual
├── snapshots/               Слой 5: эталоны JSON / структуры (не код тестов)
├── llm_judge/               Слой 6: D-уровень через haiku
├── charters/                Слой 7: маршруты ревью человеком (markdown, не код)
├── fixtures/                Общие fixture-модули (db, time_travel, telegram, anthropic, ...)
├── plans/                   Тест-планы UC-J-02 (markdown перед написанием теста)
└── reports/                 Артефакты ночного прогона (gitignored)
```

---

## Маркеры

| Маркер | Использование |
|---|---|
| `unit` | <100мс, изолированный модуль, mock зависимостей |
| `integration` | связка модулей, in-memory SQLite |
| `e2e_mock` | сквозной сценарий с моками внешних API |
| `consistency` | распределённые свойства (RYW / tentative / etc.) |
| `snapshot` | сравнение с эталоном |
| `llm_judge` | D-уровень оракул через haiku |
| `slow` | >5с — пропускается в pre-commit, идёт в nightly |
| `requires_studio` | работает только на основной машине (infra_config.is_primary) — на MacBook skipped |
| `requires_anthropic_key` | использует реальный Anthropic API — skipped без ключа и в pre-commit |

Применение: декоратор на тесте или модулe.

```python
import pytest
pytestmark = pytest.mark.unit   # на весь модуль

@pytest.mark.consistency
def test_ryw_checkin_to_gp(...):
    ...
```

---

## Как добавить новый тест для UC

1. **Убедиться что UC `confirmation=confirmed`** в `USE_CASES.md`.
   Если ещё `proposed` — тест не пишется (правило UC-J-02).

2. **Сначала тест-план** в `tests/plans/UC-X-NN.md`:
   - что именно проверяем (Then из UC, B/E/C/D);
   - какие моки нужны;
   - какой уровень (unit/integration/e2e/consistency);
   - blast radius (что сломается если тест красный).

3. **Confirm плана** — владелец читает, говорит «да» или правит.

4. **Только потом — код теста** в соответствующем подкаталоге:
   - имя файла: `test_uc_i_02_sec.py` (slug from UC ID).
   - первая строка: `pytestmark = pytest.mark.<уровень>`.
   - docstring: ссылка на UC ID и краткая прагматика.

5. **Запустить** локально на MacBook → rsync на Studio → `pytest` на Studio.

6. **Обновить `uc_index.yaml`** (когда T-5.1 готов): добавить путь к тесту в запись UC.

---

## Использование fixture `_time_inject`

В `conftest.py` есть autouse-фикстура, которая после каждого теста вызывает
`clear_test_clock()`. Это значит: время, замороженное в одном тесте, не утекает
в следующий.

```python
from datetime import date
from _time_inject import set_test_clock, get_today

def test_stale_labs_warning():
    set_test_clock("2026-05-08")
    # ... код, который зовёт get_today() / get_now() — увидит 2026-05-08
    assert get_today() == date(2026, 5, 8)
    # после теста время автоматически разморожено
```

Заметка: время заморожено только для кода, который **импортирует и использует**
`_time_inject.get_now()` / `get_today()`. Прямой `datetime.now()` / `date.today()`
не подменяется. Tier-1 файлы Health OS (gp_agent, morning_report, integrity_tests,
…) уже переведены на инъекцию (см. ROADMAP T-pre.1).

---

## Single-primary guard в тестах

В `conftest.py` стоит `os.environ.setdefault("ALLOW_WRITE_NONPRIMARY", "1")`.
Это позволяет тестам на MacBook писать в свою (in-memory или tmp) БД, не
задеваая prod-guard (`UC-I-07`).

На Studio переменная не нужна — там и так primary.

---

## Расписание

- **Локально (pre-commit)**: `pytest -m "not slow"` — должно быть зелёным до коммита.
- **Studio post-commit**: после rsync — `bash run_checks.sh`, который запускает
  smoke_tests + integrity_tests.
- **Studio nightly 00:00**: `com.larry.health.test-suite` — полный pyramid через
  `run_full_test_suite.sh`. См. TEST_ARCHITECTURE.md §11.

---

## История

| Дата | Что |
|---|---|
| 2026-05-08 | T-0.1 + T-0.2: pytest setup, structure, conftest, root smoke + _time_inject unit (17 тестов, 36/36 smoke_tests на Studio) |
