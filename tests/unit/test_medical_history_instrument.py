"""Опросник medical_history (решение владельца 27.09): путь задачи → профиль, без lab_results."""
import pytest

pytestmark = pytest.mark.unit


def test_task_to_profile_roundtrip(db, monkeypatch, tmp_path):
    import assessment_dialog as ad
    import health_db as hdb
    monkeypatch.setattr(ad, "INSTRUMENTS_DIR", tmp_path / "tenant_instruments")
    tid = hdb.save_task(source="t", type_="assessment", content="x", priority="medium")
    with db.conn() as c:
        c.execute("UPDATE tasks SET fingerprint='assessment:medical_history' WHERE id=?", (tid,))
    text, kb, sid = ad.start(7, tid)
    assert sid and "аллерги" in text.lower()
    ad.answer(sid, "allergies", "пенициллин")
    reply, _, done = ad.answer(sid, "other_conditions", "нет")
    assert done
    assert hdb.get_patient_profile()["medical.allergies"] == "пенициллин"
    assert db.fetchall("SELECT 1 FROM lab_results WHERE source LIKE 'instrument:%'") == []
