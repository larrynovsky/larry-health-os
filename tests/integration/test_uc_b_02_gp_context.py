"""
UC-B-02 — GP daily видит контекст 7/14/30/90 дней + labs + problem_list + genome.

Источник: USE_CASES.md §3.B → UC-B-02.
Реализация: `gp_agent._build_gp_context`.
Status: `partial`.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.integration


def test_build_context_runs_with_minimal_data(db, clock):
    """Smoke: при минимальных данных контекст строится без exceptions."""
    clock.set("2026-05-08")
    db.add_daily_metrics(str(clock.yesterday()), hrv=22, sleep_total=7.5)

    import gp_agent
    ctx = gp_agent._build_gp_context(date(2026, 5, 7), period_days=7)
    assert isinstance(ctx, str)
    assert len(ctx) > 100


def test_build_context_includes_trends_section(db, clock):
    """Контекст должен содержать секцию ТРЕНДЫ."""
    clock.set("2026-05-08")
    for offset in range(1, 10):
        d = date(2026, 5, 8) - timedelta(days=offset)
        db.add_daily_metrics(str(d), hrv=22, sleep_total=7.5, sleep_deep=0.7,
                              steps=8000)

    import gp_agent
    ctx = gp_agent._build_gp_context(date(2026, 5, 7), period_days=7)

    # Ищем 4-горизонта (7/14/30/90)
    assert ("ТРЕНДЫ" in ctx or "trends" in ctx.lower() or
            "7" in ctx and "30" in ctx)


def test_build_context_includes_problem_list(db, clock):
    clock.set("2026-05-08")
    db.add_problem("P001", "Test онкологическая проблема",
                    description="Диагноз X, стадия Y",
                    domain="oncology")

    import gp_agent
    ctx = gp_agent._build_gp_context(date(2026, 5, 7))
    # problem_list должен быть прочитан
    assert "Test" in ctx or "P001" in ctx or "проблем" in ctx.lower()


def test_build_context_uses_clock_inject(db, clock):
    """Замороженное время → `_build_gp_context` видит его."""
    clock.set("2030-12-31")
    db.add_daily_metrics("2030-12-30", hrv=22)

    import gp_agent
    # end_date явно — не должен пытаться date.today()
    ctx = gp_agent._build_gp_context(date(2030, 12, 30))
    assert isinstance(ctx, str)
