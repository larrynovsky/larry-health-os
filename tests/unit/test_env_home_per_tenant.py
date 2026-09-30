"""
B4 (brief-neutralization): дом-координаты среды — per-tenant, не owner-константа.

Регресс-класс (аудит 16.07): env_context брал константу дома владельца для is_home-погоды,
хотя is_home-детекция уже per-tenant (location_signal.home_anchor). Тенант с другим домом
получал бы погоду чужого города в собственном брифе (env_context кормит бриф ОБОИХ тенантов).
С 2026-09-23 (pii-scrub) константы дома нет вовсе: дом — только данные тенанта.

Тест ПРОВОДКИ (RST): дёргает РЕАЛЬНУЮ assemble_env_cards; мокает ТОЛЬКО источники
(resolve_place / home_anchor / fetch_*) — проверяет, по каким координатам реально ушёл
запрос погоды. MacBook-safe (сеть/БД не трогаются: fetch_* замкнуты).
"""
from __future__ import annotations

from datetime import date

import pytest

import env_context as ec
import env_sources as es
import location_signal as ls

pytestmark = pytest.mark.unit

OTHER_HOME = (64.15, -21.94)      # дом ДРУГОГО тенанта — не должен подставляться этому
PARTNER_HOME = (69.65, 18.96)     # дом другого тенанта (пример: Тромсё)


def _capture(monkeypatch):
    calls: list = []

    def _fake_meteo(lat, lon):
        calls.append((round(lat, 2), round(lon, 2)))
        return {"ok": False}  # короткое замыкание: карточек нет, координаты пойманы

    monkeypatch.setattr(es, "fetch_open_meteo", _fake_meteo)
    for fn in ("fetch_aqicn", "fetch_marine", "fetch_met_warnings"):
        monkeypatch.setattr(es, fn, lambda *a, **k: {"ok": False})
    return calls


def test_home_weather_uses_per_tenant_anchor_not_other_home(monkeypatch):
    """is_home → погода по home_anchor ТЕНАНТА, НЕ по чужому дому."""
    monkeypatch.setattr(ls, "home_anchor", lambda: (PARTNER_HOME[0], PARTNER_HOME[1], 15.0))
    monkeypatch.setattr(ls, "resolve_place", lambda *a, **k: {
        "known": True, "fresh": True, "is_home": True,
        "lat": PARTNER_HOME[0] + 0.01, "lon": PARTNER_HOME[1] + 0.01, "name": None})
    calls = _capture(monkeypatch)
    ec.assemble_env_cards(date(2026, 7, 16))
    assert calls, "fetch_open_meteo не вызван"
    assert calls[0] == (round(PARTNER_HOME[0], 2), round(PARTNER_HOME[1], 2)), \
        f"погода дома пошла не по home_anchor тенанта: {calls[0]}"
    assert calls[0] != (round(OTHER_HOME[0], 2), round(OTHER_HOME[1], 2)), "утечка: чужой дом"


def test_away_uses_actual_place_coords(monkeypatch):
    """away → погода по реальным координатам сигнала (не дом)."""
    monkeypatch.setattr(ls, "home_anchor", lambda: (OTHER_HOME[0], OTHER_HOME[1], 15.0))
    monkeypatch.setattr(ls, "resolve_place", lambda *a, **k: {
        "known": True, "fresh": True, "is_home": False,
        "lat": 48.85, "lon": 2.35, "name": "Paris"})
    calls = _capture(monkeypatch)
    ec.assemble_env_cards(date(2026, 7, 16))
    assert calls[0] == (48.85, 2.35), f"в поездке погода не по координатам места: {calls[0]}"


# ── Дом = ТОЛЬКО данные тенанта (§9; литерал-дефолт удалён 2026-09-23, pii-scrub) ──
def test_без_конфига_дома_нет_и_чужой_не_подставляется(db):
    """Новый тенант без дома: home_anchor честно пуст, is_home=False — никакого дефолта-города."""
    import location_signal as _ls
    assert _ls.home_anchor()[:2] == (None, None)
    assert _ls.is_home(64.15, -21.94) is False


def test_дом_читается_из_конфига_тенанта(db):
    import config_db as _cfg
    import location_signal as _ls
    _cfg.upsert_config("location.home_lat", value_num=69.65, source="manual")
    _cfg.upsert_config("location.home_lon", value_num=18.96, source="manual")
    assert _ls.home_anchor() == (69.65, 18.96, 15.0)
    assert _ls.is_home(69.66, 18.97) is True
