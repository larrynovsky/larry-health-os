# Тест-план UC-H-02 — `user_qa.append()` строго до `_build_data_package()` и до раундов

**Источник:** USE_CASES.md §4.H → UC-H-02
**Status:** `implemented` · **Confirmation:** `confirmed`
**Type:** `check` (unit) · **Oracle:** `B + E`
**Owner:** `wellally_consult.py:464 run_consultation_cycle_async`

---

## Что проверяем

В Council v2 цикле каждый поворот диалога:
1. Принимает `user_message` от пациента;
2. Если есть предыдущие раунды (`session.rounds` непуст) — добавляет
   `(last_question, user_message)` в `session.user_qa` ПЕРЕД пересборкой
   data_package и ПЕРЕД любым `asyncio.gather`-вызовом раундов;
3. Иначе пересборка не нужна (это первый поворот).

Главный инвариант (из памяти 2026): `user_qa.append()` строго ДО
`_build_data_package()` и ДО `_run_deliberation_round`. Иначе специалисты
теряют контекст диалога — они работают на устаревшем data_package.

---

## Стратегия теста

**Уровень:** `unit` через AST-инспекцию исходника `wellally_consult.py`.

**Почему AST а не runtime:** запустить реальный `run_consultation_cycle_async`
требует AsyncAnthropic клиент + специалист-промпты + БД. Это уже e2e/integration.
Для проверки **порядка вызовов** в коде достаточно AST: мы просто читаем
тело функции и видим последовательность statement-ов.

**Оракулы:**
- **B (negative):** в коде функции нет варианта, при котором `_run_deliberation_round`
  вызывается до `user_qa.append` (для not-empty session.rounds).
- **E (cross-check):** позиции вызовов в AST упорядочены: `append` < `_build_data_package` < `_run_deliberation_round`.

---

## Структура теста

```python
def test_run_consultation_cycle_has_user_qa_append_before_rounds():
    """append → build → gather/round."""

def test_user_qa_append_inside_session_rounds_branch():
    """append вызывается только если session.rounds непуст."""

def test_round_b_uses_round_a_results():
    """_run_deliberation_round для B вызывается ПОСЛЕ A с round_a_opinions."""
```

---

## Acceptance

- 3 unit-теста зелёные.
- Любая инверсия порядка `append → _build_data_package → раунды` в коде →
  один из тестов краснеет.

## Risk если красный

`implemented` → REGRESSION → CRITICAL.
Спецы потеряют контекст диалога → медицинские выводы на устаревших данных.
