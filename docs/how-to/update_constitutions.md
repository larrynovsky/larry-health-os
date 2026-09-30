[English](update_constitutions.en.md) · **Русский**

# Как обновить конституции здоровья

> **Тип документа:** How-to (Diataxis).
> Правила структуры и тона: [CONSTITUTION_RULES.md](../../CONSTITUTION_RULES.md).
> Объяснение зачем они существуют: [docs/explanation/how_gp_works.md](../explanation/how_gp_works.md).

---

## Когда обновлять

Обычно — никогда руками. С 2026-09-25 (решение владельца: «пересборка — только по триггерам,
когда появились новые вводные») каждое воскресенье в 05:30 (тенант — в 05:50) launchd
`com.larry.health.constitutions[.partner]` зовёт `generate_constitutions.py --if-changed`.
Он сверяет отпечаток вводных — состав подтверждённых связей, клинические фазы, даты заборов
крови, медицинские периоды, объём генома — и пересобирает все пять доменов, только если
что-то из этого сменилось. Недельный дрейф средних вводной не считается. Что именно сменилось,
пишется в лог `~/health_constitutions*.log`.

Руками — когда нужно пересобрать один домен или поменялся сам промпт:

```bash
ssh <studio_ssh> "/opt/homebrew/bin/python3.11 ~/health_scripts/generate_constitutions.py sleep"
```

Если расписание молчит дольше 8 дней, ночной монитор пишет «пересборка конституций по новым
вводным молчит» — проверьте, загружен ли launchd и что в логе.

---

## Предусловие: свежий longitudinal_analysis

Конституции зависят от `longitudinal_analysis` в `agent_reports`. Перед генерацией проверить:

```bash
ssh <studio_ssh> "python3.11 ~/health_scripts/generate_constitutions.py --check-only"
```

Если `longitudinal_analysis` устарел — сначала запустить его:

```bash
ssh <studio_ssh> \
  "nohup /opt/homebrew/bin/python3.11 ~/health_scripts/longitudinal_analysis.py \
   > /tmp/longitudinal.log 2>&1 &"
# Занимает 5–10 минут. Следить: tail -f /tmp/longitudinal.log
```

---

## Запуск генерации

```bash
# Все пять доменов (~10–15 мин, запускать через nohup):
ssh <studio_ssh> \
  "nohup /opt/homebrew/bin/python3.11 ~/health_scripts/generate_constitutions.py \
   > /tmp/constitutions.log 2>&1 &"

# Один домен (быстрее, ~2–3 мин):
ssh <studio_ssh> \
  "/opt/homebrew/bin/python3.11 ~/health_scripts/generate_constitutions.py sleep"
```

Доступные домены: `sleep`, `nutrition`, `stress`, `nervous_system`, `movement`

Следить за прогрессом:
```bash
ssh <studio_ssh> "tail -f /tmp/constitutions.log"
```

---

## Что происходит при генерации

1. Скрипт читает SNP из `genetic_variants` (не `promethease_variants` — та пустая)
2. Читает клиническую историю из `periods` (только медицинские типы)
3. Читает longitudinal-анализ из `agent_reports.raw_output`
4. Двухшаговая генерация:
   - Шаг 1: `_build_prompt()` → Claude Opus → свежий текст
   - Шаг 2: если есть предыдущая версия → `_build_diff_prompt()` → вставка `## Что изменилось`
5. После всех доменов: `_run_alert_review()` → анализирует diff → алерты в Telegram

**⚠️ Генерация прерывается если:**
- SNP count = 0 (ни одного варианта для домена)
- Нет longitudinal данных в agent_reports

---

## После генерации

Файлы обновляются в `constitutions/`. Проверить:

```bash
ssh <studio_ssh> "head -5 ~/health_scripts/constitutions/sleep.md"
# Должна быть свежая дата в **Обновлено:**
```

Синхронизировать на MacBook:
```bash
rsync -av <studio_ssh>:~/health_scripts/constitutions/ \
      ~/health_scripts/constitutions/
```

---

## Ручная правка конституции

Конституции — нарративные документы, их можно редактировать вручную.
Структура обязательна (SWOT + Нарратив + Базовые рекомендации + Рекомендации специалистам).
Формат секций: [CONSTITUTION_RULES.md](../../CONSTITUTION_RULES.md).

После ручной правки — зафиксировать в git:
```bash
cd ~/health_scripts && git add constitutions/ && git commit -m "update: constitution [domain]"
```


---

## Эпистемическая дисциплина (дефолт ON)

С 2026-06-20 генерация по умолчанию применяет калибровку уверенности
(`epistemic_skill/`) — менять ничего не нужно. Отключить на конкретный прогон:

```bash
ssh <studio_ssh> \
  "EPISTEMIC_DISCIPLINE=off /opt/homebrew/bin/python3.11 ~/health_scripts/generate_constitutions.py sleep"
```

Та же дисциплина применяется к синтезу координатора консилиума (`wellally_consult`).
Зачем и как устроено: [docs/explanation/epistemic_discipline.md](../explanation/epistemic_discipline.md).
