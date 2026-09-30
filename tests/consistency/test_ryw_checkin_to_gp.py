"""
Read-your-writes: вечерний checkin (день N) виден GP daily следующим утром (N+1).

Источник: USE_CASES.md UC-B-11 + TEST_ARCHITECTURE.md §5.1.
Уровень: consistency.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.consistency


def test_checkin_day_n_visible_in_gp_day_n_plus_1(db, clock):
    """
    Day N evening: записываем checkin.
    Day N+1 morning: build_context → секция recent_checkins содержит запись.
    """
    clock.set("2026-05-08T21:00:00")  # вечер
    db.add_checkin(str(clock.today()),
                    question="Как день?",
                    answer="нормально, поработал",
                    time_of_day="evening")

    # Перематываем на следующее утро
    clock.advance(hours=11)  # → 2026-05-09T08:00
    assert clock.today() == date(2026, 5, 9)

    import health_db
    recent = health_db.get_recent_checkins(7)

    # RYW: только что записанный checkin за вчера должен быть в результате
    yesterday = str(clock.yesterday())
    found = [c for c in recent if c.get("date") == yesterday]
    assert found, (
        f"Checkin за {yesterday} не найден в get_recent_checkins(7). "
        f"Got: {[c.get('date') for c in recent]}"
    )
    assert "поработал" in found[0]["answer"]


def test_multiple_checkins_in_same_day_handled(db, clock):
    """Если в один день несколько checkin — все попадают (или выбирается latest)."""
    clock.set("2026-05-08")
    db.add_checkin("2026-05-08", question="Q1", answer="A1", time_of_day="morning")
    db.add_checkin("2026-05-08", question="Q2", answer="A2", time_of_day="evening")

    import health_db
    recent = health_db.get_recent_checkins(7)
    answers = {c.get("answer") for c in recent if c.get("date") == "2026-05-08"}
    # Минимум один из них должен быть в результате
    assert answers, "ни одного checkin за день не найдено"


def test_checkin_visible_immediately_within_same_session(db, clock):
    """Базовая RYW: write → read в той же 'сессии' видит."""
    clock.set("2026-05-08")
    db.add_checkin("2026-05-08", question="Q", answer="immediate")

    import health_db
    recent = health_db.get_recent_checkins(1)
    assert any("immediate" in (c.get("answer") or "") for c in recent)
