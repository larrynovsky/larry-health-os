[English](absolute_thresholds.en.md) · **Русский**

# Справочник: таблица `absolute_thresholds` и чтение порогов

*Тип документа: справочник. Для просмотра во время работы с кодом или БД.*
*Дом норм тела по §9. Объяснение «почему» — `docs/explanation/section9_data_not_code.md`.*

---

## Назначение

`absolute_thresholds` — единственный рантайм-источник клинических порогов тела (готовность, давление, сон, множители вариабельности и шагов). Код читает значения отсюда, не из литералов. Введена Sprint 2/Р-1 (2026-05-22), идентичность перестроена 2026-06-28 (поток F §9).

## Схема

| Колонка | Тип | Смысл |
|---|---|---|
| `id` | INTEGER PK | автоинкремент |
| `metric` | TEXT | метрика: `hrv`, `readiness`, `sleep_deep`, `bp_systolic`, ... |
| `direction` | TEXT | `floor` (срабатывает при value ниже) или `ceiling` (выше) |
| `value` | REAL | для `kind=absolute` — сам порог; для `kind=relative` — множитель |
| `reason_template` | TEXT | шаблон сообщения, `{val:.0f}` подставляется |
| `source` | TEXT | провенанс: `threshold_analysis_*` (личный p10), `ESC_AHA_2023` (клинический), ... |
| `source_date` | TEXT | дата источника |
| `active` | INTEGER | 1 — учитывается |
| `kind` | TEXT | `absolute` (value — порог) или `relative` (value — множитель `baseline`) |
| `baseline` | TEXT | для `relative` — базис: метрика (`avg7_hrv`, `avg30_hrv`) или **`ULN`/`LLN`** — референс бланка той строки `lab_results`, которую судим (CTCAE-грейды выражены кратными; с 2026-09-02) |
| `band_label` | TEXT | полоса ТЯЖЕСТИ: `very_low`/`low`/...; пусто — единственный порог |
| `variant` | TEXT | НАЗНАЧЕНИЕ: `food`/`deep`/`lifestyle`; пусто — безымянный порог |
| `norm_kind` | TEXT | ВИД нормы (CHECK): `reference_interval` · `decision_threshold` · `stratified_target` · `personal` · `unclassified` (вердикт не вынесен). Введён 2026-09-02, объяснение — `docs/explanation/norm_kinds.md` |
| `next_review` | TEXT | срок годности нормы (ISO-дата). Активная строка с прошедшей датой — красное ночью (`check_norm_review_overdue`). NULL — срок не назначен |
| `stratum` | TEXT | для `stratified_target`: какой факт профиля выбрал цель (напр. `onco_history`). NULL для остальных |

**Идентичность:** `UNIQUE(metric, direction, band_label, variant)`. На одну метрику допустимо несколько порогов — разной тяжести (полосы) и разного назначения (варианты). `source` в идентичность не входит — это лишь провенанс.

## Три формы порога

**Absolute** — фиксированное значение. `readiness floor 65`: срабатывает при готовности ниже 65.

**Relative** — множитель базиса. `hrv floor, kind=relative, baseline=avg7_hrv, value=0.82`: срабатывает при вариабельности ниже 0.82 от недельного среднего. Формула в коде: `metric < baseline_value * value`.

**Полосы** — несколько порогов одной метрики, различаемых `band_label`. `sleep_deep floor very_low=30` и `low=50`: две полосы тяжести.

## Контур seed → чтение (§9)

Код держит стартовые значения только в `_seed_absolute_thresholds()` (health_db.py). На `init_db()` они пишутся в таблицу (`INSERT OR IGNORE` по UNIQUE — идемпотентно). Рабочий путь читает из таблицы. Литерала с порогом в рабочем коде быть не должно.

## Чтение (API в `rules_db.py`, ре-экспорт через `health_db`)

```python
get_threshold(metric, direction, band_label="", variant="") -> float
```
Возвращает `value` активного порога по полной идентичности. Бросает `KeyError`, если порог не засеян — это поломка seed-контура, а не повод тихо подставить дефолт (§9: громкий отказ > молчаливо неверный порог). Для `relative` возвращается множитель; базис берётся из колонки `baseline`.

