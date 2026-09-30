"""
Boundary-тест: dashboard_filters._age_days управляется единым клоком (_time_inject).

Анти-rot: дата данных ФИКСИРОВАНА, «сейчас» двигаем set_test_clock. Тот же ts при
сдвиге клока → возраст растёт. Если бы функция читала стенные часы напрямую,
заморозка не подействовала бы и оба ассерта вернули бы реальный возраст → RED.
"""
from __future__ import annotations

import pytest

import dashboard_filters as df
import _time_inject

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_clock():
    yield
    _time_inject.clear_test_clock()


def test_age_days_follows_frozen_clock():
    _time_inject.set_test_clock("2026-07-17")
    assert df._age_days("2026-07-10") == 7          # 7 дней до замороженного «сейчас»
    _time_inject.set_test_clock("2026-07-24")
    assert df._age_days("2026-07-10") == 14         # тот же ts, клок +7д → возраст +7


def test_age_days_datetime_form_and_none():
    _time_inject.set_test_clock("2026-07-17 12:00:00")
    assert df._age_days("2026-07-10 12:00:00") == 7
    assert df._age_days(None) is None
    assert df._age_days("не-дата") is None
