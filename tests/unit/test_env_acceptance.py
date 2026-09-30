"""
Приёмочные сенсоры подсистемы СРЕДА (env), теперь LOCATION-AWARE (travel-режим).

A7 (нет свежего сигнала локации ИЛИ сбой источника → молчит, НЕ фантазирует).
Дом → погода дома + сезонный якорь. Поездка → погода МЕСТА по координатам,
жара по АБСОЛЮТНОМУ порогу. A4 (карточка несёт конкретное число).

N3 (страж намерения×среда: «погуляй»→redirect) СОЗНАТЕЛЬНО не покрыт — это С-3, не построен.
"""
from __future__ import annotations

from datetime import date

import pytest

import env_context as ec
import env_sources as es

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _stub_met_warnings(monkeypatch):
    # Новый источник (MeteoAlarm) не должен ходить в сеть в юнит-тестах; по умолчанию — тишина.
    monkeypatch.setattr(es, "fetch_met_warnings", lambda *a, **k: {"ok": False})


def _ok(data):
    return {"source": "x", "ok": True, "delivery": "stable_api", "data": data}


def _fail():
    return {"source": "x", "ok": False, "delivery": "manual_staleness", "error": "down"}


@pytest.fixture(autouse=True)
def _synthetic_home(monkeypatch):
    """Дом — данные тенанта (с 2026-09-23 литерала-дефолта нет): здесь синтетический дом."""
    import location_signal as ls
    monkeypatch.setattr(ls, "home_anchor", lambda: (64.15, -21.94, 15.0))
    # Сезонная норма дома — данные региона (private/region.yaml); здесь синтетическая.
    monkeypatch.setattr(ec, "HOME_TEMP_NORM", {m: 33 if m in (6, 7, 8) else 20 for m in range(1, 13)})


def _place(known=True, fresh=True, is_home=True, lat=64.15, lon=-21.94, name="Рейкьявик"):
    return {"known": known, "fresh": fresh, "is_home": is_home,
            "lat": lat, "lon": lon, "name": name, "age_h": 0.0, "source": "device_gps"}


def _patch_loc(monkeypatch, **kw):
    import location_signal as ls
    monkeypatch.setattr(ls, "resolve_place", lambda *a, **k: _place(**kw))


# ── A7: локация неизвестна/устарела → env МОЛЧИТ ────────────────────────────

def test_a7_no_location_signal_silence(monkeypatch):
    _patch_loc(monkeypatch, known=False)
    monkeypatch.setattr(es, "fetch_open_meteo", lambda *a, **k: _ok({"uv_max": 11}))
    assert ec.assemble_env_cards(date(2026, 7, 14)) == []


def test_stale_location_uses_last_known_not_silent(monkeypatch):
    """Устаревший сигнал НЕ глушит — берём последнюю известную локацию (2026-07-14)."""
    _patch_loc(monkeypatch, fresh=False, is_home=True)
    monkeypatch.setattr(es, "fetch_open_meteo", lambda *a, **k: _ok({"uv_max": 11}))
    monkeypatch.setattr(es, "fetch_aqicn", lambda *a, **k: _fail())
    monkeypatch.setattr(es, "fetch_marine", lambda *a, **k: _fail())
    cards = ec.assemble_env_cards(date(2026, 7, 14))
    assert any(c.semantic_key == "weather:uv:high" for c in cards)  # НЕ пусто


def test_a7_source_down_silence(monkeypatch):
    """Локация свежая, но источник упал → карточки нет (не выдумываем погоду)."""
    _patch_loc(monkeypatch, is_home=True)
    monkeypatch.setattr(es, "fetch_open_meteo", lambda *a, **k: _fail())
    monkeypatch.setattr(es, "fetch_aqicn", lambda *a, **k: _fail())
    monkeypatch.setattr(es, "fetch_marine", lambda *a, **k: _fail())
    assert ec.assemble_env_cards(date(2026, 7, 14)) == []