```python
get_absolute_thresholds(direction=None) -> list[dict]
```
Все активные пороги (опционально по `direction`). Поля строки включают `kind`, `baseline`, `band_label`, `variant`. Используется генератором конституций и движком рекомендаций.

## Миграция идентичности (2026-06-28)

Прежний `UNIQUE(metric, direction, source)` допускал лишь один порог на метрику. Перестроен в `UNIQUE(metric, direction, band_label, variant)` через rebuild (создание новой таблицы → `INSERT SELECT` → drop → rename), идемпотентно по наличию старого UNIQUE, с сохранением строк. Колонки `kind`/`baseline`/`band_label`/`variant` добавлены `ALTER`-ом (старые строки получают `kind=absolute`, пустые band/variant).

## Норма из документов (2026-09-02, нить norm-from-documents)

Строки `variant='safety_net'` для лабораторных аналитов **выводятся** из снимка документа `data/norm_docs/ctcae_lab_v5.0.json` (NCI CTCAE v5.0 xlsx → `norm_documents.import_ctcae`): grade 1 → `warn` (кратное 1.0 референса бланка), grade 2 → `urgent`, grade 3 → `critical`; grade 4 не сеется. Прежние 64 набранных строки — `variant='safety_net_legacy', active=0` (архив, не удалены). `safety_net._resolve_thresholds` складывает строки с `ref_low/ref_high` бланка; без референса на бланке — модальный интервал по ≥`norm.witness_min_docs` документам; без него — алерт `norm_unresolved`, не догадка. Аналит без порога решения судится видом 1: выход за напечатанный референс → `warn`. Онкомаркеры в CTCAE отсутствуют → у них нет набранного urgent/critical; динамику судит RCV (`lab_trend_thresholds`, `pct_change` = односторонний RCV из снимка EFLM `data/norm_docs/eflm_bv.json`, `n_readings=2`). Реестр документов — таблица `norm_documents` (id, version, issued, url, sha256, next_check).

## Провенанс нормы (2026-09-02)

`source` + `source_date` — именованный датированный документ, откуда число; `norm_kind` — какой из четырёх объектов медицины оно есть; `next_review` — когда перепроверить. Машина ставит `norm_kind` только по однозначному провенансу (`health_db._NORM_KIND_BY_SOURCE`: `p10/p90_personal`, `owner_personal`, `threshold_analysis_*` → `personal`; `ESC_AHA_*` → `decision_threshold`), остальное остаётся `unclassified` и считается ночью (`check_norm_kind_unclassified`). `next_review` машина не ставит никогда.

Датчики (`integrity_tests`): `check_norm_review_overdue` (красное у владельца) · `check_norm_vs_lab_reference` — норма вида `reference_interval` против модального интервала, напечатанного лабораторией в ≥`norm.witness_min_docs` документах, расхождение >`norm.witness_max_dev` → красное; для `unclassified` печатает предложение вида · `check_norm_coverage` — аналит с ≥`norm.coverage_min_n` измерений без нормы ни в одной таблице `metric`+`direction` и не в `lab_refs` → WARN · `check_threshold_names_reach_data` — имя порога = канон и встречает данные · `check_threshold_source_is_document` — каждая активная строка `safety_net` выведена из снимка документа (`norm_documents`), а каждая запись кэша `system_config.lab_refs` имеет `source='bank_modal'` и `n_docs ≥ norm.witness_min_docs` (число без документа — красное). Особый случай: потолки Glucose (CTCAE v6 — грейды по глюкозе натощак) активны только при `patient_profile.routine.fasting_labs` ≠ false. Конфиг — `system_config` (`norm.*`, зеркало `integrity_tests._NORM_FALLBACK`).

## Связанное

- `docs/explanation/section9_data_not_code.md` — почему нормы не в коде.
- `docs/how-to/add_clinical_threshold.md` — рецепт добавления порога.
- `docs/explanation/norm_kinds.md` — четыре вида нормы и почему у числа есть срок годности.
- `lab_monitoring_schedule` — родственный дом (свежесть лабов, тот же паттерн source-приоритета).
