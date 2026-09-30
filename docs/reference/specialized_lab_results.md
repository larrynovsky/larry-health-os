[English](specialized_lab_results.en.md) · **Русский**

# Reference: specialized_lab_results

Спец-панели вне биохимии крови (BL-LAB-CANON-2, 2026-07-04). Одна long-format
таблица с дискриминатором `panel_type` для панелей, не ложащихся в модель
`lab_results` (кровь): микробиом, аутоантитела, метаболомика, жирные кислоты,
микроэлементы, аминокислоты, электрофорез, стул-маркеры.

## Схема

| колонка | тип | смысл |
|---|---|---|
| `id` | INTEGER PK | |
| `date` | TEXT | дата забора (ISO) |
| `source` | TEXT | провенанс `doc:<file>` |
| `panel_type` | TEXT | дискриминатор (см. ниже) |
| `specimen` | TEXT | blood / stool / urine / null |
| `analyte_raw` | TEXT | имя как распознано |
| `analyte_canonical` | TEXT | канон (nullable) |
| `value` | REAL | числовое значение |
| `value_text` | TEXT | нечисловое (pos/neg/титр) |
| `unit`, `ref_low`, `ref_high`, `flag` | | единица, референс, флаг |
| `method` | TEXT | ГХ-МС / ИФА / проточная цитометрия / … |

Индексы: `date`, `panel_type`, UNIQUE(`date`,`analyte_raw`,`source`,`panel_type`).

## panel_type

`microbiome`, `autoantibodies`, `metabolomics`, `fatty_acids`, `trace_elements`,
`amino_acids`, `electrophoresis`, `stool_markers`, `gastro_markers`, `immunophenotype`,
`urinalysis_sediment`, `other`.

## Единственный писатель (Primary-Based Protocol)

Таблицу пишет ТОЛЬКО `lab_specialized.promote_specialized(run_id, execute)`
(Таненбаум §7.5, как `lab_promote` для `lab_results`). Классификатор
`lab_specialized._classify(raw_name, panel, unit)` маршрутизирует staging-строку:
`blood` → `lab_results` (пропуск здесь), иначе → specialized с `panel_type`.
Идемпотентность: DELETE по `source` перед вставкой (реклассификация не плодит дубли);
строки без значения пропускаются.

## Датчик

`integrity_tests.check_specialized_lab_health` — каждая строка обязана иметь
`panel_type` и `value` или `value_text` (иначе пустой мусор). triage→Telegram.

## Потребители

`labs_db.build_specialized_context()` возвращает СВОДКУ (счётчики по `panel_type`
и результаты вне референса) с ЯВНЫМ тегом давности.
Возможный риск: без даты старый результат можно принять за текущий.
Сводка встроена в `monthly_consilium` и `generate_constitutions`; обобщение
ограничивает объём контекста даже при большом наборе результатов.

## Связанное

- Модель крови: `lab_results`, писатель `lab_promote` (замысел: `subsystem_intent.yaml::lab_recognizer`).
- Триаж low-confidence staging: `lab_triage.triage` (rejected/review/gold).
- Ревью value-конфликтов: `health/lab_conflicts_review_2026-07-05.md` (iCloud).
