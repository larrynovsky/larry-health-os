"""
tests/unit/test_lifecycle_transitions_characterization.py — пины lifecycle-переходов
health_db (Поток B добор, 2026-06-27): doc-review confirm/reject, field-review
resolve, hypothesis-outcome delivery, soft_delete_period, upsert_profile,
conit_limit, consultation-session delete.

behavior-preserving пины: «как есть» ≠ «как правильно».
"""
from __future__ import annotations

import health_db


def test_confirm_doc_review_sets_confirmed(db):
    rid = health_db.save_pending_doc_review("a.pdf", "lab")
    health_db.confirm_doc_review(rid, "oncology")
    assert rid not in [r["id"] for r in health_db.get_pending_doc_reviews()]
    row = db.fetchone("SELECT status, confirmed_type FROM pending_doc_reviews WHERE id=?", (rid,))
    assert row["status"] == "confirmed"
    assert row["confirmed_type"] == "oncology"


def test_reject_doc_review_removes_from_pending(db):
    rid = health_db.save_pending_doc_review("b.pdf", "lab")
    health_db.reject_doc_review(rid)
    assert rid not in [r["id"] for r in health_db.get_pending_doc_reviews()]


def test_get_unsent_hypothesis_outcomes_requires_coordinator_text(db):
    mid = db.add_hypothesis({"observation": "o"})
    health_db.save_hypothesis_outcome(mid, "confirmed", coordinator_text="text")
    assert mid in [o["memory_id"] for o in health_db.get_unsent_hypothesis_outcomes()]


def test_get_hypothesis_accuracy_stats_returns_dict(db):
    assert isinstance(health_db.get_hypothesis_accuracy_stats(), dict)


def test_resolve_field_review_removes_from_pending(db):
    health_db.queue_field_reviews(1, "a.pdf", [{"raw_name": "wbc", "value": 4.2}])
    rid = health_db.get_pending_field_reviews()[0]["id"]
    health_db.resolve_field_review(rid, "WBC")
    assert rid not in [r["id"] for r in health_db.get_pending_field_reviews()]


def test_soft_delete_period_excludes_from_historical(db):
    pid = db.add_period("temp", active=1)
    health_db.soft_delete_period(pid, reason="test")
    assert "temp" not in {p["name"] for p in health_db.historical_periods()}


def test_upsert_profile_then_get(db):
    health_db.upsert_profile("blood_type", value_text="B-")
    assert health_db.get_patient_profile().get("blood_type") == "B-"


def test_get_conit_limit_returns_float(db):
    assert isinstance(health_db.get_conit_limit("oura"), float)


def test_delete_consultation_session(db):
    health_db.save_consultation_session(7, {"a": 1})
    health_db.delete_consultation_session(7)
    assert health_db.load_consultation_session(7) is None
