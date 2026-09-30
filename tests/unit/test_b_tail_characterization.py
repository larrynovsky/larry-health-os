"""
tests/unit/test_b_tail_characterization.py — пины тривиального хвоста Потока B
(2026-06-27): state-transition / TG-delivery утилиты, переехавшие в домены при C.
  mark_genome_log_sent, complete_planned_event, mark_hypothesis_outcome_sent,
  confirm_field_alias, field-review tg-message, auto_confirm_stale_reviews.

behavior-preserving пины: «как есть» ≠ «как правильно».
"""
from __future__ import annotations

import health_db


def test_mark_genome_log_sent(db):
    lid = health_db.save_genome_update_log({"run_date": "2026-05-01"})
    health_db.mark_genome_log_sent(lid)
    assert db.fetchone("SELECT sent_to_user FROM genome_update_log WHERE id=?", (lid,))["sent_to_user"] == 1


def test_complete_planned_event(db):
    eid = db.add_event(event_type="lab", status="planned")
    health_db.complete_planned_event(eid)
    assert db.fetchone("SELECT status FROM events WHERE id=?", (eid,))["status"] == "completed"


def test_mark_hypothesis_outcome_sent_removes_from_unsent(db):
    mid = db.add_hypothesis({"observation": "o"})
    health_db.save_hypothesis_outcome(mid, "confirmed", coordinator_text="t")
    assert mid in [o["memory_id"] for o in health_db.get_unsent_hypothesis_outcomes()]
    health_db.mark_hypothesis_outcome_sent(mid)
    assert mid not in [o["memory_id"] for o in health_db.get_unsent_hypothesis_outcomes()]


def test_confirm_field_alias(db):
    health_db.confirm_field_alias(1, "wbc", "WBC")
    assert health_db.get_confirmed_aliases(1) == {"wbc": "WBC"}


def test_field_review_tg_message_roundtrip(db):
    health_db.queue_field_reviews(1, "a.pdf", [{"raw_name": "wbc", "value": 4.2}])
    rid = health_db.get_pending_field_reviews()[0]["id"]
    health_db.set_field_review_tg_message(rid, 555)
    assert health_db.get_field_review_by_tg_message(555)["id"] == rid


def test_auto_confirm_stale_reviews(db):
    db.execute(
        "INSERT INTO pending_doc_reviews (source_file, proposed_type, status, imported_at) "
        "VALUES (?,?,?,datetime('now','-72 hours'))", ("old.pdf", "lab", "needs_review"))
    n = health_db.auto_confirm_stale_reviews(48)
    assert n >= 1
    assert "old.pdf" not in [r["source_file"] for r in health_db.get_pending_doc_reviews()]
