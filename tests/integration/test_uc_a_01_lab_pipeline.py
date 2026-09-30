"""
UC-A-01 — Лаб PDF/JPEG → распознавание → подтверждение → запись в БД.

Источник: USE_CASES.md §3.A → UC-A-01.
Status: `partial` (Council автозапуск отложен в backlog).

Этот UC — самый сложный e2e в системе. Здесь — smoke на ключевые пайплайн-точки:
1. classify() распознаёт лаб-документы.
2. lab_extractor (если есть) интегрируется.
3. NOT-Then: Council не запускается автоматически.
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


def test_lab_extractor_module_imports_or_skip():
    """lab_extractor.py может ещё не существовать (backlog)."""
    try:
        import lab_extractor
        assert lab_extractor is not None
    except ImportError:
        pytest.skip("lab_extractor.py ещё не реализован — backlog UC-A-01")


def test_council_not_triggered_after_pending_create(pending_labs_dir, db,
                                                       make_pending_json):
    """
    NOT-Then UC-A-01: создание pending_labs/ файла не должно само по себе
    запускать Council. (Council только по расписанию или ручной /consult).
    """
    import json
    visit_key = "2026-05-01_clinic"
    payload = make_pending_json(visit_key)
    (pending_labs_dir / f"{visit_key}.json").write_text(json.dumps(payload))

    # agent_reports должен оставаться пустым (Council ещё не запущен)
    count = db.count("agent_reports", "agent_type='gp' OR agent_name LIKE '%Council%'")
    assert count == 0, "Council/GP-отчёт появился после tentative-создания"


def test_set_import_status_called_after_lab_import(db):
    """
    После успешного импорта `set_import_status("oncology_pdf")` должен
    обновиться. Smoke: функция существует.
    """
    import health_db
    assert hasattr(health_db, "set_import_status")
