[English](howto_regenerate_descriptions.en.md) · **Русский**

# Как перегенерировать русские описания вариантов

*Тип документа: инструкция. Пошаговый рецепт для конкретной задачи.*

**Когда применять:** после импорта новых вариантов из VCF, после обновления ClinVar,
или если нужно переписать описания для отдельного гена (например, после уточнения
системного промпта).

---

## Шаг 1. Проверить, сколько строк без описания

Подключитесь к базе на Studio и запустите:

```sql
SELECT COUNT(*)
FROM genetic_variants
WHERE description_ru IS NULL
  AND significance IN (
    'Pathogenic', 'Likely_pathogenic',
    'Pathogenic/Likely_pathogenic',
    'Likely pathogenic',
    'Pathogenic/Likely pathogenic',
    'Pathogenic, low penetrance'
  );
```

Если результат 0 — всё уже сгенерировано, делать нечего.

---

## Шаг 2 (опционально). Сбросить описания для конкретного гена

Если нужно переписать описания только для одного гена, а не для всех пустых:

```sql
UPDATE genetic_variants
SET description_ru = NULL
WHERE gene = 'BRCA2';
```

После этого скрипт на шаге 3 охватит только сброшенные строки.

---

## Шаг 3. Запустить скрипт генерации на Studio

Скрипт живёт на MacBook в `/tmp/generate_variant_descriptions.py`, но работает
только на Studio, потому что там находится база данных.

Если скрипт уже скопирован на Studio — запускайте прямо:

```bash
ssh <studio_ssh> \
  "cd ~/health_scripts && python3.11 /tmp/generate_variant_descriptions.py 2>&1 | tee /tmp/gen_desc.log"
```

Если нужно обновить скрипт перед запуском:

```bash
scp /tmp/generate_variant_descriptions.py <studio_ssh>:/tmp/
ssh <studio_ssh> \
  "python3.11 /tmp/generate_variant_descriptions.py 2>&1 | tee /tmp/gen_desc.log"
```

Скрипт читает ключ из `~/.health_secrets/anthropic_key`, использует модель
`claude-haiku-4-5-20251001` и обрабатывает пары (gene, conditions) батчами по 50.
Колонка `description_ru` создаётся автоматически, если её ещё нет.

---

## Шаг 4. Следить за прогрессом

В соседнем терминале:

```bash
ssh <studio_ssh> "tail -f /tmp/gen_desc.log"
```

Иллюстративный вывод; все количества ниже вымышлены. Пример использует те же
300 импортированных строк, что и объяснение фильтрации: генерация охватывает
120 пар (gene, conditions) во всех 300 строках до фильтрации отображения.
При размере пакета 50 получаются три пакета: 50 + 50 + 20 пар, 120 + 130 + 50 строк.

```
Pairs to generate: 120
  Batch 1/3 (50 pairs)... OK (120 rows)
  Batch 2/3 (50 pairs)... OK (130 rows)
  Batch 3/3 (20 pairs)... OK (50 rows)
Done: 120/120 pairs, 0 errors
```

Если батч завершился с ошибкой `ERR:`, скрипт продолжает со следующего батча.
Повторный запуск безопасен — уже заполненные строки пропускаются.

---

## Шаг 5. Верифицировать результат

```sql
SELECT gene, description_ru
FROM genetic_variants
WHERE significance IN (
  'Pathogenic', 'Likely_pathogenic',
  'Pathogenic/Likely_pathogenic',
  'Likely pathogenic',
  'Pathogenic/Likely pathogenic',
  'Pathogenic, low penetrance'
)
LIMIT 5;
```

Убедитесь, что поле заполнено, описание по-русски и не содержит аббревиатур типа
«SNP», «rs-числа» или генетической нотации.

Проверьте счётчик оставшихся NULL (должен быть 0 или только те, у кого gene IS NULL):

```sql
SELECT COUNT(*)
FROM genetic_variants
WHERE description_ru IS NULL
  AND gene IS NOT NULL
  AND significance IN (
    'Pathogenic', 'Likely_pathogenic',
    'Pathogenic/Likely_pathogenic',
    'Likely pathogenic',
    'Pathogenic/Likely pathogenic',
    'Pathogenic, low penetrance'
  );
```

---

## Шаг 6. Перезапустить дашборд

```bash
ssh <studio_ssh> "pkill -f 'python.*dashboard.py'"
```

launchd поднимет процесс автоматически. Дашборд обновится через несколько секунд.
Откройте `/genome` и проверьте, что описания отображаются под названиями заболеваний.
