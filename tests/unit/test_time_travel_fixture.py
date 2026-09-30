"""Unit-тест на саму fixture `clock` из tests/fixtures/time_travel.py."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

pytestmark = pytest.mark.unit


def test_clock_fixture_starts_unfrozen(clock):
    assert not clock.is_frozen


def test_clock_set_string(clock):
    clock.set("2026-05-08")
    assert clock.is_frozen
    assert clock.today() == date(2026, 5, 8)


def test_clock_freeze_at_alias(clock):
    clock.freeze_at(date(2026, 6, 15))
    assert clock.today() == date(2026, 6, 15)


def test_clock_advance_days(clock):
    clock.set("2026-05-08")
    clock.advance(days=3)
    assert clock.today() == date(2026, 5, 11)


def test_clock_advance_hours(clock):
    clock.set("2026-05-08T10:00:00")
    clock.advance(hours=5)
    assert clock.now().hour == 15


def test_clock_advance_negative(clock):
    clock.set("2026-05-08")
    clock.advance(days=-2)
    assert clock.today() == date(2026, 5, 6)


def test_clock_yesterday(clock):
    clock.set("2026-05-08")
    assert clock.yesterday() == date(2026, 5, 7)


def test_clock_days_ago(clock):
    clock.set("2026-05-08")
    assert clock.days_ago(7) == date(2026, 5, 1)


def test_clock_days_ahead(clock):
    clock.set("2026-05-08")
    assert clock.days_ahead(14) == date(2026, 5, 22)


def test_clock_jump_to(clock):
    clock.set("2026-05-08")
    clock.jump_to("2027-01-01")
    assert clock.today() == date(2027, 1, 1)


def test_clock_thaw(clock):
    clock.set("2026-05-08")
    assert clock.is_frozen
    clock.thaw()
    assert not clock.is_frozen


def test_clock_advance_without_set_freezes_from_now(clock):
    """advance без предварительного set → морозит от текущего now."""
    assert not clock.is_frozen
    clock.advance(days=1)
    assert clock.is_frozen
    # tomorrow относительно реального today
    assert (clock.today() - date.today()).days == 1


def test_autoreset_after_clock_fixture(clock):
    """После теста с clock — следующий тест должен видеть размороженное время.
    Этот тест замораживает; следующий — `_aftermath` — проверяет."""
    clock.set("2010-01-01")
    assert clock.is_frozen


def test_autoreset_works_aftermath():
    """Проверка что autoreset из conftest снял подмену."""
    from _time_inject import is_frozen
    assert not is_frozen()
