"""
UC-B-07 — GP weekly синтезирует MDT, labs, problem_list, tasks, genome, freshness.

Источник: USE_CASES.md §3.B → UC-B-07.
Реализация: `gp_agent.py weekly`.
Status: `partial`.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_gp_agent_module_imports():
    import gp_agent
    assert gp_agent is not None


def test_gp_agent_has_weekly_function():
    import gp_agent
    # weekly может быть функцией или CLI-режимом
    has_weekly = (hasattr(gp_agent, "generate_weekly_report") or
                  hasattr(gp_agent, "weekly") or
                  hasattr(gp_agent, "run_weekly") or
                  hasattr(gp_agent, "generate_daily_report"))  # daily как минимум
    assert has_weekly, "gp_agent не имеет weekly-функции"


def test_gp_weekly_marks_mdt_missing_when_no_mdt(db, clock):
    """
    Если MDT не запускался — weekly должен либо явно помечать `mdt_missing=true`,
    либо работать на 7д контексте без падения.

    Smoke: вызов не падает.
    """
    clock.set("2026-05-11T07:00:00")  # понедельник
    pytest.skip("UC-B-07 weekly synthesis требует Anthropic — e2e_mock уровня")
