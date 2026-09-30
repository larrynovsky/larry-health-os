[English](move_lexicon_to_db.en.md) · **Русский**

# How-to: тебя флажнул §9-датчик лексиконов

Датчик нашёл доменный список строк (`*_TERMS/_MARKERS/_FLAGS/_LEXICON/_KEYWORDS`) в коде.
По §9 осмысленные данные живут в БД, не в `.py`. Три пути — выбери по смыслу константы.

## Путь 1. Это данные пациента/клиника → вынести в system_config (по умолчанию)

Эталон — `memory_salience.SYMPTOM_TERMS` (уже вынесен). Повторяй паттерн:

1. В модуле оставь литерал как **fallback** и читай через аксессор:
   ```python
   import memory_config as _mc
   SYMPTOM_TERMS = ("боль", "тошнота", ...)          # код = fallback
   def symptom_terms():
       return _mc.get_lexicon("symptom_terms", SYMPTOM_TERMS)
   ```
   Все потребители зовут `symptom_terms()`, не константу напрямую.
2. Засей контур в БД (write-through, один раз на init):
   ```python
   memory_config.seed(lexicons={"symptom_terms": list(SYMPTOM_TERMS)}, params={})
   ```
3. Зарегистрируй вердикт в `lexicon_registry.REGISTERED`:
   ```python
   "memory_salience.SYMPTOM_TERMS": {
       "verdict": "db-backed", "oracle": "владелец",
       "note": "memory_lexicon.symptom_terms в system_config; код = fallback"},
   ```
Правка списка теперь = строка в БД/дашборде, не `git commit`.

## Путь 2. Это лингвистика/инфра/представление (не данные пациента) → structural

Маркеры отрицания, триггеры системного поведения, инфра-эвристики (`_NEG_MARKERS`,
`_DEV_CLONE_MARKERS`) остаются в коде — они не персональные (§9: структурно-семантические
сигналы живут в коде). Зарегистрируй как `structural`:
```python
"memory_salience._NEG_MARKERS": {
    "verdict": "structural", "oracle": "инженер",
    "note": "маркеры отрицания — лингвистика, не клинический список"},
```
Не злоупотребляй: `structural` — это «не про пациента и не про методологию». Если сомневаешься,
это Путь 1.

## Путь 3. Легаси, вынести сейчас нельзя → legacy (счёт заморожен)

Терпим существующий список, но он **не растёт** (ратчет-по-вхождениям):
```python
"module.OLD_TERMS": {"verdict": "legacy", "count": 12,   # текущее число элементов
    "oracle": "владелец", "note": "вынести в БД — task #NN"},
```
Добавишь термин → `count` вырастет → датчик заблокирует, пока не обновишь БД или число осознанно.

## Проверить локально
```bash
python3.11 lexicon_registry.py          # печатает скан + находки, exit 1 если есть
python3.11 -m pytest tests/unit/test_lexicon_registry.py -q
```

## Почему так (короткая ссылка)
Полное объяснение — `дизайн_датчик_§9_lexicon_2026-07-07.md` (iCloud): хардкод-список = вечно
стухшая реплика единственного источника (БД); датчик проверяет наличие write-through-контура,
не наличие дефолта. Реестр — единый дом благословлённых исключений (не разбросанные `# ok`).
