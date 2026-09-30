"""
tests/fixtures/pending_labs.py — tmp-директория для tentative-записей лаб-данных.

`lab_extractor.py` сохраняет извлечённый JSON в `~/iCloud/health/data/pending_labs/`
до подтверждения пользователем (UC-A-01 шаги 3-4). Fixture создаёт tmp-директорию
для тестов tentative → committed.

Использование:

    def test_tentative_to_committed(pending_labs_dir, db):
        # Создаём tentative-запись
        json_path = pending_labs_dir / "2026-05-01_clinic.json"
        json_path.write_text(json.dumps({
            "visit_key": "2026-05-01_clinic",
            "items": [{"name": "WBC", "value": 4.2, ...}]
        }))
        # Перед approval — нет в lab_results
        assert db.count("lab_results") == 0
        # ... approve → import_from_pending(json_path) ...
        assert db.count("lab_results") == 1
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def make_pending_lab_json(visit_key: str, items: list[dict] | None = None,
                          confidence: str = "high",
                          source: str = "clinic") -> dict:
    """Helper: построить JSON в формате lab_extractor output."""
    if items is None:
        items = [
            {"name": "WBC", "value": 4.2, "unit": "x10^9/L",
             "ref_low": 4.0, "ref_high": 10.0,
             "flagged": False, "confidence": confidence,
             "raw_line": "WBC 4.2 (4.0-10.0)"},
        ]
    return {
        "visit_key": visit_key,
        "source": source,
        "extracted_at": "2026-05-01T12:00:00",
        "items": items,
    }


@pytest.fixture
def pending_labs_dir(tmp_path: Path) -> Path:
    """tmp-директория для tentative pending_labs JSON."""
    d = tmp_path / "pending_labs"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def make_pending_json():
    """Helper-фикстура — возвращает функцию для построения JSON."""
    return make_pending_lab_json
