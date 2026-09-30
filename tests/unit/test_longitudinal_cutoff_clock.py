"""
Boundary-тест вынесенного порога longitudinal_analysis._recent_cutoff_iso.

Оракул независим: фикс-дата минус timedelta (НЕ через get_today) — доказывает,
что хелпер возвращает «замороженное сегодня − N». RED: на прямом date.today()
заморозка не подействовала бы и равенство с фикс-датой сломалось.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

import longitudinal_analysis as la
import _time_inject

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_clock():
    yield
    _time_inject.clear_test_clock()


def test_recent_cutoff_follows_frozen_clock():
    _time_inject.set_test_clock("2026-07-17")
    assert la._recent_cutoff_iso(90) == str(date(2026, 7, 17) - timedelta(days=90))
    _time_inject.set_test_clock("2026-08-16")            # клок +30д
    assert la._recent_cutoff_iso(90) == str(date(2026, 8, 16) - timedelta(days=90))
