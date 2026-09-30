[English](run_analysis.en.md) · **Русский**

# Как запустить longitudinal-анализ и прочитать результаты

> **Тип документа:** How-to (Diataxis).
> Объяснение что такое longitudinal-анализ: [docs/explanation/data_flow.md](../explanation/data_flow.md).
> Ключевые находки (корреляции): [ARCH_SNAPSHOT.md](../../ARCH_SNAPSHOT.md).

---

## Когда запускать

- Раз в 1–3 месяца или при значимом изменении данных (новые лабы, смена фазы лечения)
- Перед генерацией конституций — они зависят от свежего longitudinal-анализа
- Когда integrity_tests выдаёт предупреждение об устаревшем analysis

---

## Запуск

Анализ занимает 5–10 минут, запускать через nohup:

```bash
ssh <studio_ssh> \
  "nohup /opt/homebrew/bin/python3.11 ~/health_scripts/longitudinal_analysis.py \
   > /tmp/longitudinal.log 2>&1 &"

# Следить за прогрессом:
ssh <studio_ssh> "tail -f /tmp/longitudinal.log"
```

---

## Что создаётся

| Артефакт | Путь | Содержимое |
|----------|------|------------|
| Excel | `outputs/longitudinal_analysis.xlsx` | 4 листа (см. ниже) |
| agent_report | `health.db → agent_reports` | `agent_type='longitudinal_analysis'`, поле `raw_output` |

**Листы Excel:**
1. Корреляционная матрица — Spearman r для 78 пар метрик (min_pairs=30)
2. Лаговые корреляции — предикторы vs таргеты, лаги 1/2/3 дня
3. Лабы vs метрики — через ±7d окно
4. Recovery trajectory — текущие 90d vs pre-illness baseline (% и slope)

Синхронизировать Excel на MacBook после запуска:
```bash
rsync -av <studio_ssh>:~/health_scripts/outputs/longitudinal_analysis.xlsx \
      ~/health_scripts/outputs/
```

---

## Как читать корреляционную матрицу

| r | Интерпретация |
|---|---------------|
| ≥ 0.5 | Сильная связь — обоснован алерт / протокол |
| 0.15–0.5 | Средняя — использовать с осторожностью |
| < 0.15 | Слабая — не использовать в DOMAIN_SIGNALS |

Обратить внимание на **направление**: если "больше метрики = хуже исход" — это INVERTED.

**Текущие находки** — в отчёте последнего анализа (`agent_reports`,
`agent_type='longitudinal_analysis'`). Числа — в данных тенанта; в документацию
они не переносятся.

---

## Как результаты попадают в конституции

После запуска `longitudinal_analysis.py` запись появляется в `agent_reports`:
- `agent_type='longitudinal_analysis'`
- `raw_output` — текстовый блок трендов и фаз

`generate_constitutions.py` читает этот `raw_output` через `_get_longitudinal_context()`.
Поэтому конституции нужно регенерировать после каждого нового longitudinal-анализа.

---

## Если анализ упал с ошибкой

```bash
# Проверить лог:
ssh <studio_ssh> "cat /tmp/longitudinal.log | grep -i error"

# Частая причина: нет данных за период
ssh <studio_ssh> \
  "python3.11 -c \"import health_db; db=health_db.HealthDB(); print(db.get_stats(90))\""
```
