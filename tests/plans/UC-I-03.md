# Тест-план UC-I-03 — NULL ≠ 0 в нарративе агентов

**Источник:** USE_CASES.md §4.I → UC-I-03 (alias `UC-NULL-001`).
**Status:** `partial` · **Confirmation:** `confirmed`
**Type:** `integration` · **Oracle:** `B + E`
**Owner:** все агенты, формирующие текст; в первую очередь `gp_agent._build_gp_context`.

---

## Что проверяем

NULL в БД (нет данных) ≠ реальный 0. Семантически разные смыслы:
- `hrv = NULL` → «нет данных» (сенсор не записал, либо мы ещё не импортировали);
- `hrv = 0` → буквально 0 (физически невозможно, но допустим как mock).

В нарративе НЕ должно быть фраз «спал 0 часов», «HRV 0», «Deep: 0 мин» —
если источник пуст. Иначе агент создаёт ложные медицинские утверждения.

**Подтверждённый баг (валидация 2026-05-08):**
- `gp_agent.py:478` — `int((stats.get('avg_deep') or 0)*60)` без guard.
  При NULL avg_deep за 7д контекст содержит «Deep: 0 / 50 / 60 / 70 мин».
- `gp_agent.py:389` — аналогично для дневного `s.get('deep') or 0`.

Тест должен ловить эти баги (REGRESSION-сигнал когда починим, и
expected_gap пока баг есть — UC-I-03 = `partial`).

---

## Стратегия теста

**Уровень:** `integration` через fixture `db` (in-tmp-file SQLite).

**Сценарий:**
1. Чистая БД, никаких daily_metrics за последние 30д.
2. Заполняем `lab_results` минимально (чтобы build_context не упал на других секциях).
3. Вызываем `_build_gp_context(yesterday)`.
4. Парсим текст: ищем паттерны «0 мин», «0 ч», «0 часов», «HRV 0», «Deep: 0».
5. Если хоть один найден → fail (или `xfail` пока баг не починен).

**Оракулы:**
- **B (negative):** в тексте нет «Deep: 0 мин» при NULL avg_deep.
- **E (cross-check):** при заполненных данных — есть число (т.е. инвариант не блокирует все нули).

---

## Структура теста

```python
def test_no_zero_minutes_in_text_when_data_missing(db, clock):

def test_no_zero_hours_when_sleep_data_missing(db, clock):

def test_no_zero_hrv_when_no_oura_data(db, clock):

def test_real_zero_in_filled_data_is_okay(db, clock):
    """Если данные есть и реально 0 — не блокируем. (mock-сценарий)"""

def test_known_bug_documents_in_partial_status(db, clock):
    """Документация: текущий gp_agent имеет баг. Тест помечен xfail."""
```

---

## Acceptance

5 integration-тестов. **Часть может быть xfail сейчас** — известный partial.
Когда `_build_gp_context` починят (добавят guard) — снять xfail и тесты должны
позеленеть.

## Risk если красный

`partial` → expected_gap (xfail). Когда станет `implemented` после фикса —
любой регресс нуля в нарративе будет CRITICAL.