# ── Дом: координаты дома + сезонный якорь ────────────────────────────────

def test_home_uses_home_coords_and_seasonal(monkeypatch):
    seen = {}
    def _fetch(lat, lon):
        seen["coords"] = (round(lat, 2), round(lon, 2))
        return _ok({"temp_max": 41.0})   # 41°C в июле — выше сезонной нормы ~33
    _patch_loc(monkeypatch, is_home=True)
    monkeypatch.setattr(es, "fetch_open_meteo", _fetch)
    monkeypatch.setattr(es, "fetch_aqicn", lambda *a, **k: _fail())
    monkeypatch.setattr(es, "fetch_marine", lambda *a, **k: _fail())
    cards = ec.assemble_env_cards(date(2026, 7, 14))
    assert seen["coords"] == (64.15, -21.94)                      # координаты дома
    assert any(c.semantic_key == "weather:heat:above_seasonal" for c in cards)


def test_marine_home_swimmable_card(monkeypatch):
    """Дома + тёплое спокойное море → карта «поплавать»."""
    _patch_loc(monkeypatch, is_home=True)
    monkeypatch.setattr(es, "fetch_open_meteo", lambda *a, **k: _ok({"temp_max": 30}))  # норма июля
    monkeypatch.setattr(es, "fetch_aqicn", lambda *a, **k: _fail())
    monkeypatch.setattr(es, "fetch_marine", lambda *a, **k: _ok({"sea_temp": 27.8, "wave_max": 0.6}))
    cards = ec.assemble_env_cards(date(2026, 7, 14))
    assert any(c.semantic_key == "sea:temp:swimmable" for c in cards)


def test_marine_not_fetched_when_away(monkeypatch):
    """В поездке море не дёргаем (домашний берег — дома-only)."""
    called = {"n": 0}
    monkeypatch.setattr(es, "fetch_marine", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or _ok({}))
    _patch_loc(monkeypatch, is_home=False, lat=69.65, lon=18.96, name="Тромсё")
    monkeypatch.setattr(es, "fetch_open_meteo", lambda *a, **k: _ok({"temp_max": 30}))
    ec.assemble_env_cards(date(2026, 7, 14))
    assert called["n"] == 0


# ── Поездка: координаты МЕСТА + абсолютная жара, без наземки ─────────────────

def test_travel_uses_dest_coords_and_absolute_heat(monkeypatch):
    seen = {}
    def _fetch(lat, lon):
        seen["coords"] = (round(lat, 2), round(lon, 2))
        return _ok({"temp_max": 42.0})   # вымышленная жара в поездке — абсолютный порог
    aqicn_called = {"n": 0}
    def _aq(*a, **k):
        aqicn_called["n"] += 1
        return _fail()
    _patch_loc(monkeypatch, is_home=False, lat=69.65, lon=18.96, name="Тромсё")
    monkeypatch.setattr(es, "fetch_open_meteo", _fetch)
    monkeypatch.setattr(es, "fetch_aqicn", _aq)
    cards = ec.assemble_env_cards(date(2026, 7, 14))
    assert seen["coords"] == (69.65, 18.96)                     # координаты Тромсё, не дома
    assert any(c.semantic_key == "weather:heat:high_abs" for c in cards)
    assert not any(c.semantic_key == "weather:heat:above_seasonal" for c in cards)
    assert aqicn_called["n"] == 0                               # наземку в поездке не дёргаем


def test_from_weather_absolute_vs_seasonal():
    """Одна и та же жара: дома по сезону, в поездке по абсолюту (разные ключи)."""
    home = ec.from_weather({"temp_max": 42.0}, 7, is_home=True)
    away = ec.from_weather({"temp_max": 42.0}, 7, is_home=False)
    assert any(c.semantic_key == "weather:heat:above_seasonal" for c in home)
    assert any(c.semantic_key == "weather:heat:high_abs" for c in away)
    # 30°C в поездке — не «жарко» по абсолюту (нет карточки), а дома в июле — норма
    assert ec.from_weather({"temp_max": 30.0}, 7, is_home=False) == []


