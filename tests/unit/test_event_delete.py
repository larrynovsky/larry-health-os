"""Удаление ошибочного события медкарты (фидбэк с GitHub 2026-10-18).

Создать событие было можно, убрать — нет: неверный приём жил в медкарте и в контексте
моделей навсегда. Оракулы: событие и его подтаблицы исчезают, старая строка — в журнале;
событие, на которое ссылается лекарство, не удаляется (иначе ссылка оборвётся молча).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _encounter(db_mod):
    return db_mod.save_event(event_type="encounter", effective_date="2026-10-27",
                             status="planned", recorded_by="dashboard",
                             encounter={"class": "outpatient", "reason_text": "Повторная консультация"})


def test_delete_removes_event_and_subtable_and_logs(db):
    import events_db, health_db
    eid = _encounter(health_db)
    old = events_db.delete_event(eid, reason="тест")
    assert old["effective_date"] == "2026-10-27"
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM events WHERE id=?", (eid,)).fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM encounters WHERE event_id=?", (eid,)).fetchone()[0] == 0
        logged = c.execute("SELECT old_value FROM data_repair_log WHERE home='sqlite:events' "
                           "AND entity=?", (str(eid),)).fetchone()
    # подтаблица тоже в журнале: смысл приёма (причина) живёт в encounters, не в events
    import json
    snap = json.loads(json.loads(logged[0]))      # log_repair кодирует строку ещё раз
    assert snap["event"]["effective_date"] == "2026-10-27"
    assert snap["encounters"]["reason_text"] == "Повторная консультация"


def test_delete_refuses_when_medication_points_to_event(db):
    import events_db, health_db
    eid = _encounter(health_db)
    with db.conn() as c:
        c.execute("INSERT INTO medications (name, prescribing_event_id) VALUES ('X', ?)", (eid,))
        c.commit()
    with pytest.raises(ValueError, match="лекарства"):
        events_db.delete_event(eid, reason="тест")
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM events WHERE id=?", (eid,)).fetchone()[0] == 1


def test_delete_missing_event_is_lookup_error(db):
    import events_db
    with pytest.raises(LookupError):
        events_db.delete_event(999999, reason="тест")


def test_endpoint_shows_refusal_instead_of_silent_4xx(db, monkeypatch):
    import events_db
    from dashboard_routers import api_events

    def boom(*a, **k):
        raise ValueError("на событие ссылаются: лекарства — 1")
    monkeypatch.setattr(events_db, "delete_event", boom)
    r = api_events.api_event_delete(5)
    assert r.status_code == 200 and r.headers["HX-Retarget"] == "#evmsg-5"
    assert "лекарства" in r.body.decode()
