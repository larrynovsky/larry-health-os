[English](reference_genetic_variants_table.en.md) · **Русский**

# Справочник: таблица genetic_variants и genome() view

*Тип документа: справочник. Для просмотра во время работы с кодом или БД.*

---

## Схема таблицы `genetic_variants`

| Колонка | Тип | Содержимое |
|---|---|---|
| `rsid` | TEXT | Идентификатор SNP из dbSNP (например, `rs1799945`) |
| `gene` | TEXT | Символ гена (например, `HFE`, `BRCA2`) |
| `genotype` | TEXT | Двухсимвольная строка аллелей пользователя (например, `AG`, `CC`) |
| `significance` | TEXT | Оценка ClinVar (см. список ниже) |
| `conditions` | TEXT | JSON-массив строк с названиями заболеваний/состояний |
| `clinical_summary` | TEXT | HGVS-нотация (техническое, для экспортов) |
| `effect_allele` | TEXT | Один символ — патогенный аллель по ClinVar; NULL если неизвестен |
| `effect_allele_status` | TEXT | Метка происхождения `effect_allele` (pipeline-техническое) |
| `description_ru` | TEXT | Русское описание ≤25 слов; NULL если ещё не сгенерировано |

---

## Значения `significance`, которые попадают в /genome

```
'Pathogenic'
'Likely_pathogenic'
'Pathogenic/Likely_pathogenic'
'Likely pathogenic'
'Pathogenic/Likely pathogenic'
'Pathogenic, low penetrance'
```

Значения `Benign`, `Likely benign`, `Uncertain significance`, `Conflicting` и прочие
не отображаются. Строки с этими статусами в таблице есть, но CTE их отсекает.

---

## Классификация зиготности в CTE

Вычисляется в подзапросе `classified` функции `genome()` в `dashboard_routers/views.py`.

| zyg_rank | Условие | Значение | Значок на странице |
|---|---|---|---|
| 0 | `effect_allele` не NULL, оба символа `genotype` = `effect_allele` | гомозиготный | «обе копии» (красный) |
| 1 | `effect_allele` не NULL, ровно один символ `genotype` = `effect_allele` | гетерозиготный / носитель | «носитель» (жёлтый) |
| 2 | `effect_allele IS NULL` | зиготность неизвестна | без значка |
| 99 | `effect_allele` не NULL, ни один символ не совпадает | нормальный генотип | строка не показывается |

Определение зиготности работает только для двухсимвольных `genotype` (`length(genotype)=2`).
Строки с более длинным или пустым значением попадают в zyg_rank=2 (неизвестно).

После группировки `GROUP BY gene, conditions` итоговая зиготность = `MIN(zyg_rank)`,
то есть выбирается наиболее тревожный вариант из всех SNP пары.

---

## Сортировка в /genome

Строки отдаются в порядке `ORDER BY _sig_order, _zyg_order, gene`:

1. Сначала `Pathogenic` (sig_rank=0), затем `Likely pathogenic` (sig_rank=1)
2. Внутри группы значимости: гомозиготные (0) → гетерозиготные (1) → неизвестные (2)
3. Внутри группы зиготности: алфавит по `gene`

---

## generate_variant_descriptions.py

**Расположение:** `/tmp/generate_variant_descriptions.py` (MacBook и Studio)

**Запуск:**
```bash
ssh <studio_ssh> "python3.11 /tmp/generate_variant_descriptions.py"
```

**Что делает:**
1. Читает уникальные пары `(gene, conditions)` из `genetic_variants` где `description_ru IS NULL` и `significance` в списке патогенных
2. Группирует в батчи по 50
3. Отправляет каждый батч в Claude Haiku (`claude-haiku-4-5-20251001`) через Anthropic API
4. Парсит JSON-ответ и записывает `description_ru` в строки, у которых совпадает `(gene, conditions)` после нормализации

**Ключ API:** `~/.health_secrets/anthropic_key`

**Системный промпт (правила описания):**
- Одно предложение на русском, не более 25 слов
- Объяснять функцию гена и связанный риск для неспециалиста
- Не использовать аббревиатуры белков, rs-числа или молекулярную нотацию
- Не писать слова «патогенный», «вариант», «мутация» — вместо них «риск» или «функция органа»
- Если `conditions` пустое или «not provided» — кратко описать только функцию гена

**Идемпотентность:** повторный запуск безопасен, уже заполненные строки пропускаются.
Колонка `description_ru` создаётся автоматически если её нет (`ALTER TABLE ADD COLUMN`).
