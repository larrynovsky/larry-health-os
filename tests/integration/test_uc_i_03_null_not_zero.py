"""
UC-I-03 — NULL ≠ 0 в нарративе агентов.

Источник: USE_CASES.md §4.I → UC-I-03 (alias `UC-NULL-001`).
Status: `partial` (известный баг в gp_agent.py:478,389,527-528) — часть тестов xfail.
"""
from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.integration


# Паттерны, которые НЕ должны появляться в тексте при пустых данных
ZERO_PATTERNS = [
    re.compile(r"\bDeep:?\s*0\s*мин", re.IGNORECASE),
    re.compile(r"\bHRV:?\s*0\b"),
    re.compile(r"\b0\s*часов\b"),
    re.compile(r"\b0\s*мин(?:ут)?\b"),  # «0 мин» / «0 минут»
    re.compile(r"\bспал\s+0\b"),
    re.compile(r"\bВСР:?\s*0\b"),
]


def _has_zero_leak(text: str) -> list[str]:
    """Возвращает список совпавших паттернов (пусто = инвариант сохранён)."""
    return [p.pattern for p in ZERO_PATTERNS if p.search(text)]


def test_build_context_does_not_say_deep_0_when_no_data(db, clock):
    """
    При пустой БД GP-контекст не содержит «Deep: 0 мин» / «HRV 0».
    Фикс W2A-1 (2026-05-08): `_fmt_helpers.fmt_min()` заменяет
    `int((stats.get('avg_deep') or 0)*60)` на None-aware форматтер.
    """
    clock.set("2026-05-08")

    from datetime import date
    import gp_agent
    ctx = gp_agent._build_gp_context(date(2026, 5, 7), period_days=7)
    leaks = _has_zero_leak(ctx)

    assert not leaks, (
        f"NULL → 0 leaks обнаружены: {leaks}. "
        f"Контекст:\n{ctx[:600]}"
    )


def test_build_context_uses_dash_for_missing_data(db, clock):
    """При отсутствии данных в TRENDS используется «—» вместо «0 мин»."""
    clock.set("2026-05-08")
    from datetime import date
    import gp_agent
    ctx = gp_agent._build_gp_context(date(2026, 5, 7), period_days=7)
    # При полностью пустой БД ожидаем тире вместо нулей
    # (т.к. avg_deep будет None для всех окон)
    if "Deep:" in ctx:
        deep_section = ctx[ctx.find("Deep:"):ctx.find("Deep:") + 100]
        assert "—" in deep_section, (
            f"При пустых данных Deep-секция должна содержать '—'. "
            f"Got: {deep_section}"
        )


def test_build_context_with_data_includes_real_numbers(db, clock):
    """
    Cross-check: при заполненных данных числа в контексте есть. Это значит
    инвариант не блокирует ВСЕ нули, а только NULL → 0 переход.
    """
    clock.set("2026-05-08")

    from datetime import date
    # Заполняем 14 дней с реальными значениями
    for offset in range(1, 15):
        d = date(2026, 5, 8) - __import__("datetime").timedelta(days=offset)
        db.add_daily_metrics(str(d), hrv=22, sleep_total=7.5, sleep_deep=0.7,
                              steps=8000, readiness=75)

    import gp_agent
    ctx = gp_agent._build_gp_context(date(2026, 5, 7), period_days=7)

    # При реальных данных в трендах должны быть какие-то цифры
    assert "Deep:" in ctx or "deep" in ctx.lower()
    # Среднее deep ≈ 42 мин (0.7ч * 60). Должно появиться число рядом с deep.
    deep_section = ctx[ctx.lower().find("deep"):ctx.lower().find("deep") + 200]
    assert re.search(r"\b\d+\b", deep_section), \
        f"Deep section без цифр? {deep_section[:200]}"


def test_no_hrv_zero_when_no_oura(db, clock):
    """`HRV: 0` или `ВСР: 0` не должно быть при пустой БД."""
    clock.set("2026-05-08")

    from datetime import date
    import gp_agent
    ctx = gp_agent._build_gp_context(date(2026, 5, 7), period_days=7)

    assert not re.search(r"\bHRV:?\s*0\b", ctx), "HRV: 0 в пустом контексте"
    assert not re.search(r"\bВСР:?\s*0\b", ctx), "ВСР: 0 в пустом контексте"
