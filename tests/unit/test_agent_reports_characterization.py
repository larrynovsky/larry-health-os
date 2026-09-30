"""
tests/unit/test_agent_reports_characterization.py — характеризационные пины
домена agent_reports (Поток B рефакторинга, 2026-06-27).
  save_agent_report, get_agent_report.

behavior-preserving пины: «как есть» ≠ «как правильно».
"""
from __future__ import annotations

import health_db


def _save(name, date_str):
    return health_db.save_agent_report(
        "specialist", name, date_str,
        True, [], [], [], "summary", "findings",
    )


def test_save_and_get_agent_report(db):
    _save("longitudinal_analysis", "2026-05-01")
    reports = health_db.get_agent_report("longitudinal_analysis")
    assert len(reports) >= 1
    assert reports[0]["agent_name"] == "longitudinal_analysis"


def test_get_agent_report_returns_most_recent_first(db):
    _save("gp", "2026-05-01")
    _save("gp", "2026-06-01")
    latest = health_db.get_agent_report("gp", n=1)
    assert latest[0]["date"] == "2026-06-01"
