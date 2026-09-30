[English](wave8_observations.en.md) · **Русский**

# Wave 8 — Data sources & cascades (наблюдения)

Файл-черновик для накопления наблюдений во время эксплуатации Phase 3. После закрытия Phase 3 → конвертация в полноценный `wave8_data_sources_plan.md`.

Контракт: пишем коротко, по одному наблюдению на пункт. Не пытаемся проектировать решение — фиксируем наблюдение и context.

## 1. Данные из внешних источников (не вводятся руками)

Наблюдения пользователя 2026-05-15:

### 1.1 Вес → Apple Health

- `patient_profile.identity.weight_kg` сейчас редактируемо inline-edit'ом
- Правильно: приходит из `daily_metrics.weight` через Apple Health импорт
- В дашборде должно быть **read-only** с пометкой «Apple Health», hover-tooltip «изменить в Apple Health»
- Open question: показывать ли тренд за 30/90 дней рядом со значением?

**Discovery 2026-05-16:** `SELECT updated_by, COUNT(*) FROM patient_profile GROUP BY updated_by` показал что Apple Health **не пишет в `patient_profile`** напрямую. Источники: manual, dashboard, patch_personal_data.py, rule9-audit. Это значит read-only-блокировка — не «защита от перезаписи», а **новая интеграция** sync `daily_metrics.weight` → `patient_profile.identity.weight_kg`. Отдельная подзадача в Wave 8.

### 1.2 (резерв) Какие ещё поля приходят из Apple Health?

Кандидаты на проверку — нужна разведка `daily_metrics`:
- `heart_rate_*` (resting, hrv)
- `sleep_*` (total, deep, rem, efficiency)
- `steps`, `active_energy`
- `body_fat_percentage` (если есть умные весы)
- `bp_systolic`, `bp_diastolic` (если меряет)

Для каждого — проверить, есть ли соответствующий `patient_profile.X` который дублирует.

### 1.3 (резерв) Что ещё приходит из других источников?

- Oura: sleep/HRV/recovery — должны быть read-only в дашборде
- Лабы → `lab_results` — уже отдельная страница, не profile
- Геном → `genetic_variants` — read-only по дизайну

## 2. Cascade при действиях

Наблюдения пользователя 2026-05-15:

### 2.1 Confirm hypothesis → создать task из `payload.test` ✅ ЗАКРЫТО

Реализовано в Phase 3.3C-extend (commit 70e92ef, 2026-05-16):
- При confirm с непустым `payload.test` → INSERT в tasks
- source=dashboard_confirm, type=followup, priority=medium, status=open
- text трим до 500 символов
- audit через `_log_edit("tasks", task_id, "create_from_hypothesis_confirm", ...)`

Остаётся в Wave 8: `payload.prediction` тоже может создавать task с `type=check` — не реализовано.

### 2.2 (резерв) Reject hypothesis → что должно произойти?

- Сейчас reject = active=0 + resolution_type=rejected
- Вопрос: создавать ли что-то downstream? Возможно — patterns row «эта связь не подтвердилась»? Или ничего, и это норма?

### 2.3 (резерв) Complete experiment → update linked hypothesis

- Если эксперимент имеет `linked_hypothesis_id` — после complete с `result` нужно append в `hypothesis.payload.experiment_results[]`
- Это даёт CBCR контекст «гипотеза проверена экспериментом X, результат Y»
- Сейчас полностью отсутствует

### 2.4 (резерв) Retire protocol → что?

- Возможно: добавить запись в `patterns` «протокол X не дал эффекта за N дней»
- Или: ничего, retire — это просто «прекратили практику»

## 3. Поля из документов врача

Наблюдения пользователя 2026-05-15:

### 3.1 `consultations.key_findings` — extract из source_file

- Сейчас редактируется руками через inline-edit
- Правильно: парсится из `source_file` (PDF врача) через LLM-pipeline или regex-extraction
- В дашборде read-only, ссылка «исходный документ»
- Помечается `key_findings_source='extracted'` vs `'manual'`

### 3.2 (резерв) Какие другие поля приходят из документов?

Нужно понять что лежит в `source_file` обычно:
- Диагнозы (МКБ-коды?)
- Назначения (препараты, дозы)
- Результаты обследований
- Дата следующего визита

Не все из них нужны в дашборде, но в `patient_profile.medical.*` могли бы попадать структурно.

## 4. Расширение схем

Вопросы к проектированию схемы (без инвентаря личного профиля):

### 4.1 `profile.identity.routine.*` — мало содержательных полей

- Независимо придуманный ключ для обсуждения: `routine.example_sketching` (учебный пример регулярного рисования, не запись профиля)
- Что ещё должно быть? Нужна разведка существующих ключей + brainstorm:
  - `routine.coffee` (сколько чашек, когда)
  - `routine.exercise` (тип, частота)
  - `routine.sleep_window` (отбой/подъём)
  - `routine.intermittent_fasting` (окна)
  - `routine.supplements` (текущий стек)
  - `routine.meditation` / `routine.breathwork`
  - `routine.travel_pattern` (как часто, надолго)
- Open question: routine vs experiments. Где грань? Routine = «постоянная практика», experiment = «временная проверка с метриками»?

### 4.2 (резерв) Какие ещё категории профиля недоразвиты?

Запросить `SELECT category, COUNT(*) FROM patient_profile GROUP BY category`. Сравнить с целевой картой.

## 5. Анти-паттерны которые нашли

Наблюдения 2026-05-15:

### 5.1 Inline-edit для производных значений = ошибка

- Если значение приходит из внешнего источника — редактирование через UI создаёт **divergence**: дашборд показывает X, источник показывает Y, агенты читают то одно то другое
- Защита: `value_source` колонка или unique table `derived_profile_fields` со списком ключей где edit запрещён

### 5.2 (резерв)

## 6. Wishlist / Wild ideas

### 6.1 Visualisation тренда рядом со значением

- На карточке `patient_profile.identity.weight_kg = <N>` показать sparkline за 90 дней
- Это требует JOIN с `daily_metrics`
- Дёшево если sparkline = ascii (▁▂▃▄▅▆▇), дорого если canvas/SVG

### 6.2 (резерв)

## Конверсия в план

Когда Phase 3 закроется:

1. Каждая секция выше → отдельный этап Wave 8 (W8-A, W8-B, ...)
2. Уточнить time-estimate
3. Решить очерёдность — что блокирует что
4. Перенести в `docs/explanation/wave8_data_sources_plan.md`
5. ROADMAP §19

Сейчас — file in flight. Дополнять по мере наблюдений во время эксплуатации Phase 3.
