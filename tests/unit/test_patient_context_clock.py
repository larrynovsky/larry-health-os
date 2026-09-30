"""
Boundary-тесты возрастных/recency-хелперов patient_context под единым клоком.

Анти-rot: дата данных ФИКСИРОВАНА, «сейчас» двигаем set_test_clock. `_age_label`
даёт многопороговый чек (сегодня/вчера/N дн). RED: на прямых часах заморозка не
подействовала бы (см. симуляцию в CI-логе разработки).
"""
from __future__ import annotations

import pytest

import patient_context as pc
import _time_inject

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_clock():
    yield
    _time_inject.clear_test_clock()


def test_age_suffix_follows_clock():
    _time_inject.set_test_clock("2026-07-17")
    assert pc._age_suffix("1990-07-01") == ", 36 лет"
    _time_inject.set_test_clock("2050-07-17")           # клок +24 года
    assert pc._age_suffix("1990-07-01") == ", 60 лет"
    assert pc._age_suffix(None) == ""
    assert pc._age_suffix("мусор") == ""


def test_age_label_boundaries():
    _time_inject.set_test_clock("2026-07-17")
    assert pc._age_label("2026-07-17").strip() == "[2026-07-17, сегодня]"
    assert pc._age_label("2026-07-16").strip() == "[2026-07-16, вчера]"
    assert pc._age_label("2026-07-10").strip() == "[2026-07-10, 7 дн. назад]"
    assert pc._age_label("не-дата").strip() == ""


def test_age_days_follows_clock():
    _time_inject.set_test_clock("2026-07-17")
    assert pc._age_days("2026-07-10") == 7
    _time_inject.set_test_clock("2026-07-24")            # тот же ts, клок +7д
    assert pc._age_days("2026-07-10") == 14
    assert pc._age_days(None) is None


def test_utc_floor_follows_clock():
    from datetime import datetime, timedelta
    _time_inject.set_test_clock("2026-07-17 12:00:00")
    exp = (datetime(2026, 7, 17, 12, 0, 0) - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
    assert pc._utc_floor_str(12) == exp                 # freshness-потолок = клок − N часов
    _time_inject.set_test_clock("2026-07-18 12:00:00")  # +24ч → потолок сдвинулся на сутки
    assert pc._utc_floor_str(12) == "2026-07-18 00:00:00"
