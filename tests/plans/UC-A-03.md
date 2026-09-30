# Тест-план UC-A-03 — HAE merge без затирания Oura

**Источник:** USE_CASES.md §4.A → UC-A-03.
**Status:** `partial` · **Confirmation:** `confirmed`
**Type:** `integration` · **Oracle:** `B + E`
**Owner:** `import_apple_health.py:230 save_daily_summaries`

---

## Что проверяем

`save_daily_summaries(daily, mode="merge")` использует `existing.update(summary)`
для слияния новых данных HAE с уже существующим JSON-файлом за тот же день.

**Подтверждённый риск (валидация 2026-05-08):**
shallow `.update()` перезаписывает Oura-поля верхнего уровня, если HAE прислал
для них `null` или новое значение.

Сценарий из USE_CASES.md:
1. На YYYY-MM-DD JSON содержит `{hrv: 25, sleep_total: 7.5}` (от Oura).
2. HAE прислал `{steps: 8000}` без `hrv` (HAE не записывает HRV — это норм).
3. После merge должно быть `{hrv: 25, sleep_total: 7.5, steps: 8000}`.

Тест проверяет именно этот инвариант: **HAE не должен затирать Oura-поля,
если HAE их не прислал.**

---

## Стратегия теста

**Уровень:** `integration` через fixture `hae_mock` (создаёт HAE JSON в tmp)
+ tmp `HEALTH_DATA` директория для existing JSON.

**Оракулы:**
- **B (negative):** existing-JSON содержит hrv=25; HAE без hrv → final hrv=25.
- **B:** existing JSON содержит sleep_total=7.5; HAE без sleep_* → sleep_total=7.5.
- **E (cross-check):** HAE прислал steps=8000 → final steps=8000 (новое поле добавлено).

---

## Структура теста

```python
def test_hae_merge_preserves_oura_hrv():
    """Если existing has hrv=25, HAE без hrv → hrv остаётся 25.
    XFAIL пока баг не починен (status=partial)."""

def test_hae_merge_adds_new_fields():
    """HAE прислал steps → они в результате."""

def test_hae_overwrite_with_explicit_value():
    """Если HAE прислал hrv=30 явно → перезапись OK (новое значение)."""
```

---

## Acceptance

3 integration-теста. Главный (preserves Oura) — xfail сейчас.
Когда `import_apple_health.py:248` починят (filter `not None` перед update) — xpass.

## Risk если красный (после фикса)

`partial` → `implemented`. REGRESSION = HAE снова затирает Oura.
