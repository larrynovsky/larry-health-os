[English](add_domain.en.md) · **Русский**

# Как добавить новый домен рекомендаций

> **Тип документа:** How-to (Diataxis) — конкретные шаги для реальной задачи.
> Архитектура системы рекомендаций: [recommendation_engine.md](../../recommendation_engine.md).
> Справочник DOMAIN_SIGNALS + пороги: [ARCH_SNAPSHOT.md §АЛЕРТ-ЛОГИКА](../../ARCH_SNAPSHOT.md).

---

## Предусловие

Перед добавлением домена нужны статистически обоснованные корреляции.
Требования: |r| ≥ 0.15 по Spearman, n ≥ 50 пар.

---

## Шаг 1 — Найти корреляции

Запустить на Studio:

```bash
ssh <studio_ssh>
cd ~/health_scripts
HEALTH_DATA_DIR=<каталог тенанта> /opt/homebrew/bin/python3.11 longitudinal_analysis.py
```

Или использовать существующий шаблон `/tmp/stress_corr.py` (есть на Studio).
Выбрать сигналы с |r| ≥ 0.15 и n ≥ 50. Отсечь шум.

**Что проверить:**
- Направление связи: больше метрики = лучше или хуже?
- Если "больше = хуже" → метрика пойдёт в INVERTED (см. шаг 3)
- Лаг: сигнал влияет на исход сегодня или завтра?

---

## Шаг 2 — Создать протокол в БД

```bash
ssh <studio_ssh>
cd ~/health_scripts
/opt/homebrew/bin/python3.11 -c "
import health_db
db = health_db.HealthDB()
db.save_protocol(
    title='Название протокола',
    domain='domain_name',
    behavior='Описание ключевого поведения',
    status='active'
)
"
```

Запомнить `id` нового протокола (нужен для ограничений).

---

## Шаг 3 — Добавить в DOMAIN_SIGNALS

На Studio записать сигналы в `system_config` под ключом `domain_signals.domain_name` через `health_db.upsert_config` (значения — из шага 1):

```python
health_db.upsert_config("domain_signals.domain_name", value_json=[
    {"metric": "metric_name", "label": "исход на русском", "r": r_value, "threshold_pct": threshold_pct},
    # threshold_pct: перцентиль срабатывания (обычно 25–30)
])
```

Если метрика "больше = хуже" — на MacBook добавить её в множество `INVERTED` внутри `get_metric_percentiles()` в `metrics_db.py`:

```python
INVERTED.add("metric_name")  # высокое = плохо; существующие метрики сохранить
```

---

## Шаг 4 — Добавить _signal_reason

На MacBook в `services/recommendations.py` найти функцию `_signal_reason()` и добавить ветку:

```python
elif metric == "metric_name":
    return f"[объяснение на русском: что значит это отклонение]"
```

---

## Шаг 5 — Обновить ARCH_SNAPSHOT

В файле `ARCH_SNAPSHOT.md` в разделе `## АЛЕРТ-ЛОГИКА` добавить новый домен в таблицу DOMAIN_SIGNALS и в таблицу активных протоколов.

---

## Шаг 6 — Деплой на Studio

Правки кода и документации коммитить на MacBook. Работа длиннее одного коммита — в дереве, созданном `scripts/thread_start.sh <slug>`: [порядок работы](thread_worktree.md).

```bash
# После коммитов в дереве нити — закрыть её из главной копии MacBook:
cd ~/health_scripts
scripts/thread_finish.sh <slug>
```

Закрытие само гонит полный прогон на Studio, сливает ветку и вызывает post-commit для `git push` и рестарта. При разовой правке прямо в `main` post-commit запускается самим коммитом.

---

## Шаг 7 — Проверить

```bash
# Сначала убедиться, что проверяемый коммит присутствует на Studio (§12):
git fetch studio
git log studio/main
ssh <studio_ssh> "cd ~/health_scripts && /opt/homebrew/bin/python3.11 integrity_tests.py 2>&1 | tail -10"
```

Послать тестовый `/report` в Telegram и убедиться, что новый домен срабатывает.

---

## Добавить ограничение (опционально)

Если домен нельзя использовать в определённое время суток, используй
таблицу `alerts` (см. `docs/explanation/survivorship_engine.md` —
`patient_constraints` была удалена с canonical Studio БД в 2026-05):

```python
db.save_alert(
    type_="medication_interaction",  # или allergy|DNR|other
    message="Причина ограничения (например: vagal_activation prohibit evening)",
    severity="medium",
    source="manual",
)
```

`evaluate_domain_need(domain)` читает активные alerts через
`get_active_constraints()` (legacy сигнатура, читает alerts с
backward-compat алиасами).
