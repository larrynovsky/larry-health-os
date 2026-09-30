"""
A (2026-07-15): событие смены города. Чистая логика — геометрия дома (15 км),
location_state (home/away/None), location_event (переходы домой/новый город).
"""
from __future__ import annotations

import pytest

import location_signal as ls

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _synthetic_home(monkeypatch):
    """Дом — данные тенанта (с 2026-09-23 литерала-дефолта нет): здесь синтетический дом."""
    monkeypatch.setattr(ls, "home_anchor", lambda: (64.15, -21.94, 15.0))


# ── Геометрия дома: радиус 15 км (не 50 — соседний город больше не «дома») ──────────

def test_home_center():
    assert ls.is_home(64.15, -21.94) is True


def test_home_nearby_suburb():
    assert ls.is_home(64.21, -21.94) is True   # ~7 км — пригород, дома


def test_neighbour_city_is_away_at_15km():
    """Ключ фикса 50→15: соседний город (~36 км) больше НЕ дома."""
    assert ls.is_home(64.47, -21.94) is False


def test_ring_20km_is_away():
    """Точка в кольце 15–50 км: при 15 км — поездка (при 50 была бы дома)."""
    assert ls.is_home(64.33, -21.94) is False


# ── location_state ──────────────────────────────────────────────────────────

def _place(known=True, fresh=True, lat=64.15, lon=-21.94, name="Рейкьявик"):
    return {"known": known, "fresh": fresh, "lat": lat, "lon": lon, "name": name}


def test_state_home():
    assert ls.location_state(_place()) == "home"


def test_state_away_with_city():
    assert ls.location_state(_place(lat=52.52, lon=13.405, name="Берлин")) == "away:Берлин"


def test_state_away_no_city():
    assert ls.location_state(_place(lat=52.52, lon=13.405, name=None)) == "away:?"


def test_state_stale_is_none():
    assert ls.location_state(_place(fresh=False)) is None


def test_state_unknown_is_none():
    assert ls.location_state(_place(known=False)) is None


# ── location_event: переходы через watermark ────────────────────────────────

def test_no_event_when_state_none():
    assert ls.location_event(None, "away:Берлин") is None  # несвежо → watermark не двигаем


def test_no_event_same_state():
    assert ls.location_event("home", "home") is None
    assert ls.location_event("away:Берлин", "away:Берлин") is None


def test_first_run_home_silent():
    """Первый запуск дома (watermark None) не кричит «домой» на пустом месте."""
    assert ls.location_event("home", None) is None


def test_first_run_away_announces_city():
    ev = ls.location_event("away:Берлин", None)
    assert ev and ev["kind"] == "new_city" and ev["city"] == "Берлин"
    assert ev["new_watermark"] == "away:Берлин"


def test_home_to_away_new_city():
    ev = ls.location_event("away:Берлин", "home")
    assert ev["kind"] == "new_city" and ev["city"] == "Берлин"


def test_away_to_home_domoy():
    ev = ls.location_event("home", "away:Берлин")
    assert ev["kind"] == "home" and "домой" in ev["text"] and ev["new_watermark"] == "home"


def test_away_to_other_away():
    ev = ls.location_event("away:Мюнхен", "away:Берлин")
    assert ev["kind"] == "new_city" and ev["city"] == "Мюнхен"


def test_away_unknown_city_silent():
    """away, но город неизвестен → не объявляем (§3: нет city → молчим)."""
    assert ls.location_event("away:?", "home") is None
