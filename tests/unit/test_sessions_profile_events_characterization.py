"""
tests/unit/test_sessions_profile_events_characterization.py — характеризационные
пины доменов assessments, profile, events, hae, context_events
(Поток B рефакторинга, 2026-06-27).

behavior-preserving пины: «как есть» ≠ «как правильно».
"""
from __future__ import annotations

import health_db


# ── assessment sessions ──────────────────────────────────────────────────────

def test_save_get_assessment_session(db):
    sid = health_db.save_assessment_session("ISI", "hash1", chat_id=123)
    s = health_db.get_assessment_session(sid)
    assert s["instrument_id"] == "ISI"
    assert s["chat_id"] == 123


def test_get_active_assessment_session_finds_in_progress(db):
    sid = health_db.save_assessment_session("ISI", "h", chat_id=123)
    active = health_db.get_active_assessment_session(123)
    assert active is not None and active["id"] == sid


def test_get_active_assessment_session_none_for_unknown_chat(db):
    assert health_db.get_active_assessment_session(999) is None


def test_update_assessment_session_status(db):
    sid = health_db.save_assessment_session("ISI", "h", chat_id=1)
    health_db.update_assessment_session(sid, status="completed")
    assert health_db.get_assessment_session(sid)["status"] == "completed"


# ── profile ──────────────────────────────────────────────────────────────────

def test_get_patient_profile_returns_keyvalue(db):
    db.add_profile("blood_type", value_text="O+")
    assert health_db.get_patient_profile().get("blood_type") == "O+"


def test_get_patient_profile_category_filter(db):
    db.add_profile("k1", value_text="v1", category="medical")
    db.add_profile("k2", value_text="v2", category="lifestyle")
    med = health_db.get_patient_profile(category="medical")
    assert "k1" in med and "k2" not in med


def test_apply_stated_unknown_field_writes_nothing(db):
    # До 2026-09-23 здесь характеризовался update_profile_field: запись в profile_context.json,
    # без файла — тихий no-op. Это и был дефект (сказанное не доходило до профиля; нить
    # profile-home). Теперь: поле из methodology/profile_fields.yaml — в patient_profile,
    # незнакомый ключ — None и ни одной записи.
    assert health_db.apply_stated("k", "v", source="test") is None
    assert "k" not in health_db.get_patient_profile()


# ── events (медкарта) ────────────────────────────────────────────────────────

def test_get_events_returns_added_event(db):
    db.add_event(event_type="lab", effective_date="2026-05-01")
    assert "lab" in {e["event_type"] for e in health_db.get_events()}


def test_get_events_filter_by_type(db):
    db.add_event(event_type="lab")
    db.add_event(event_type="imaging")
    assert {e["event_type"] for e in health_db.get_events(event_type="lab")} == {"lab"}


# ── hae registry ─────────────────────────────────────────────────────────────

def test_upsert_hae_metric_then_in_registry(db):
    health_db.upsert_hae_metric("steps_variability", status="known", unit="count")
    reg = health_db.get_hae_registry()
    assert "steps_variability" in reg
    assert reg["steps_variability"]["unit"] == "count"
