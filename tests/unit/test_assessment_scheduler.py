"""assessment_scheduler — idempotency + создание задач (SX-17g)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _setup_instrument_catalog(tmp_path, monkeypatch):
    """Создать каталог инструментов в tmp и подменить путь в модуле."""
    cat = tmp_path / "instruments"
    cat.mkdir()
    (cat / "isi.json").write_text(json.dumps({
        "id": "isi", "name": "ISI", "cadence_days": 90, "cadence_offset_days": 0,
        "items": [{"id": "q1", "text": "?", "subscale": "total"}],
    }, ensure_ascii=False))
    import assessment_scheduler as sched
    monkeypatch.setattr(sched, "INSTRUMENTS_DIR", cat)
    return sched


def test_scheduler_creates_task_when_no_filling(db, tmp_path, monkeypatch):
    sched = _setup_instrument_catalog(tmp_path, monkeypatch)
    created = sched.run()
    assert len(created) == 1
    assert created[0]["instrument"] == "isi"
    # Подтвердим в БД
    rows = db.fetchall("SELECT fingerprint, content FROM tasks WHERE source='assessment_scheduler'")
    assert any(r["fingerprint"] == "assessment:isi" for r in rows)
    assert "Вопросы о бессоннице" in rows[0]["content"]
    assert "за последнюю неделю" in rows[0]["content"]
    assert "1 вопрос" in rows[0]["content"]
    assert "recall" not in rows[0]["content"] and "ISI" not in rows[0]["content"]


def test_scheduler_is_idempotent_no_duplicate(db, tmp_path, monkeypatch):
    sched = _setup_instrument_catalog(tmp_path, monkeypatch)
    first = sched.run()
    second = sched.run()
    assert len(first) == 1
    assert len(second) == 0  # idempotent: open task → skip
