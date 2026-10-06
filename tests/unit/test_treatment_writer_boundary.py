"""Граница писателя medications (нить treatment-homes, 04.10.2026).

Замер, из которого выросло: в каноне владельца два режима со status='ongoing'
(вне словаря active|completed|discontinued: consult_prep их не видел, get_medications видел)
и режим, «начатый» датой визита, которую экстрактор подставил вместо даты начала.
Каждый тест красный на коде до нити.
"""
import json
import sqlite3

import pytest

import health_db
import treatment_extractor


def test_ongoing_от_модели_ложится_в_словарь(db):
    health_db.upsert_medication("REG-A", status="ongoing")
    row = health_db.get_medications(include_proposed=True)[0]
    assert row["status"] == "active"


def test_статус_вне_словаря_не_пишется(db):
    with pytest.raises(ValueError):
        health_db.upsert_medication("REG-A", status="maybe")
    assert health_db.get_medications(include_proposed=True) == []


def test_триггер_держит_словарь_мимо_писателя(db):
    health_db._migrate_medications_treatment()
    with health_db.get_conn() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO medications (name, status) VALUES ('REG-B', 'ongoing')")
        conn.execute("INSERT INTO medications (name, status) VALUES ('REG-B', 'completed')")


def test_дата_документа_не_становится_началом_лечения(db, anthropic_mock):
    anthropic_mock.script(
        match="ДОКУМЕНТ",
        response=json.dumps([{"regimen": "REG-A", "modality": "chemo", "cycles_completed": 9,
                              "start_date": None, "end_date": None, "status": "completed"}]),
    )
    n = treatment_extractor.process_treatment(
        event_id=17, text="Пациент завершил 9 cycles of REG-A химиотерапии.",
        effective_date="2031-05-10")
    assert n == 1
    row = health_db.get_medications(include_proposed=True)[0]
    assert row["start_date"] is None, "дата документа не дата начала"
    assert row["prescribing_event_id"] == 17, "документ связан ссылкой"


def test_статус_модели_вне_словаря_не_роняет_документ(db, anthropic_mock):
    anthropic_mock.script(
        match="ДОКУМЕНТ",
        response=json.dumps([
            {"regimen": "REG-A", "modality": "chemo", "status": "paused"},
            {"regimen": "REG-B", "modality": "chemo", "status": "stopped"},
        ]),
    )
    n = treatment_extractor.process_treatment(
        event_id=5, text="Пациент получал REG-A и REG-B химиотерапии.", effective_date="2031-01-01")
    names = {r["name"]: r["status"] for r in health_db.get_medications(include_proposed=True)}
    assert n == 1 and names == {"REG-B": "discontinued"}


def test_ремонт_строки_лечения_откатывается_по_журналу(db):
    """Ремонт канона лечения пишется в data_repair_log; до нити revert_repairs отказывал
    на доме sqlite:medications — обратимость была бы заявленной, а не исполнимой."""
    health_db._ensure_data_repair_log()
    mid = health_db.upsert_medication("REG-A", status="active", confirmation="confirmed")
    with health_db.get_conn() as conn:
        health_db.log_repair(conn, run_id="r1", home="sqlite:medications", entity=mid,
                             field="status", old_value="active", new_value="completed",
                             reason="тест")
        conn.execute("UPDATE medications SET status='completed' WHERE id=?", (mid,))
    assert health_db.revert_repairs("r1") == 1
    assert health_db.get_medications()[0]["status"] == "active"
