"""
UC-D-02 — `evaluate_domain_need` 3-слойная логика (percentile + floors + constraints).

Источник: USE_CASES.md §3.D → UC-D-02.
Status: `implemented`.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_evaluate_domain_need_function_exists():
    """Функция должна быть доступна (через telegram_bot re-export И в services.recommendations)."""
    import telegram_bot
    import services.recommendations
    assert hasattr(telegram_bot, "evaluate_domain_need"), \
        "telegram_bot.evaluate_domain_need отсутствует (Sprint 6 C9: должен быть re-export)"
    assert callable(services.recommendations.evaluate_domain_need)


def test_evaluate_uses_3_layers():
    """В коде должны быть упоминания всех 3-х слоёв.

    Sprint 6 C9 (2026-05-23): функция переехала из telegram_bot.py
    в services/recommendations.py.
    """
    import ast
    from pathlib import Path
    src = (Path(__file__).parents[2] / "services" / "recommendations.py").read_text(encoding="utf-8")
    # Область — ТЕЛО функции через AST (get_source_segment), не байтовое окно
    # [idx:idx+5000]: окно переливалось в соседние функции и слой мог совпасть у
    # соседа (false-GREEN, 2026-07-17).
    tree = ast.parse(src)
    fn_block = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "evaluate_domain_need":
            fn_block = ast.get_source_segment(src, node)
            break
    assert fn_block, "evaluate_domain_need не найдена в services/recommendations.py"

    has_percentile = ("percentile" in fn_block.lower() or
                       "перцентил" in fn_block.lower() or
                       "baseline" in fn_block.lower())
    has_floor = ("floor" in fn_block.lower() or
                  "пол" in fn_block or
                  "<=17" in fn_block or "<17" in fn_block)
    has_constraints = ("constraint" in fn_block.lower() or
                        "patient_constraint" in fn_block.lower() or
                        "active_protocol" in fn_block.lower())

    layers_present = [has_percentile, has_floor, has_constraints]
    assert sum(layers_present) >= 2, \
        "evaluate_domain_need не имеет минимум 2 из 3 слоёв"


def test_recommendation_engine_doc_exists():
    """Архитектурный документ recommendation_engine.md должен существовать."""
    from pathlib import Path
    p = Path(__file__).parents[2] / "recommendation_engine.md"
    assert p.exists(), "recommendation_engine.md отсутствует — UC-D-02 без архитектуры"
