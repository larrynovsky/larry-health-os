# Тест-план UC-A-04 — Oura: дата сна по пробуждению

**Источник:** USE_CASES.md §4.A → UC-A-04 (alias `UC-OURA-001`).
**Status:** `implemented` · **Confirmation:** `confirmed`
**Type:** `check` (unit) · **Oracle:** `B + E`
**Owner:** `import_oura.py` (`parse_sleep`, `parse_activity`, `parse_readiness`).

---

## Что проверяем

В Oura API v2 `summary_date` (поле `day` в sessions) — это **дата пробуждения**.
Сон, начавшийся 7 мая в 23:00 и завершившийся 8 мая в 7:00, имеет `day = "2026-05-08"`.

`parse_sleep(sessions, scores)`:
- использует `s["day"]` как ключ результата;
- считает `totalSleep = total_sleep_duration / 3600` (секунды → часы);
- фильтрует только `type in ("long_sleep", "sleep")`;
- пропускает сессии короче 1 часа (обрывки);
- если за один `day` несколько сессий — берёт самую длинную.

UC-A-04 = «target=today для сна, target=yesterday для активности» — это
семантика на уровне утреннего отчёта (где «сегодня» = день пробуждения).

---

## Стратегия теста

**Уровень:** `unit` через прямой вызов `parse_sleep` / `parse_activity` /
`parse_readiness` с фиктивными `sessions`/`data`-массивами.

**Оракулы:**
- **B (negative):** обрывки сна <1ч пропускаются; non-sleep типы (`nap`,
  `deep_nap`) не попадают.
- **E (cross-check):** для конкретного `day=2026-05-08` и `total_sleep_duration=27000с`
  → `result["2026-05-08"]["totalSleep"] == 7.5`.

---

## Структура теста

```python
def test_parse_sleep_uses_day_as_key():
def test_parse_sleep_converts_seconds_to_hours():
def test_parse_sleep_filters_short_sessions():
def test_parse_sleep_ignores_naps():
def test_parse_sleep_picks_longest_session_per_day():
def test_parse_sleep_includes_score():
def test_parse_activity_uses_day_as_key():
def test_parse_readiness_uses_day_as_key():
def test_oura_dates_in_cli_block_only():  # AST: date.today() только в __main__
```

---

## Acceptance

9 unit-тестов зелёные. Проверяет инвариант: target правильный.

## Risk если красный

`implemented` → REGRESSION. Если parse_sleep начнёт класть сегодняшний сон
на вчерашнюю дату — все тренды сместятся, утренний отчёт будет «вчерашний».
