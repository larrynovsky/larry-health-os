"""
T-4.1 — Self-consistency check для lab_extractor (двойной прогон).

Источник: USE_CASES.md UC-A-01 + ROADMAP T-4.1.

Идея (вместо snapshot для LLM): запускаем `lab_extractor.extract_labs()` дважды
на одном PDF и сравниваем результаты. Расхождения в ключевых полях =
нестабильность extractor-а.

Status: skipped — `lab_extractor.py` ещё не реализован (UC-A-01 partial,
        Council-автозапуск отложен в backlog).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.snapshot


def test_lab_extractor_module_available_or_skip():
    try:
        import lab_extractor  # noqa: F401
    except ImportError:
        pytest.skip("lab_extractor.py не реализован — backlog UC-A-01")


def test_double_run_consistency_for_clinic_pdf_skipped():
    """
    Когда lab_extractor реализуется, тест:
    1. Запускает extract_labs(pdf) дважды.
    2. Сравнивает {name, value, unit, flagged} бит-в-бит.
    3. raw_line может отличаться (OCR-шум допустим).
    """
    pytest.skip("lab_extractor.py отсутствует")


def test_double_run_consistency_for_healthfund_pdf_skipped():
    pytest.skip("lab_extractor.py отсутствует")
