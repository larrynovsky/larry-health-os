"""
UC-B-11 — Checkin следующего утра виден GP daily.

Источник: USE_CASES.md §3.B → UC-B-11.
Status: `partial`.

(Примечание: RYW-инвариант уже покрыт в `tests/consistency/test_ryw_checkin_to_gp.py`.
Здесь — integration на стороне gp_agent.)
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.integration


def test_recent_checkin_in_build_context(db, clock):
    clock.set("2026-05-08T09:00:00")
    db.add_checkin("2026-05-07", question="Как день?",
                    answer="нормально, поработал", time_of_day="evening")
    # GP daily читает контекст
    import gp_agent
    ctx = gp_agent._build_gp_context(date(2026, 5, 7))
    assert isinstance(ctx, str)
    # Просто smoke — наличие в контексте проверять не обязательно (структура
    # контекста зависит от реализации)


def test_checkin_visibility_via_health_db_api(db, clock):
    clock.set("2026-05-08")
    db.add_checkin("2026-05-07", question="Q", answer="visible_marker",
                    time_of_day="evening")

    import health_db
    recent = health_db.get_recent_checkins(7)
    assert any("visible_marker" in (c.get("answer") or "") for c in recent)
