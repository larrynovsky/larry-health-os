"""
tests/unit/test_problems_periods_characterization.py — характеризационные пины
доменов problems и periods (Поток B рефакторинга, 2026-06-27).
  upsert_problem, get_problem_list, historical_periods.

behavior-preserving пины: «как есть» ≠ «как правильно».
"""
from __future__ import annotations

import health_db


def test_upsert_problem_appears_in_list(db):
    health_db.upsert_problem("p1", "Anemia")
    assert "p1" in [p["problem_id"] for p in health_db.get_problem_list()]


def test_get_problem_list_status_filter(db):
    health_db.upsert_problem("p1", "A", status="active")
    health_db.upsert_problem("p2", "B", status="resolved")
    active_ids = [p["problem_id"] for p in health_db.get_problem_list("active")]
    assert "p1" in active_ids
    assert "p2" not in active_ids


def test_upsert_problem_updates_existing_in_place(db):
    health_db.upsert_problem("p1", "old title")
    health_db.upsert_problem("p1", "new title")
    p1 = [p for p in health_db.get_problem_list() if p["problem_id"] == "p1"]
    assert len(p1) == 1            # upsert, не дубликат
    assert p1[0]["title"] == "new title"


def test_historical_periods_excludes_inactive(db):
    db.add_period("alive", active=1)
    db.add_period("inactive", active=0)
    names = {p["name"] for p in health_db.historical_periods()}
    assert "alive" in names
    assert "inactive" not in names


def test_historical_periods_exclude_types(db):
    db.add_period("trav", type_="travel", active=1)
    db.add_period("exp", type_="experiment", active=1)
    names = {p["name"] for p in health_db.historical_periods(exclude_types=["travel"])}
    assert "exp" in names
    assert "trav" not in names
