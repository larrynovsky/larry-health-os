"""
UC-I-01 — Наружный язык всегда русский (alias `UC-LANG-001`).

Источник: USE_CASES.md §4.I → UC-I-01.
Реализация: `hai_core.FORMAT_RULES` + system_prompt + неявная защита через
русский system prompt. Status: `partial` (явного правила «only Russian» в
FORMAT_RULES пока нет).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


# ── Detector (живёт в тестах, может быть вынесен в utils позже) ─────────────


# CJK Unicode ranges:
# 0x3040-0x309F — хирагана
# 0x30A0-0x30FF — катакана
# 0x3400-0x4DBF — CJK Extension A
# 0x4E00-0x9FFF — CJK Unified Ideographs
_CJK_RUN = re.compile(r"[぀-ヿ㐀-䶿一-鿿]{3,}")


def has_cjk_leakage(text: str, threshold: int = 3) -> bool:
    """True если в тексте подряд ≥threshold CJK-символов."""
    if threshold == 3:
        return bool(_CJK_RUN.search(text))
    pattern = re.compile(rf"[぀-ヿ㐀-䶿一-鿿]{{{threshold},}}")
    return bool(pattern.search(text))


# ── B (negative): «нормальный» русский текст не триггерит детектор ──────────


def test_pure_russian_no_leakage():
    text = ("Утренний отчёт. ВСР за вчера 22 мс — в норме недели. "
            "Глубокий сон 45 минут.")
    assert has_cjk_leakage(text) is False


def test_russian_with_english_terms_no_leakage():
    text = "ВСР (HRV) за неделю 22 мс. Анализ CEA и CA19-9 в норме."
    assert has_cjk_leakage(text) is False


def test_russian_with_rsid_no_leakage():
    text = "Вариант rs4680 (COMT, GG): высокий клиренс дофамина."
    assert has_cjk_leakage(text) is False


def test_russian_with_units_no_leakage():
    text = "Глюкоза 5.5 mmol/L, креатинин 88 mg/dL — в норме."
    assert has_cjk_leakage(text) is False


def test_russian_with_emoji_safe():
    """Emoji не CJK — игнорируем."""
    text = "Утром: ВСР 22 мс, шаги 8000."
    assert has_cjk_leakage(text) is False


def test_empty_string_no_leakage():
    assert has_cjk_leakage("") is False


# ── B (positive): китайский/японский текст детектируется ────────────────────


def test_chinese_phrase_detected():
    """3 китайских символа подряд — leakage."""
    text = "ВСР 22 мс, 病人需要 (имя)"  # 病人需要 = "пациент нуждается"
    assert has_cjk_leakage(text) is True


def test_japanese_hiragana_detected():
    text = "Анализ показал ありがとう вместо результата."
    assert has_cjk_leakage(text) is True


def test_long_chinese_block_detected():
    text = "心血管疾病的风险评估和管理"  # длинный chunk
    assert has_cjk_leakage(text) is True


def test_single_cjk_char_below_threshold():
    """Один-два символа CJK (например в названии) — не leakage."""
    text = "Бренд: 安 (один знак). Описание: модель X."
    assert has_cjk_leakage(text, threshold=3) is False


# ── E (cross-check): FORMAT_RULES загружается ───────────────────────────────


def _read_hai_core_format_rules() -> str:
    """Извлекает строковый литерал FORMAT_RULES из hai_core.py через AST."""
    import ast
    src = (Path(__file__).parents[2] / "hai_core.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "FORMAT_RULES":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        return node.value.value
    return ""


def test_format_rules_exists_in_hai_core():
    """FORMAT_RULES определён в hai_core.py (или _build_system_prompt)."""
    src = (Path(__file__).parents[2] / "hai_core.py").read_text(encoding="utf-8")
    assert "FORMAT_RULES" in src, "FORMAT_RULES не определён в hai_core.py"


def test_format_rules_is_in_russian():
    """FORMAT_RULES написан по-русски (implicit-защита: русский промпт → русский ответ).

    Язык проверяем по ИЗВЛЕЧЁННОМУ значению FORMAT_RULES (AST-хелпер выше), не по
    байтовому окну `src[idx:idx+3000]`: окно захватывало соседний код/комменты и
    зеленело бы на де-русифицированном правиле (false-GREEN, 2026-07-17). Плотность
    кириллицы — прямой сигнал языка, устойчивее списка общих слов.
    """
    val = _read_hai_core_format_rules()
    assert val, "FORMAT_RULES не извлечён из hai_core.py (AST)"
    cyrillic = sum(1 for ch in val if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    assert cyrillic >= 100, (
        f"FORMAT_RULES слабо русифицирован: кириллических символов {cyrillic} (<100). "
        f"Правило должно быть на русском."
    )


def test_format_rules_explicit_only_russian():
    """
    Явное правило «отвечай только на русском» в FORMAT_RULES (W2A-3 фикс).
    UC-I-01 переведён partial → implemented.
    Если кто-то снимет правило — REGRESSION-сигнал.
    """
    src = (Path(__file__).parents[2] / "hai_core.py").read_text(encoding="utf-8")
    has_explicit = ("ОТВЕЧАЙ ТОЛЬКО НА РУССКОМ" in src or
                    "только на русском" in src.lower())
    assert has_explicit, (
        "Явное правило `only Russian` в FORMAT_RULES должно быть "
        "(UC-I-01 implemented после W2A-3)"
    )


def test_format_rules_allows_technical_inserts():
    """В правиле явно перечислены допустимые вставки (rsID, units, аббревиатуры).
    Без этого LLM может пытаться переводить технические термины."""
    src = (Path(__file__).parents[2] / "hai_core.py").read_text(encoding="utf-8")
    # Должны быть упомянуты конкретные exception-патерны
    markers = ["rsID", "rs4680", "mg/dL", "HRV", "PET-CT"]
    found = [m for m in markers if m in src]
    assert len(found) >= 3, (
        f"FORMAT_RULES должен явно разрешать минимум 3 технические вставки. "
        f"Found: {found}"
    )