def test_a4_card_carries_specific_number_not_generic():
    uv = [c for c in ec.from_weather({"uv_max": 10.0}, 7)
          if c.semantic_key == "weather:uv:high"][0]
    assert uv.allowed_claims and any("10" in ac for ac in uv.allowed_claims)


# ── C2 (2026-07-15): PM в поездке из Open-Meteo, дефолт ВОЗ-2021 суточные ────

def test_travel_pm_elevated_emits_card():
    """В поездке грязный воздух (pm2.5≥15) → карта air:pm:elevated с числами."""
    away = ec.from_weather({"pm25": 32.0, "pm10": 60.0}, 7, is_home=False)
    pm = [c for c in away if c.semantic_key == "air:pm:elevated"]
    assert pm and "PM2.5 32" in pm[0].evidence_summary


def test_home_ignores_openmeteo_pm():
    """Дома PM из Open-Meteo НЕ эмитим (воздух держит наземка aqicn)."""
    home = ec.from_weather({"pm25": 32.0, "pm10": 60.0}, 7, is_home=True)
    assert not any(c.semantic_key == "air:pm:elevated" for c in home)


def test_travel_pm_below_threshold_silent():
    """Берлин сегодня: pm2.5=6.2 / pm10=8.3 — ниже порога → карты нет."""
    assert ec.from_weather({"pm25": 6.2, "pm10": 8.3}, 7, is_home=False) == []


def test_pm_threshold_configurable():
    """Порог — параметр (конфиг §9): при 40 pm2.5=32 молчит; дефолт 15 — говорит."""
    assert ec.from_weather({"pm25": 32.0}, 7, is_home=False, pm25_thr=40.0) == []
    assert any(c.semantic_key == "air:pm:elevated"
               for c in ec.from_weather({"pm25": 32.0}, 7, is_home=False))


def test_travel_pm_via_assemble(monkeypatch):
    """Полный путь: away + Open-Meteo грязный PM → карта; наземку не зовём."""
    _patch_loc(monkeypatch, is_home=False, lat=52.52, lon=13.405, name="Берлин")
    monkeypatch.setattr(es, "fetch_open_meteo",
                        lambda *a, **k: _ok({"temp_max": 25.0, "pm25": 30.0, "pm10": 55.0}))
    aq = {"n": 0}
    monkeypatch.setattr(es, "fetch_aqicn",
                        lambda *a, **k: aq.__setitem__("n", aq["n"] + 1) or _fail())
    cards = ec.assemble_env_cards(date(2026, 7, 14))
    assert any(c.semantic_key == "air:pm:elevated" for c in cards)
    assert aq["n"] == 0


# ── C1 (2026-07-15): env_probe — статус проверки среды (не эмитит карточки) ──

def test_env_probe_no_location(monkeypatch):
    _patch_loc(monkeypatch, known=False)
    p = ec.env_probe()
    assert p["known"] is False and p["reachable"] is False


def test_env_probe_away_reachable(monkeypatch):
    _patch_loc(monkeypatch, is_home=False, lat=52.52, lon=13.405, name="Берлин")
    monkeypatch.setattr(es, "fetch_open_meteo", lambda *a, **k: _ok({"temp_max": 25.0}))
    assert ec.env_probe() == {"known": True, "reachable": True,
                              "away": True, "place": "Берлин"}


def test_env_probe_source_down_not_reachable(monkeypatch):
    _patch_loc(monkeypatch, is_home=False, lat=52.52, lon=13.405, name="Берлин")
    monkeypatch.setattr(es, "fetch_open_meteo", lambda *a, **k: _fail())
    p = ec.env_probe()
    assert p["known"] is True and p["reachable"] is False and p["away"] is True
