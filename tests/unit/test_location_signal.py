"""
location_signal — сигнал локации с устройства (iOS Shortcut → travel-режим).

Покрывает: is_home (гаверсинус дом/поездка), record→latest, датчик свежести
(fresh/stale/нет-сигнала), запись belief current_location, best-effort reverse-geocode
(сбой → None, координаты остаются). Сеть НЕ трогаем (geocode=False / monkeypatch).
БД — фикстура `db` (свежая tmp-схема на каждый тест).
"""
from __future__ import annotations

import pytest

import location_signal as ls
import memory_facts_db as mf

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _synthetic_home(monkeypatch):
    """Дом — данные тенанта (с 2026-09-23 литерала-дефолта нет): здесь синтетический дом."""
    monkeypatch.setattr(ls, "home_anchor", lambda: (64.15, -21.94, 15.0))


def test_is_home_vs_lisbon():
    assert ls.is_home(64.15, -21.94) is True          # дом
    assert ls.is_home(64.13, -21.96) is True          # рядом с домом (~2км)
    assert ls.is_home(38.72, -9.14) is False         # Лиссабон — поездка
    assert ls.is_home(69.65, 18.96) is False         # Тромсё — поездка


def test_record_then_latest_home(db, monkeypatch):
    monkeypatch.setattr(ls, "_reverse_geocode", lambda *a, **k: None)
    s = ls.record(64.15, -21.94, accuracy_m=12.0)
    assert s["is_home"] is True and s["label"] == "дом"
    row = ls.latest()
    assert row and abs(row["lat"] - 64.15) < 1e-6 and row["is_home"] == 1


def test_record_travel_writes_belief(db, monkeypatch):
    monkeypatch.setattr(ls, "_reverse_geocode", lambda *a, **k: "Лиссабон")
    ls.record(38.72, -9.14)
    facts = {f["key"]: f["value"] for f in mf.get_facts("fact")}
    assert facts.get("current_location") == "Лиссабон"


def test_resolve_place_fresh_home(db, monkeypatch):
    monkeypatch.setattr(ls, "_reverse_geocode", lambda *a, **k: None)
    ls.record(64.15, -21.94)
    p = ls.resolve_place()
    assert p["known"] and p["fresh"] and p["is_home"] is True


def test_resolve_place_stale(db):
    """Сигнал старше порога → fresh=False (travel-режим не доверяет координатам)."""
    import health_db as hdb
    with hdb.get_conn() as c:
        c.execute("INSERT INTO device_location (subject,lat,lon,is_home,received_at) "
                  "VALUES ('self',38.72,-9.14,0, datetime('now','-5 days'))")
    p = ls.resolve_place(max_age_h=36)
    assert p["known"] is True and p["fresh"] is False and p["is_home"] is False


def test_resolve_place_no_signal(db):
    p = ls.resolve_place()
    assert p["known"] is False and p["fresh"] is False


def test_reverse_geocode_failure_is_none(monkeypatch):
    """Сбой сети reverse-geocode → None, не исключение (координаты важнее имени)."""
    def _boom(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr(ls.urllib.request, "urlopen", _boom)
    assert ls._reverse_geocode(64.15, -21.94) is None
