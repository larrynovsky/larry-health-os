[English](add_visual_domain.en.md) · **Русский**

# Как добавить домен visual-intake (данными, без кода)

> **Тип документа:** How-to (Diátaxis). Замысел — в
> [explanation/symptom_intake_hypothesis](../explanation/symptom_intake_hypothesis.md).
>
> Проверяемая цена обобщения: новый домен (зубы, ногти, глаз) заводится СТРОКАМИ в
> `visual_domains`, а не правкой `.py`. Если пришлось трогать движок — обобщение неверно.

## Когда

Поток symptom-intake должен принимать новую область тела (сегодня засеяна одна демо-строка).
Красных флагов и клинических порогов здесь НЕТ (осознанно — см. explanation, раздел «почему
безопасность не детекция опасного»): домен несёт только регионы и параметры диалога.

## Шаги

1. **Реши, что за домен.** Имя латиницей-слагом (`dental`, `nails`, `hair`, `eye`). Оно —
   значение в БД, в коде его быть не должно.

2. **Засей строку** через `visual_db.upsert_visual_domain`:

   ```python
   import visual_db
   visual_db.upsert_visual_domain(
       "dental",
       region_options=["зуб", "десна", "язык", "губа"],
       dialog_params={"min_candidates": 3},   # переопределяет DIFF_MIN_CANDIDATES при желании
   )
   ```

   Запускать на Studio (primary): `ssh <studio_ssh> "cd ~/health_scripts &&
   /opt/homebrew/bin/python3.11 -c '...'"`. На не-primary запись упадёт (§8).

3. **Проверь §9-инвариант.** Имя домена НЕ должно появиться в коде:

   ```bash
   grep -rn "dental" symptom_intake.py handlers/symptom.py specialists/symptom_intake_system.txt
   # должно быть пусто
   ```

   Если хочется — добавь имя болезни-сентинел этого домена в `diagnosis_guard.SITES`
   (сперва убедись, что его нет в живых файлах, иначе страж упадёт).

4. **Ничего в движке не меняй.** `symptom_intake.step`, промпт, роутинг и сенсоры —
   домен-агностичны. Если тянет добавить `if domain == "dental"` — это нарушение §9;
   различие должно жить в `dialog_params`/`region_options`, не в ветке кода.

## Как проверить, что домен «взлетел»

- `visual_db.get_visual_domain("dental")` возвращает засеянные регионы/параметры.
- Прогони elicitation на выдуманном кейсе этого домена (mock `symptom_intake._call_model`):
  диалог собирает контур и уходит в `needs_specialist` БЕЗ правок кода.
- `python -m` прогон сенсора `check_symptom_prompt_discipline` — зелёный (§9 не нарушен).

## Чего НЕ делать

- Не хардкодить регионы/пороги домена в `.py` — это замаскированный §9-хардкод.
- Не добавлять красные флаги / сортировку срочности (см. explanation — это отброшено осознанно).
- Не заводить домен на не-primary хосте (запись в БД упадёт, и правильно).
