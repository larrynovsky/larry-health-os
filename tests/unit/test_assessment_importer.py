"""assessment_importer — JSON → lab_results subscale scores (SX-17i)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _setup(tmp_path, monkeypatch):
    cat = tmp_path / "instruments"
    cat.mkdir()
    inst = {
        "id": "test_inst", "name": "Test", "wording_version_hash": "v1",
        "cadence_days": 30,
        "items": [{"id": "q1", "text": "?", "subscale": "s1"},
                   {"id": "q2", "text": "?", "subscale": "s1"}],
        "subscales": [{"id": "s1", "items": ["q1", "q2"],
                        "direction": "symptom_higher_worse"}],
        "response_scale": {"min": 1, "max": 4, "labels": {}},
    }
    (cat / "test_inst.json").write_text(json.dumps(inst))
    import assessment_importer as ai
    monkeypatch.setattr(ai, "INSTRUMENTS_DIR", cat)
    return ai, cat


def test_importer_parses_and_saves_subscale_scores(db, tmp_path, monkeypatch):
    ai, _ = _setup(tmp_path, monkeypatch)
    payload = {
        "instrument_id": "test_inst",
        "wording_version_hash": "v1",
        "assessment_date": "2026-05-18",
        "raw_responses": {"q1": 1, "q2": 4},
    }
    p = tmp_path / "filled.json"
    p.write_text(json.dumps(payload))
    ok = ai.import_file(p)
    assert ok is True
    rows = db.fetchall("SELECT test_name, value FROM lab_results "
                       "WHERE source='instrument:test_inst'")
    # mean=2.5, (2.5-1)/3*100 = 50.0
    assert len(rows) == 1
    assert rows[0]["test_name"] == "test_inst_s1"
    assert abs(rows[0]["value"] - 50.0) < 0.01


def test_importer_warns_on_wording_hash_mismatch(db, tmp_path, monkeypatch, caplog):
    ai, _ = _setup(tmp_path, monkeypatch)
    payload = {
        "instrument_id": "test_inst", "wording_version_hash": "WRONG_HASH",
        "assessment_date": "2026-05-18",
        "raw_responses": {"q1": 2, "q2": 2},
    }
    p = tmp_path / "filled.json"
    p.write_text(json.dumps(payload))
    import logging
    with caplog.at_level(logging.WARNING):
        ok = ai.import_file(p)
    assert ok is True
    assert any("wording_version_hash mismatch" in r.message for r in caplog.records)



def test_importer_creates_self_observation_event_with_diagnostic(db, tmp_path, monkeypatch):
    """SX-15: PRO-импорт создаёт events(self_observation) + diagnostic_event
    с raw_values_ref на созданные lab_results."""
    ai, _ = _setup(tmp_path, monkeypatch)
    payload = {
        "instrument_id": "test_inst",
        "wording_version_hash": "v1",
        "assessment_date": "2026-05-18",
        "raw_responses": {"q1": 2, "q2": 3},
    }
    p = tmp_path / "filled.json"
    p.write_text(json.dumps(payload))
    ok = ai.import_file(p)
    assert ok is True

    # 1. event(self_observation) создан
    evt_rows = db.fetchall(
        "SELECT id, event_type, effective_date, performer, performer_role "
        "FROM events WHERE event_type='self_observation' "
        "AND effective_date='2026-05-18'"
    )
    assert len(evt_rows) == 1
    evt = evt_rows[0]
    assert evt["performer"] == "self"
    assert evt["performer_role"] == "self"

    # 2. diagnostic_event прицеплен к этому event_id, modality = instrument_id
    diag_rows = db.fetchall(
        "SELECT type, modality, raw_values_ref FROM diagnostic_events "
        "WHERE event_id=?", (evt["id"],)
    )
    assert len(diag_rows) == 1
    diag = diag_rows[0]
    assert diag["type"] == "functional_test"
    assert diag["modality"] == "test_inst"

    # 3. raw_values_ref — JSON-массив с id'ами созданных lab_results
    refs = json.loads(diag["raw_values_ref"])
    assert isinstance(refs, list)
    assert len(refs) >= 1
    # Проверим что эти id реально существуют в lab_results
    lab_rows = db.fetchall(
        f"SELECT id FROM lab_results WHERE id IN ({','.join('?' * len(refs))})",
        tuple(refs)
    )
    assert len(lab_rows) == len(refs)


def test_zero_rows_is_failure_and_task_stays_open(db, tmp_path, monkeypatch):
    """BL-SILENT-0ROWS-1: ответы пришли под ключами, которых нет в items каталога
    (майский случай `qN_suffix`) — импортёр раньше возвращал успех и закрывал задачу."""
    ai, _ = _setup(tmp_path, monkeypatch)
    resolved = []
    monkeypatch.setattr(ai.db, "resolve_task", lambda *a, **k: resolved.append(a))
    payload = {"instrument_id": "test_inst", "wording_version_hash": "v1",
               "assessment_date": "2026-05-18", "task_id": 7,
               "raw_responses": {"q1_suffix": 2, "q2_suffix": 3}}
    p = tmp_path / "filled.json"
    p.write_text(json.dumps(payload))
    assert ai.import_file(p) is False
    assert resolved == []
    assert db.fetchall("SELECT 1 FROM lab_results WHERE source='instrument:test_inst'") == []
