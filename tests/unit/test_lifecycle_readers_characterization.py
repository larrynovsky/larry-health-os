"""
tests/unit/test_lifecycle_readers_characterization.py — пины оставшихся
lifecycle/reader-утилит health_db (Поток B добор, 2026-06-27):
  med-gate, hae-alerts, lab-dates, future_periods, raw_snp, episode, awaiting.

behavior-preserving пины: «как есть» ≠ «как правильно».
"""
from __future__ import annotations

import health_db


def test_unsent_proposed_meds_and_mark_gate_sent(db):
    health_db.upsert_medication("MedX")  # proposed, gate_sent_at NULL
    unsent = health_db.get_unsent_proposed_medications()
    assert "MedX" in [m["name"] for m in unsent]
    mid = [m for m in unsent if m["name"] == "MedX"][0]["id"]
    health_db.mark_medication_gate_sent(mid)
    assert "MedX" not in [m["name"] for m in health_db.get_unsent_proposed_medications()]


def test_get_hypotheses_awaiting_excludes_those_with_outcome(db):
    mid = db.add_hypothesis({"status": "testing", "observation": "o"})
    health_db.save_hypothesis_outcome(mid, "confirmed")
    assert mid not in [h["memory_id"] for h in health_db.get_hypotheses_awaiting_evaluation()]


def test_get_hypotheses_awaiting_is_per_round(db):
    """Решение владельца 30.08: outcome старше замка eval_started_at не гасит
    ожидание — консилиум, убитый рестартом, должен быть подобран. На старом
    коде (любой outcome исключает) — красный."""
    mid = db.add_hypothesis({"status": "testing", "observation": "o",
                             "eval_started_at": "2026-08-30T15:59:49"})
    db.execute("INSERT INTO hypothesis_outcomes (memory_id, verdict, evaluated_at) "
               "VALUES (?, 'partial', '2026-08-29')", (mid,))
    assert mid in [h["memory_id"] for h in health_db.get_hypotheses_awaiting_evaluation()]
    db.execute("INSERT INTO hypothesis_outcomes (memory_id, verdict, evaluated_at) "
               "VALUES (?, 'partial', '2026-08-30')", (mid,))
    assert mid not in [h["memory_id"] for h in health_db.get_hypotheses_awaiting_evaluation()]


def test_get_pending_hae_alerts_includes_new(db):
    health_db.upsert_hae_metric("steps_var", status="new")
    assert "steps_var" in [a["metric_name"] for a in health_db.get_pending_hae_alerts()]


def test_get_all_lab_dates_sorted_distinct(db):
    db.add_lab_result("2026-02-01", "WBC", 4.0)
    db.add_lab_result("2026-01-01", "RBC", 5.0)
    db.add_lab_result("2026-02-01", "HGB", 13.0)
    assert health_db.get_all_lab_dates() == ["2026-01-01", "2026-02-01"]


def test_future_periods_includes_future_only(db):
    db.add_period("future", start_date="2027-01-01", active=1)
    db.add_period("past", start_date="2020-01-01", active=1)
    names = {p["name"] for p in health_db.future_periods()}
    assert "future" in names
    assert "past" not in names


def test_get_raw_snp(db):
    db.execute("INSERT INTO raw_snps (rsid, genotype) VALUES (?, ?)", ("rsX", "AG"))
    assert health_db.get_raw_snp("rsX")["genotype"] == "AG"
    assert health_db.get_raw_snp("nope") is None


def test_save_episode_returns_id_and_appears(db):
    eid = health_db.save_episode("E1", "2026-01-01")
    assert isinstance(eid, int)
    assert "E1" in [e["title"] for e in health_db.get_episodes()]
