"""
tests/unit/test_misc_utils_characterization.py — пины оставшихся утилит health_db
(Поток B добор, 2026-06-27): consultation_sessions, import_status,
medication-proposals, workouts, absolute_thresholds, constraints, context_events.

behavior-preserving пины: «как есть» ≠ «как правильно».
"""
from __future__ import annotations

import health_db


def test_save_load_consultation_session(db):
    health_db.save_consultation_session(123, {"a": 1})
    assert health_db.load_consultation_session(123) == {"a": 1}


def test_load_consultation_session_missing_none(db):
    assert health_db.load_consultation_session(999) is None


def test_set_import_status_then_not_stale(db):
    health_db.set_import_status("oura")
    assert health_db.get_import_staleness("oura")[0] is False


def test_get_import_staleness_missing_is_fresh(db):
    assert health_db.get_import_staleness("nope")[0] is False


def test_get_proposed_and_set_confirmation(db):
    health_db.upsert_medication("ProposedX")
    proposed = health_db.get_proposed_medications()
    assert "ProposedX" in [m["name"] for m in proposed]
    mid = [m for m in proposed if m["name"] == "ProposedX"][0]["id"]
    health_db.set_medication_confirmation(mid, "confirmed")
    assert "ProposedX" not in [m["name"] for m in health_db.get_proposed_medications()]


def test_upsert_workout_dedups_by_date_start_time(db):
    # ИСПРАВЛЕНО 2026-06-28 (был footgun): добавлен UNIQUE(date,start_time)
    # (_migrate_workouts_dedup + idx_workouts_unique в фикстуре), теперь
    # ON CONFLICT DO NOTHING реально дедуплицирует. Раньше был холостым → дубли.
    w = {"activity_type": "run", "start_time": "08:00"}
    health_db.upsert_workout("2026-05-01", w)
    health_db.upsert_workout("2026-05-01", w)
    assert db.count("workouts", "date=?", ("2026-05-01",)) == 1


def test_get_absolute_thresholds(db):
    db.execute(
        "INSERT INTO absolute_thresholds (metric, direction, value, reason_template, source, active) "
        "VALUES (?,?,?,?,?,1)", ("hrv", "low", 17, "tpl", "manual"))
    assert any(r["metric"] == "hrv" for r in health_db.get_absolute_thresholds())


def test_get_active_constraints_reads_alerts(db):
    health_db.save_alert("allergy", "penicillin")
    assert any(c.get("message") == "penicillin" for c in health_db.get_active_constraints())


def test_save_context_event_returns_id(db):
    rid = health_db.save_context_event("2026-05-01", "manual", "mood", value_num=5)
    assert isinstance(rid, int)
