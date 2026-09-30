"""
tests/unit/test_lab_meta_misc_characterization.py — характеризационные пины
остаточных утилит health_db (Поток B рефакторинга, 2026-06-27):
  check_data_freshness, get_lab_format_by_name/by_id, get_effective_lab_schedule,
  get_reports_with_findings, get_context_events.

behavior-preserving пины: «как есть» ≠ «как правильно».
"""
from __future__ import annotations

import health_db


def test_check_data_freshness_returns_dict(db):
    assert isinstance(health_db.check_data_freshness(), dict)


def test_get_lab_format_by_name_and_id(db):
    db.execute("INSERT INTO lab_formats (name, description, parser_type) VALUES (?,?,?)",
               ("Synevo", "desc", "kv"))
    by_name = health_db.get_lab_format_by_name("Synevo")
    assert by_name["name"] == "Synevo"
    assert health_db.get_lab_format_by_id(by_name["id"])["name"] == "Synevo"


def test_get_lab_format_missing_returns_none(db):
    assert health_db.get_lab_format_by_name("nope") is None
    assert health_db.get_lab_format_by_id(99999) is None


def test_get_effective_lab_schedule_returns_map(db):
    db.execute(
        "INSERT INTO lab_monitoring_schedule (test_name, interval_days, priority, source, note) "
        "VALUES (?,?,?,?,?)", ("WBC", 90, "high", "manual", "n"))
    sched = health_db.get_effective_lab_schedule()
    assert sched["WBC"]["interval_days"] == 90


def test_get_reports_with_findings_excludes_no_findings(db):
    health_db.save_agent_report("specialist", "cardio", "2026-05-01", True, [], [], [], "s", "f")
    health_db.save_agent_report("specialist", "neuro", "2026-05-01", False, [], [], [], "s", "f")
    names = {r["agent_name"] for r in health_db.get_reports_with_findings("2026-01-01")}
    assert "cardio" in names
    assert "neuro" not in names


def test_get_context_events_in_range(db):
    db.execute("INSERT INTO context_events (date, source, category, value_text) VALUES (?,?,?,?)",
               ("2026-05-05", "manual", "travel", "trip"))
    events = health_db.get_context_events("2026-05-01", "2026-05-31")
    assert any(e.get("value_text") == "trip" for e in events)
