"""Конвенция короткого id опросника — один дом (assessment_importer.instrument_source_id).
Модуль EORTC с полным id в заполнении и коротким именем файла каталога импортируется
под коротким source. Краснеет, если импортёр перестанет пробовать короткое имя."""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.unit


def test_instrument_source_id_снимает_префикс_семейства():
    from assessment_importer import instrument_source_id
    assert instrument_source_id("eortc_qlq_zz99") == "zz99"
    assert instrument_source_id("isi") == "isi"


def test_полный_id_находит_каталог_под_коротким_именем(db, tmp_path, monkeypatch):
    import assessment_importer as ai
    cat = tmp_path / "instruments"
    cat.mkdir()
    (cat / "zz99.json").write_text(json.dumps({
        "id": "eortc_qlq_zz99", "wording_version_hash": "v1", "cadence_days": 30,
        "items": [{"id": "q1", "text": "?", "subscale": "s1"}],
        "subscales": [{"id": "s1", "items": ["q1"], "direction": "symptom_higher_worse"}],
        "response_scale": {"min": 1, "max": 4, "labels": {}},
    }))
    monkeypatch.setattr(ai, "INSTRUMENTS_DIR", cat)
    p = tmp_path / "filled.json"
    p.write_text(json.dumps({"instrument_id": "eortc_qlq_zz99", "wording_version_hash": "v1",
                             "assessment_date": "2026-05-18", "raw_responses": {"q1": 4}}))
    assert ai.import_file(p) is True
    rows = db.fetchall("SELECT test_name FROM lab_results WHERE source='instrument:zz99'")
    assert [r["test_name"] for r in rows] == ["zz99_s1"]
