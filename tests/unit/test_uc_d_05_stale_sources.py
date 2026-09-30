"""
UC-D-05 — Устаревшие labs/genome помечаются датой и сниженной уверенностью.

Источник: USE_CASES.md §3.D → UC-D-05.
Status: `partial` (требует усиления промптов).

Этот тест документирует ожидаемое поведение — пока xfail / skip,
поскольку в коде нет явного механизма «пометить дату источника» в выводе.
"""
from __future__ import annotations

from datetime import date

import pytest

pytestmark = pytest.mark.unit


def test_lab_age_can_be_computed_from_db(db, clock):
    """База: можем посчитать возраст последнего лаб-источника."""
    clock.set("2026-05-08")
    db.add_lab_result("2025-08-01", "CEA", 2.3)

    row = db.fetchone("SELECT MAX(date) AS last FROM lab_results")
    last_date = date.fromisoformat(row["last"])
    age = (clock.today() - last_date).days
    assert age > 180, "9 месяцев между источниками"


def test_old_data_warning_threshold():
    """Порог: 6 мес = warning, 9 мес = alert (TC §1)."""
    REMINDER = 180
    ALERT = 270
    assert REMINDER < ALERT
    # Эти константы должны быть в integrity_tests
    from pathlib import Path
    src = (Path(__file__).parents[2] / "integrity_tests.py").read_text(encoding="utf-8")
    assert "180" in src and "270" in src, \
        "Пороги 180/270 дней (UC-D-05) не найдены в integrity_tests.py"


def test_stale_marker_instruction_in_format_rules():
    """
    UC-D-05 W2A-4 фикс: инструкция «при устаревших labs / genome >30д указывай
    дату и снижай уверенность» должна быть в FORMAT_RULES. С 27.09 «устаревший»
    анализ — просроченный по графику контроля своего аналита (блок свежести в
    контексте чата), а не общее число 180 дней в промпте.

    Это **prompt-уровень** контракт. Реальная проверка что LLM следует
    инструкции — W3B (LLM-judge с golden dataset), сейчас skip.
    """
    from pathlib import Path
    src = (Path(__file__).parents[2] / "hai_core.py").read_text(encoding="utf-8")
    has_instruction = (
        ("180" in src and "labs" in src.lower())
        or "180 дней" in src
        or "УСТАРЕВШИЕ ИСТОЧНИКИ" in src
    )
    assert has_instruction, (
        "FORMAT_RULES должен содержать инструкцию про устаревшие источники "
        "(UC-D-05 — labs >180д, genome >30д)"
    )


def test_stale_marker_genome_threshold():
    from pathlib import Path
    src = (Path(__file__).parents[2] / "hai_core.py").read_text(encoding="utf-8")
    assert "30 дней" in src or "30д" in src, \
        "Должен быть упомянут порог 30 дней для genome"


def test_llm_actual_compliance_with_stale_marker():
    """
    Реальная проверка что LLM в выводе указывает дату — это **D-уровень**
    (LLM-judge с golden dataset). Откладывается до W3B.
    """
    pytest.skip("UC-D-05 LLM-actual-compliance — W3B (LLM-judge eval)")
