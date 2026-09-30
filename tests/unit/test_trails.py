"""
Трейлы (S4, второй этап): семейная активность на выходной день дома.

Гейт: выходной + тенант дома + погода не опасна. Каждому, когда ОН дома (2026-07-14).
Список — данные региона (private/region.yaml, с 2026-09-23, pub-prep); логику судим на
СИНТЕТИЧЕСКОМ пакете, форму настоящего — отдельным тестом со skip без приватного файла.
"""
from __future__ import annotations

from datetime import date

import pytest

import brief_cards as bc
import brief_pipeline as bp
import env_sources as es
import location_signal as ls
import region_pack
import trails as tr

pytestmark = pytest.mark.unit

_SAT = date(2026, 7, 18)   # суббота
_TUE = date(2026, 7, 14)   # будни


_PACK = {"trails": [{"name": f"Trail {i}", "km": None, "note": f"~{i} км от дома"} for i in range(5)]}


@pytest.fixture(autouse=True)
def _synthetic_region(monkeypatch):
    monkeypatch.setattr(region_pack, "value", lambda k, d=None: _PACK.get(k, d))


def _home(monkeypatch, is_home=True):
    monkeypatch.setattr(ls, "resolve_place", lambda *a, **k: {
        "known": True, "fresh": True, "is_home": is_home, "lat": 64.1, "lon": -21.9,
        "name": "Рейкьявик", "age_h": 0.0, "source": "device_gps"})
    # среда молчит (нет сети/хазарда)
    for fn in ("fetch_open_meteo", "fetch_aqicn", "fetch_marine", "fetch_met_warnings"):
        monkeypatch.setattr(es, fn, lambda *a, **k: {"ok": False})


def test_from_trail_card():
    c = bc.from_trail({"name": "Esja Ridge Loop", "km": None, "note": "Эсья"})
    assert c.provider == "trail" and c.semantic_key.startswith("movement:trail:")
    assert "Esja" in c.evidence_summary


def test_pick_trail_rotates():
    assert tr.pick_trail(0)["name"] != tr.pick_trail(1)["name"]


def test_no_region_pack_no_trail(monkeypatch):
    monkeypatch.setattr(region_pack, "value", lambda k, d=None: d)
    assert tr.pick_trail(0) is None


@pytest.mark.skipif(not region_pack.PATH.exists(), reason="приватного пакета региона нет (публичный клон)")
def test_real_pack_trail_list():
    import yaml
    names = [t["name"] for t in yaml.safe_load(region_pack.PATH.read_text(encoding="utf-8"))["trails"]]
    assert len(names) >= 45 and len(names) == len(set(names))  # ~50 троп, без дублей


def test_weekend_home_shows_trail(db, monkeypatch):
    _home(monkeypatch, is_home=True)
    cards = bp.assemble_cards(_SAT)
    assert any(getattr(c, "provider", "") == "trail" for c in cards)


def test_weekday_no_trail(db, monkeypatch):
    _home(monkeypatch, is_home=True)
    cards = bp.assemble_cards(_TUE)
    assert not any(getattr(c, "provider", "") == "trail" for c in cards)


def test_away_weekend_no_trail(db, monkeypatch):
    _home(monkeypatch, is_home=False)
    cards = bp.assemble_cards(_SAT)
    assert not any(getattr(c, "provider", "") == "trail" for c in cards)
