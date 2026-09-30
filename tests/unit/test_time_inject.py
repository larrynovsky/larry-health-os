"""
Unit-тест на сам `_time_inject.py` — модуль инъекции времени.

Покрывает:
- get_today / get_now без подмены = реальное время
- set_test_clock с разными типами входа (str, date, datetime)
- clear_test_clock / is_frozen
- автосброс через autouse fixture в tests/conftest.py
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from _time_inject import (
    clear_test_clock,
    get_now,
    get_today,
    get_utcnow,
    is_frozen,
    set_test_clock,
)


pytestmark = pytest.mark.unit


def test_no_clock_returns_real_today():
    assert not is_frozen()
    today = get_today()
    assert isinstance(today, date)
    # реальная дата, никакой подмены
    assert (today - date.today()).days == 0


def test_set_test_clock_str_date():
    set_test_clock("2026-01-15")
    assert is_frozen()
    assert get_today() == date(2026, 1, 15)


def test_set_test_clock_str_datetime():
    set_test_clock("2026-03-10T09:30:00")
    assert get_today() == date(2026, 3, 10)
    n = get_now()
    assert n == datetime(2026, 3, 10, 9, 30, 0)


def test_set_test_clock_date_object():
    set_test_clock(date(2025, 12, 31))
    assert get_today() == date(2025, 12, 31)


def test_set_test_clock_datetime_object():
    set_test_clock(datetime(2026, 6, 15, 14, 0, 0))
    assert get_today() == date(2026, 6, 15)
    assert get_now() == datetime(2026, 6, 15, 14, 0, 0)


def test_clear_test_clock():
    set_test_clock("2020-01-01")
    assert is_frozen()
    clear_test_clock()
    assert not is_frozen()
    assert get_today() != date(2020, 1, 1)


def test_set_none_equivalent_to_clear():
    set_test_clock("2020-01-01")
    set_test_clock(None)
    assert not is_frozen()


def test_get_now_with_tz_when_frozen_naive():
    """Если frozen-значение naive, а запрашиваем с tz — добавляем tz."""
    set_test_clock(datetime(2026, 5, 8, 10, 0, 0))
    n = get_now(timezone.utc)
    assert n.tzinfo == timezone.utc
    assert n.replace(tzinfo=None) == datetime(2026, 5, 8, 10, 0, 0)


def test_get_now_naive_when_frozen_aware():
    """Если frozen aware, а tz не передан — возвращаем naive."""
    set_test_clock(datetime(2026, 5, 8, 10, 0, 0, tzinfo=timezone.utc))
    n = get_now(None)
    assert n.tzinfo is None


def test_invalid_type_raises():
    with pytest.raises(TypeError):
        set_test_clock(12345)


def test_autoreset_between_tests_part1():
    """
    Этот тест замораживает время. Следующий тест (`_part2`) проверяет,
    что autoreset из conftest.py снял подмену.
    """
    set_test_clock("2010-01-01")
    assert is_frozen()


def test_autoreset_between_tests_part2():
    """Если autoreset работает — здесь _TEST_CLOCK уже None."""
    assert not is_frozen()


def test_get_utcnow_with_frozen_naive():
    set_test_clock(datetime(2026, 5, 8, 12, 0, 0))
    n = get_utcnow()
    assert n == datetime(2026, 5, 8, 12, 0, 0)
