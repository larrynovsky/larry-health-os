"""
tests/unit/test_checkins_characterization.py — характеризационные пины домена
checkins (Поток B рефакторинга, 2026-06-27).
  save_checkin, get_recent_checkins, get_checkin_by_date, update_checkin_scores.

behavior-preserving пины: «как есть» ≠ «как правильно».
"""
from __future__ import annotations

import health_db


def test_save_checkin_then_in_recent_with_limited_keys(db):
    health_db.save_checkin("2026-05-08", "how?", "fine")
    recent = health_db.get_recent_checkins()
    assert "fine" in [c["answer"] for c in recent]
    # get_recent_checkins отдаёт только эти ключи (не SELECT *)
    assert set(recent[0].keys()) == {"date", "question", "answer", "created_at"}


def test_get_recent_checkins_respects_limit(db):
    for i in range(3):
        health_db.save_checkin("2026-05-08", "q", f"a{i}")
    assert len(health_db.get_recent_checkins(2)) == 2


def test_get_checkin_by_date_filters_time_of_day(db):
    health_db.save_checkin("2026-05-08", "q", "m", time_of_day="morning")
    health_db.save_checkin("2026-05-08", "q", "e", time_of_day="evening")
    assert health_db.get_checkin_by_date("2026-05-08", "morning")["answer"] == "m"
    assert health_db.get_checkin_by_date("2026-05-08", "evening")["answer"] == "e"


def test_get_checkin_by_date_missing_returns_none(db):
    assert health_db.get_checkin_by_date("2099-01-01") is None


def test_update_checkin_scores_returns_count_and_writes(db):
    health_db.save_checkin("2026-05-08", "q", "a", time_of_day="morning")
    n = health_db.update_checkin_scores("2026-05-08", "morning", stress_score=3)
    assert n == 1
    row = db.fetchone(
        "SELECT stress_score FROM checkins WHERE date=? AND time_of_day=?",
        ("2026-05-08", "morning"),
    )
    assert row["stress_score"] == 3
