# Тест-план UC-I-01 — Наружный язык всегда русский

**Источник:** USE_CASES.md §4.I → UC-I-01 (alias `UC-LANG-001`).
**Status:** `partial` · **Confirmation:** `confirmed`
**Type:** `check` · **Oracle:** `B + E`
**Owner:** `hai_core.FORMAT_RULES`, `wellally_consult.py`, `telegram_bot.py`

---

## Что проверяем

Все наружные выходы (Telegram, отчёты, ошибки) — на русском. Внутри допустимы:
- specialist-промпты на китайском (`.claude/specialists/*.md`);
- Английские медицинские термины как **вставки** (rsID, PET-CT, mg/dL, HRV, имена моделей).

**Главный инвариант:** в наружном тексте не должно быть ≥3 подряд CJK-символов.
Это эвристика: единичные иероглифы могут попасть в имя/код, но связный
китайский фрагмент = leakage.

---

## Стратегия теста

**Уровень:** `unit` через локальный CJK-detector.

**Реализация detector в тесте** (не пушим в production-код пока):

```python
def has_cjk_leakage(text: str, threshold: int = 3) -> bool:
    """True если в тексте есть подряд >=threshold CJK-символов."""
    import re
    # CJK ranges: 0x4E00-0x9FFF (общие), 0x3400-0x4DBF (extension A),
    # 0x3040-0x309F (хирагана), 0x30A0-0x30FF (катакана)
    pattern = re.compile(rf"[一-鿿぀-ゟ゠-ヿ]{{{threshold},}}")
    return bool(pattern.search(text))
```

**Оракулы:**
- **B (negative):** русский текст с rsID/PET-CT/mg/dL → `has_cjk_leakage = False`.
- **E (cross-check):** текст с китайским фрагментом → `True`.

**Дополнительно (E):** проверяем, что `hai_core.FORMAT_RULES` упоминает «русский»
или явно блокирует CJK на уровне промпта. Если правило слабое — фиксируем
как «известный partial» в notes.

---

## Структура теста

```python
# Detector
def test_pure_russian_no_leakage():
def test_russian_with_english_terms_no_leakage():
def test_russian_with_rsid_no_leakage():
def test_russian_with_units_no_leakage():
def test_chinese_phrase_detected():
def test_japanese_hiragana_detected():
def test_single_cjk_char_below_threshold():

# FORMAT_RULES
def test_format_rules_is_in_russian():
def test_format_rules_loaded_in_system_prompt():
```

---

## Acceptance

9 тестов зелёные. Если кто-то поменяет FORMAT_RULES на английский → тест краснеет.

## Что не покрыто

- Не запускаем реальный Council с китайскими промптами и не проверяем что
  ответ на русском — это `e2e_mock` уровня (Фаза 3).
- Не делаем live-тест на ASR/OCR (могут возвращать смешанный язык).

## Risk если красный

`partial` → expected_gap для FORMAT_RULES (мы знаем что правило слабое).
REGRESSION только если detector сам сломался.
