"""
Поездки календаря не обрезаются до первых шести, а горизонт выборки назван (18.11).

Живой случай: купленный рейс через 29 дней был 11-м перелётом в кэше. Шапка веры и контекст
календаря показывали первые 6, шапка «авторитетна» — и бот уверенно отвечал «такого рейса нет».
Ниже — вымышленные рейсы; дата и «сейчас» фиксированы через _time_inject (анти-гниение, как в
test_calendar_end_boundary.py).
"""
from __future__ import annotations

import json
import pytest

import calendar_client as cc
import _time_inject

pytestmark = pytest.mark.unit

NOW = "2032-11-18T08:00"


@pytest.fixture(autouse=True)
def _reset_clock():
    yield
    _time_inject.clear_test_clock()


def _install(tmp_path, monkeypatch, n=14, time_max="2032-12-18T09:00:00+00:00"):
    ev = [{"summary": f"Flight to City{i:02d} (XX {100 + i})", "start": f"2032-11-{20 + i:02d}T05:00:00Z"
           if 20 + i <= 30 else f"2032-12-{20 + i - 30:02d}T05:00:00Z",
           "end": f"2032-11-{20 + i:02d}T08:00:00Z" if 20 + i <= 30 else f"2032-12-{20 + i - 30:02d}T08:00:00Z",
           "is_all_day": False, "location": "", "event_type": "fromGmail"} for i in range(n)]
    ev.append({"summary": "Flight to Farland (XX 999)", "start": "2033-02-01T05:00:00Z", "end": "2033-02-01T08:00:00Z",
               "is_all_day": False, "location": "", "event_type": "fromGmail"})
    cache = {"fetched_at": "2032-11-18T06:00:00Z", "account": None, "time_max": time_max,
             "calendars": [{"id": "x", "summary": "primary"}], "events": ev}
    p = tmp_path / "calendar_cache.json"
    p.write_text(json.dumps(cache))
    monkeypatch.setattr(cc, "_cache_path", lambda: p)
    monkeypatch.setattr(cc, "_expected_account", lambda: None)
    _time_inject.set_test_clock(NOW)


def test_eleventh_flight_in_calendar_context(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch)
    txt = cc.format_calendar_context(days=30)
    assert "City13" in txt, "поездки за шестой обрезаны (14 рейсов: обычный список режет на 12, раздел поездок — нет)"
    assert "Farland" not in txt, "горизонт days не соблюдается"
    assert "календарь загружен до 18.12.2032" in txt


def test_travel_events_sorted_and_bounded(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch)
    ev = cc.get_travel_events(days=30)
    dates = [e["start_date"] for e in ev]
    assert dates == sorted(dates) and len(ev) == 14, dates
    assert len(cc.get_travel_events(days=5)) == 4   # 20–23.11 при «сейчас» 18.11 + 5 дней


def test_belief_header_lists_all_trips_and_horizon(db, monkeypatch, tmp_path):
    import health_db, beliefs
    monkeypatch.setattr(health_db, "get_profile_context", lambda: {"identity": {"location": "Тестовая страна"}})
    _install(tmp_path, monkeypatch)
    h = beliefs.render_header()
    assert "City13" in h, "в шапке веры не все поездки горизонта"
    assert "ВСЕ поездки календаря до 18.12.2032" in h and "НЕ значит отмену" in h, h


def test_old_cache_without_time_max_still_works(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch, time_max=None)
    assert cc.horizon_date() is None
    assert "загружен до" not in cc.format_calendar_context(days=30)
