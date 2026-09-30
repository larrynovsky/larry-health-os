[English](reseed_food_rules.en.md) · **Русский**

# How-to: расширить пищевые правила (медкарта/геном → диета)

Пищевой профиль строит детерминированный слой из курируемых карт. Добавляй правила, когда
появляется НОВОЕ состояние в медкарте или ты хочешь учесть новую связь. **LLM тут не решает** —
только эти карты. Рамка: медкарта доминирует над геномом.

## Где что лежит
- `methodology/clinical_kb/_index.yaml` + `food.yaml` — условия и правила `frame_rule`
  → мутатор рамки {energy, protein, micro, constraints}. `medical_frame()` читает их через `clinical_kb.active_entries()`.
- Ген-связи — там же: геномные триггеры в `_index.yaml`, рамка в `food.yaml`. Слабые SNP не трогаем.
- `food_profile.py::_SUPPRESSED_WHEN_GAIN` — какие ген-ограничения снимаются при наборе/удержании веса.
- `food_staples.py::STAPLES` — несезонные базовые продукты (мясо/яйца/молочное/бакалея/жиры) + теги.
- `food_staples.py::MICRO_FOODS` — микронутриент → где взять.

## Добавить новое состояние
1. На MacBook в `methodology/clinical_kb/_index.yaml` добавь состояние с `trigger.problem_regex`.
   В `food.yaml`, в `by_condition.<id состояния>`, добавь запись `kind: frame_rule` с рамкой в `payload`.
   После деплоя обнови таблицы на Studio через `clinical_kb.seed_clinical_kb()` (её же вызывает `init_db`).
2. Если вводишь новый constraint — обработай его в `_annotate` (form_note) и в секции «Ограничить»
   внутри `build_food_profile`, иначе он ни на что не повлияет.
3. Если это ген-ограничение, которое надо снимать при gain — добавь в `_SUPPRESSED_WHEN_GAIN`.

## Добавить продукт
- В `STAPLES` — `{item, cat, tags, why}`. Теги — язык рамки (см. докстринг food_staples).
- Сезонные — в `seasonal_produce.py` + польза в `food_genome.FOOD_BENEFITS`/`_SEAFOOD_BENEFITS`.

## Проверить
Тест правил питания (`tests/unit/test_food_profile.py`, в закрытой части проекта) — обязательно проходит **флагман** (резекция ⇒ нет
«постное»). Живьём — только на Studio: `HEALTH_DATA_DIR=<каталог тенанта> /opt/homebrew/bin/python3.11 -c "import food_profile as fp; print(fp.medical_frame())"`.

## Мины
- Новый constraint без обработки в `_annotate`/limit → «мёртвый» флаг (ничего не меняет). Проверь оба.
- Медкарта ДОЛЖНА перебивать геном: если добавляешь ген-ограничение калорий/жира — внеси в suppress-набор.
- Не бери слабые SNP как факт (честность): только установленные связи.
