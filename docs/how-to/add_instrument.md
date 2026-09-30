[English](add_instrument.en.md) · **Русский**

# Как добавить новый опросник

> **Тип:** How-to (Diataxis).
> Архитектура: [docs/explanation/survivorship_engine.md](../explanation/survivorship_engine.md).
> Каталог: `~/health/data/instruments/*.json`.

## Когда использовать

Когда нужен ещё один валидированный опросник (например, PHQ-9 для депрессии или GAD-7 для тревоги) в дополнение к уже подключённым.

## Предусловие

- Валидированный текст пунктов в виде PDF / DOI / опубликованной таблицы.
- Правила скоринга (формула, диапазон).
- Каденс заполнения (90 / 30 / 14 дней).

## Шаги

### 1. Создать `~/health/data/instruments/<id>.json`

Структура совпадает с `isi.json`/`mfsi_sf.json`:

```json
{
  "id": "phq9",
  "name": "PHQ-9 — Patient Health Questionnaire",
  "version": "v1.0_validated_ru",
  "source": "Kroenke & Spitzer 2001 — sourced PDF (DOI: 10.1046/j.1525-1497.2001.016009606.x)",
  "validation_status": "validated_ru",
  "cadence_days": 30,
  "cadence_offset_days": 15,
  "recall_period": "past_2_weeks",
  "response_scale": {"min": 0, "max": 3,
    "labels": {"0": "совсем нет", "1": "несколько дней", "2": "больше половины", "3": "почти каждый день"}},
  "items": [
    {"id": "q1", "text": "Малый интерес или удовольствие от дел", "subscale": "total"},
    ...
  ],
  "subscales": [{"id": "total", "items": ["q1", "q2", ...], "direction": "symptom_higher_worse"}],
  "scoring_formula": "total = sum(items); 0-4 minimal, 5-9 mild, 10-14 moderate, 15-19 moderately severe, 20-27 severe",
  "thresholds": {"mild": 5, "moderate": 10, "severe": 20},
  "shadow_rules": [
    {"item_or_subscale": "total",
     "proxies": ["stress_high_min", "sleep_score"],
     "min_window_days": 7, "persistence_days": 3,
     "divergence_threshold": 0.5,
     "note": "depression vs stress/sleep proxies"}
  ],
  "wording_version_hash": "phq9_v1_2026-05-18"
}
```

### 2. Sanity-check через Python

```bash
ssh <studio_ssh> "cd ~/health_scripts && /opt/homebrew/bin/python3.11 -c '
import assessment_dialog as ad
ins = ad._load_instrument_by_source_id(\"phq9\")
print(\"loaded:\", ins[\"name\"], len(ins[\"items\"]), \"items\")
'"
```

### 3. Дать assessment_scheduler подхватить

Следующий cron-проход (ежедневно 03:30) создаст задачу `assessment:phq9` с дедлайном `today + cadence_offset_days`. Или вручную для immediate смока:

```bash
ssh <studio_ssh> "cd ~/health_scripts && /opt/homebrew/bin/python3.11 assessment_scheduler.py"
```

### 4. Заполнить через Telegram

Получишь сообщение с inline-кнопками — нажмёшь «📋 Заполнить». Диалог пройдёт по items автоматически.

### 5. Проверить, что данные пришли

```sql
SELECT date, test_name, value FROM lab_results
WHERE source = 'instrument:phq9'
ORDER BY date DESC;
```

И что watchdog `check_assessments_freshness()` теперь знает про phq9 (он автоматически итерирует каталог).

## Что НЕ нужно

- Не патчить `assessment_scheduler.py`, `assessment_dialog.py`, `assessment_importer.py` — они итерируют каталог сами.
- Не патчить `survivorship_analyzer.py` — он подхватывает `shadow_rules` из JSON.
- Не патчить `integrity_tests.py` — `check_assessments_freshness()` итерирует каталог.

## Антипаттерны

- **Парафраз пунктов без указания статуса** → trend integrity ломается через 90 дней. Всегда указывай `validation_status` и `wording_version_hash`.
- **shadow_rules с одним прокси и threshold=0** → ложные срабатывания. Минимум `min_window_days: 7` и `persistence_days: 3`.
- **Не обновлять wording_version_hash при редактировании текста** → `assessment_importer` молча принимает старые заполнения как валидные.
