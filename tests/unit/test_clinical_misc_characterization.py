"""
tests/unit/test_clinical_misc_characterization.py — характеризационные пины
мелких клинических доменов health_db (Поток B рефакторинга, 2026-06-27):
  consultations, alerts, episodes, medications.

behavior-preserving пины: «как есть» ≠ «как правильно».
"""
from __future__ import annotations

import health_db


# ── consultations ────────────────────────────────────────────────────────────

def test_save_consultation_then_get(db):
    health_db.save_consultation("2026-05-01", "cardiology")
    assert "cardiology" in [c["specialist_type"] for c in health_db.get_consultations()]


def test_get_last_consultation_most_recent_and_none(db):
    assert health_db.get_last_consultation() is None
    health_db.save_consultation("2026-05-01", "cardiology")
    health_db.save_consultation("2026-06-01", "neurology")
    assert health_db.get_last_consultation()["date"] == "2026-06-01"


def test_get_consultations_filter_by_specialist(db):
    health_db.save_consultation("2026-05-01", "cardiology")
    health_db.save_consultation("2026-05-02", "neurology")
    types = {c["specialist_type"]
             for c in health_db.get_consultations(specialist_type="cardiology")}
    assert types == {"cardiology"}


# ── alerts ───────────────────────────────────────────────────────────────────

def test_save_alert_then_active(db):
    health_db.save_alert("lab", "high WBC")
    assert "high WBC" in [a["message"] for a in health_db.get_active_alerts()]


def test_get_active_alerts_source_filter(db):
    health_db.save_alert("a", "m1", source="survivorship:x")
    health_db.save_alert("b", "m2", source="manual")
    msgs = [a["message"] for a in health_db.get_active_alerts(source_like="survivorship%")]
    assert msgs == ["m1"]


def test_save_alert_inactive_excluded(db):
    health_db.save_alert("a", "hidden", active=False)
    assert "hidden" not in [a["message"] for a in health_db.get_active_alerts()]


# ── episodes ─────────────────────────────────────────────────────────────────

def test_get_episodes_filter_by_status(db):
    db.add_episode("E1", status="active")
    db.add_episode("E2", status="resolved")
    titles = {e["title"] for e in health_db.get_episodes(status="active")}
    assert titles == {"E1"}


# ── medications ──────────────────────────────────────────────────────────────

def test_get_medications_default_excludes_proposed(db):
    db.execute("INSERT INTO medications (name, confirmation) VALUES (?, ?)",
               ("ConfirmedMed", "confirmed"))
    db.execute("INSERT INTO medications (name, confirmation) VALUES (?, ?)",
               ("ProposedMed", "proposed"))
    default_names = {m["name"] for m in health_db.get_medications()}
    with_proposed = {m["name"] for m in health_db.get_medications(include_proposed=True)}
    assert "ConfirmedMed" in default_names
    assert "ProposedMed" not in default_names
    assert "ProposedMed" in with_proposed
