"""
Двусторонний boundary-тест фильтра «событие завершилось» в calendar_client.

Анти-rot (урок инцидента 2026-07-21): дата события ФИКСИРОВАНА, а «сейчас»
двигаем через _time_inject.set_test_clock — тест проверяет ПОВЕДЕНИЕ на пороге
end<now, а не календарь. Раньше фикстура хардкодила дату впритык к now → гнила.

Ловит регрессию: если фильтр end<now сломан (или сайт снова читает стенные часы
в обход seam и клок не действует), один из двух порогов покраснеет.
"""
from __future__ import annotations

import json
import pytest

import calendar_client as cc
import _time_inject

pytestmark = pytest.mark.unit

_EVENT_END = "2026-07-20T09:00:00Z"  # фикс; сравниваем с ним, двигая клок


@pytest.fixture(autouse=True)
def _reset_clock():
    yield
    _time_inject.clear_test_clock()


def _install_cache(tmp_path, monkeypatch):
    cache = {
        "fetched_at": "2026-07-20T07:00:00Z",  # ~2ч до клока → кэш свеж (<25ч)
        "account": None,
        "calendars": [{"id": "x", "summary": "primary"}],
        "events": [{
            "summary": "XX 106 Homecity to Tromsø",
            "start": "2026-07-20T05:00:00Z", "end": _EVENT_END,
            "is_all_day": False, "location": "Homecity", "event_type": "default",
        }],
    }
    p = tmp_path / "calendar_cache.json"
    p.write_text(json.dumps(cache))
    monkeypatch.setattr(cc, "_cache_path", lambda: p)
    monkeypatch.setattr(cc, "_expected_account", lambda: None)


def test_served_just_before_end(tmp_path, monkeypatch):
    _install_cache(tmp_path, monkeypatch)
    _time_inject.set_test_clock("2026-07-20T08:59")   # за минуту ДО конца
    ev = cc.get_travel_events(60)
    assert len(ev) == 1 and "Tromsø" in ev[0]["title"]


def test_dropped_just_after_end(tmp_path, monkeypatch):
    _install_cache(tmp_path, monkeypatch)
    _time_inject.set_test_clock("2026-07-20T09:01")   # через минуту ПОСЛЕ конца
    assert cc.get_travel_events(60) == []
