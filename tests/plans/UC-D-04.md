# Тест-план UC-D-04 — `triage_agent` чинит WARN, идемпотентен по дню

**Источник:** USE_CASES.md §4.D → UC-D-04
**Status:** `implemented` · **Confirmation:** `confirmed`
**Type:** `e2e_mock` (integration с моками subprocess) · **Oracle:** `B + E`
**Owner:** `triage_agent.py:48 run_triage`

---

## Что проверяем

`run_triage(send_user_questions=True)`:
1. Читает `logs/integrity_latest.json` (вывод integrity_tests).
2. Авто-фиксит:
   - WARN «GP-отчёт отсутствует» → запуск `gp_agent.py weekly` через `Popen`;
   - WARN «genome_update никогда не запускался» → запуск `genome_update_agent.py`.
3. Спрашивает пользователя через TG:
   - «анализы старше 180 дней» → формулирует вопрос;
   - «истёкший клинический период» → вопрос.
4. Идемпотентен по дню через `logs/triage_done_{date}.flag`.
5. НЕ лечит FAIL (только WARN) — FAIL блокирует утренний отчёт через
   `run_checks.sh --scheduled`.

---

## Стратегия теста

**Уровень:** `integration` через mock'и:
- `subprocess.Popen` через monkeypatch — capture launches;
- `triage_agent._send_telegram` через monkeypatch — capture messages;
- tmp-каталог для `logs/integrity_latest.json` и flag-файла.

**Оракулы:**
- **B (negative):**
  - FAIL в integrity_tests → triage НЕ запускает Popen (только WARN);
  - повторный вызов в тот же день → no-op (flag);
  - integrity_latest.json не существует → graceful return.
- **E (cross-check):**
  - WARN «GP-отчёт» → ровно 1 Popen-вызов на gp_agent.py weekly;
  - WARN про анализы → 1 TG-message с упоминанием «Анализы».

---

## Структура теста

```python
def test_no_integrity_log_no_action(triage_env):
def test_warn_gp_report_triggers_popen(triage_env, monkeypatch):
def test_warn_genome_update_delivered_not_autofixed(triage_env, monkeypatch):  # 2026-06-29: было triggers_popen; теперь Diagnose-don't-Repair
def test_warn_old_labs_asks_user(triage_env, monkeypatch):
def test_warn_expired_period_asks_user(triage_env, monkeypatch):
def test_idempotent_per_day(triage_env, monkeypatch):
def test_fail_does_not_trigger_autofix(triage_env, monkeypatch):
```

---

## Acceptance

7 integration-тестов. Все зелёные при mock-окружении.

## Risk если красный

`implemented` → REGRESSION. Если автофиксы перестанут срабатывать —
сломанные пайплайны не будут чиниться к 08:00.
