"""assessment_dialog — state machine + finalize (SX-17h)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _setup_instrument(tmp_path, monkeypatch):
    cat = tmp_path / "instruments"
    cat.mkdir()
    (cat / "test_inst.json").write_text(json.dumps({
        "id": "test_inst",
        "name": "Test",
        "wording_version_hash": "test_v1",
        "cadence_days": 30,
        "items": [
            {"id": "q1", "text": "Q1?", "subscale": "s1"},
            {"id": "q2", "text": "Q2?", "subscale": "s1"},
        ],
        "subscales": [{"id": "s1", "items": ["q1", "q2"],
                        "direction": "symptom_higher_worse"}],
        "response_scale": {"min": 1, "max": 4, "labels": {"1": "no", "4": "very"}},
        "scoring_formula": "linear",
    }, ensure_ascii=False))
    import assessment_dialog as ad
    import assessment_importer as ai
    monkeypatch.setattr(ad, "INSTRUMENTS_DIR", cat)
    # ответы — в каталог тенанта (фикстура db), а не в подменённую константу (2026-09-23)
    return ad, ai.assessments_dir()


def test_dialog_full_happy_path_creates_session_answers_finalizes(db, tmp_path, monkeypatch):
    ad, out_dir = _setup_instrument(tmp_path, monkeypatch)
    import health_db as hdb
    tid = hdb.save_task(
        source="assessment_scheduler", type_="assessment",
        content="fill test_inst", priority="medium",
    )
    db.execute("UPDATE tasks SET fingerprint=? WHERE id=?",
               ("assessment:test_inst", tid))
    text, kb, sid = ad.start(chat_id=42, task_id=tid)
    assert sid is not None
    assert "Q1" in text
    # Answer q1=2, q2=3
    text2, kb2, done2 = ad.answer(sid, "q1", 2)
    assert done2 is False
    text3, kb3, done3 = ad.answer(sid, "q2", 3)
    assert done3 is True
    # JSON saved
    jsons = list(out_dir.glob("*.json"))
    assert len(jsons) == 1
    data = json.loads(jsons[0].read_text())
    assert data["raw_responses"] == {"q1": 2, "q2": 3}


def test_dialog_resume_returns_active_session_for_chat(db, tmp_path, monkeypatch):
    ad, _ = _setup_instrument(tmp_path, monkeypatch)
    sid = ad.db.save_assessment_session(
        instrument_id="test_inst", wording_version_hash="test_v1",
        chat_id=99, task_id=None, answers_json='{"q1": 1}',
    )
    active = ad.get_active(chat_id=99)
    assert active is not None
    assert active["id"] == sid
