[English](add_clinical_threshold.en.md) · **Русский**

# Как добавить или изменить клинический порог

*Тип документа: инструкция. Пошаговый рецепт для конкретной задачи.*
*Когда применять: появилась новая клиническая норма, или существующий порог нужно вынести из кода в `absolute_thresholds`, или изменить значение существующего.*

Контекст и обоснование — `docs/explanation/section9_data_not_code.md`. Схема — `docs/reference/absolute_thresholds.md`.

---

## Изменить значение существующего порога

Порог уже в таблице → правка только данных, без кода:

```sql
UPDATE absolute_thresholds
   SET value = <новое>, source = '<именованный документ>', source_date = '<дата документа>',
       norm_kind = '<reference_interval|decision_threshold|stratified_target|personal>',
       next_review = '<YYYY-MM-DD — когда перепроверить>'
 WHERE metric = '<канон-имя>' AND direction = '<floor|ceiling>' AND band_label = '<...>' AND variant = '<...>';
```

С 2026-09-02 строка нормы обязана нести **вид** и **срок годности** (`docs/explanation/norm_kinds.md`).
`unclassified` разрешён только миграции; человек, трогающий строку, выносит вердикт. `metric` — канон-имя
`lab_canon.normalize` (иначе порог не встретит данных — `check_threshold_names_reach_data`). Просроченный
`next_review` — красное ночью; это и есть «регулярно апдейтить» как механизм.

Кода это не касается — он читает значение из таблицы. Изменение делается на Studio (канонический узел), не в коде.

## Добавить лабораторный порог решения — добавь ДОКУМЕНТ, не число (с 2026-09-02)

Лабораторные пороги `safety_net` не набираются: они выводятся из документа. Новый аналит из CTCAE —
одна строка в `data/norm_docs/ctcae_lab_terms.json` (терм → канон-имя, направление, единица), затем
`python3 -c "import norm_documents as nd; nd.snapshot_ctcae()"` и коммит снимка вместе с картой:
coherence-тест `test_snapshot_equals_import_of_document` не даст разойтись. Новая версия CTCAE —
файл в `data/norm_docs/`, карта термов своей версии, запись в `norm_documents.CTCAE_FILES` и `DOCUMENTS`, переключатель
`CTCAE_CURRENT`, оба снимка (`snapshot_ctcae(id)`), `diff_ctcae(old, new)` в тексте коммита; прежние строки сид уводит в архив сам. Аналит,
которого нет ни в одном документе, — вид 1: судится референсом бланка автоматически, порога
добавлять не нужно. Личный порог (как Amylase) — единственный случай ручной строки, с
`source` вида `*_owner_personal_*` и `norm_kind='personal'`.

## Вынести новый порог из кода (daily-метрики; лабораторные — см. выше)

### Шаг 1. Засеять в `_seed_absolute_thresholds()` (health_db.py)

Добавить строку в список `seeds`. Поля зависят от формы:

- **Absolute:** `(metric, direction, value, reason_template, source, source_date)` — `kind` по умолчанию `absolute`.
- **Relative** (множитель базиса, напр. `hrv < avg7 * 0.82`): задать `kind='relative'`, `baseline='avg7_hrv'`, `value=0.82`, и `variant` если на метрику несколько (`food`/`deep`).
- **Полоса** (несколько порогов тяжести): по строке на полосу с разным `band_label` (`very_low`/`low`).

Seed идемпотентен (`INSERT OR IGNORE`). Стартовое значение в коде живёт ТОЛЬКО здесь — это легитимный seed-контур §9.

### Шаг 2. Переписать рабочий код на чтение

Было (литерал — нарушение §9):
```python
low_ready = readiness < 65
```
Стало (чтение из дома):
```python
import health_db as _db
low_ready = readiness < _db.get_threshold("readiness", "floor")
```

Для relative — применить множитель к базису:
```python
mult = _db.get_threshold("hrv", "floor", variant="deep")   # kind=relative
low_hrv = hrv < avg7_hrv * mult
```

`get_threshold` бросает `KeyError`, если порог не засеян — это нарочно: громкий отказ лучше, чем тихо неверный порог. Не оборачивать в `try/except` с дефолтом.

### Шаг 3. Удалить литерал

После шага 2 числа в коде быть не должно — иначе split-brain (значение в двух местах разойдётся).

### Шаг 4. Пин против split-brain

Тест, который флипает порог в таблице и проверяет, что поведение меняется (с захардкоженным числом тест бы не флипнул). Образец — `tests/unit/test_threshold_from_table.py`.

### Шаг 5. Если тесту нужно ДРУГОЕ значение порога — переопредели, не вставляй

Фикстура `db` с потока F (2026-06-28) **сама сеет канонические `absolute_thresholds`**
(тем же `_seed_absolute_thresholds()`, что и боевой `init_db`). Поэтому код, читающий
порог через `get_threshold`, в тестах больше не падает `KeyError` сам по себе.

Тест, которому нужно ДРУГОЕ значение, обязан **переопределить** строку через
`INSERT OR REPLACE` (UPDATE), а не `INSERT` — иначе коллизия по
`UNIQUE(metric, direction, band_label, variant)`. Образец — `_seed_floor` в
`test_threshold_from_table.py` (использует `INSERT OR REPLACE`).

## Вынести вердикт о виде нормы (остаток `unclassified`)

Ночной прогон печатает `[health] норм без вида: N` и предложения свидетеля: `HGB floor warn 12 vs
лаборатория 13.5–17.5 (11%) → совпадает: кандидат reference_interval`. Вопрос к каждой строке один:
**это статистика здоровых (референс) или число из исходов (порог решения)?** Совпало с лабораторией →
почти всегда референс. Расходится (Glucose 126 против 70–100) → порог решения, назови документ
(`source='ADA_2025'`). Личный (`Amylase`) → `personal`. Вердикт — `UPDATE ... SET norm_kind, source,
source_date, next_review` как выше; машина сама не пишет.

## Кто оракул

Кто вправе менять значение — зависит от класса (см. объяснение §9): нормы тела — пациент (личное) или литература (популяционное); рубрики метода — автор методологии; параметры анализа — инженер. Источник (`source`) фиксирует, чьё это решение.

## Связанное

- `docs/reference/absolute_thresholds.md` — схема и API.
- `docs/explanation/section9_data_not_code.md` — почему так.
