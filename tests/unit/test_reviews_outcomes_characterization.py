"""
tests/unit/test_reviews_outcomes_characterization.py — пины human-gate/review
утилит health_db (Поток B добор, 2026-06-27): doc_reviews, cbcr_payload,
hypothesis_outcomes, field_reviews, profile_context, upsert_config.

behavior-preserving пины: «как есть» ≠ «как правильно».
"""
from __future__ import annotations

import health_db


def test_save_get_pending_doc_review(db):
    health_db.save_pending_doc_review("a.pdf", "lab")
    rows = health_db.get_pending_doc_reviews()
    assert "a.pdf" in [r["source_file"] for r in rows]


def test_save_get_cbcr_payload(db):
    mid = db.add_hypothesis({"observation": "o"})
    health_db.save_cbcr_payload(mid, '{"x": 1}', 5, "high", "test", "opus")
    p = health_db.get_cbcr_payload(mid)
    assert p is not None
    assert p["structural_score"] == 5


def test_get_cbcr_payload_missing_none(db):
    assert health_db.get_cbcr_payload(999999) is None


def test_save_get_hypothesis_outcome(db):
    health_db.save_hypothesis_outcome(123, "confirmed", confidence=0.8)
    o = health_db.get_hypothesis_outcome(123)
    assert o["verdict"] == "confirmed"


def test_get_hypothesis_outcome_missing_none(db):
    assert health_db.get_hypothesis_outcome(999999) is None


def test_queue_field_reviews_returns_count(db):
    n = health_db.queue_field_reviews(1, "a.pdf", [{"raw_name": "wbc", "value": 4.2}])
    assert n >= 1
    assert "wbc" in [r["raw_name"] for r in health_db.get_pending_field_reviews()]


def test_queue_field_reviews_empty_returns_zero(db):
    assert health_db.queue_field_reviews(1, "a.pdf", []) == 0


def test_get_profile_context_nested_by_dot(db):
    db.add_profile("medical.blood_type", value_text="O+")
    ctx = health_db.get_profile_context()
    assert ctx["medical"]["blood_type"] == "O+"


def test_upsert_config_then_get(db):
    health_db.upsert_config("k", value_json={"a": 1})
    assert health_db.get_config("k") == {"a": 1}
