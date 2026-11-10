"""
UC-A-01 — Лаб PDF/JPEG → распознавание → подтверждение → запись в БД.

Источник: USE_CASES.md §3.A → UC-A-01.
Status: `partial` (Council автозапуск отложен в backlog).

Этот UC — самый сложный e2e в системе. Здесь — smoke на ключевые пайплайн-точки:
1. classify() распознаёт лаб-документы.
2. set_import_status существует.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_classify_lab_signal():
    """Документ с явным lab-маркером в имени → 'lab'."""
    from import_all import classify
    cls = classify(Path("/Users/zz/health/CR/lab eng 2026-05-01.pdf"), "")
    assert cls == "lab"


def test_classify_lab_by_text_content():
    """Имя без lab-маркера, но текст содержит laboratory tests."""
    from import_all import classify
    cls = classify(Path("/Users/zz/health/CR/x.pdf"), "Laboratory Tests")
    assert cls == "lab"


def test_set_import_status_called_after_lab_import(db):
    """
    После успешного импорта `set_import_status("oncology_pdf")` должен
    обновиться. Smoke: функция существует.
    """
    import health_db
    assert hasattr(health_db, "set_import_status")
