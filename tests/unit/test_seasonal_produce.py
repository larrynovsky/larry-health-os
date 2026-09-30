"""Сезонная таблица региона (SHARED reference — вход для сезон×геном).

С 2026-09-23 таблица — данные региона (private/region.yaml), не код (pub-prep). Логику
судим на СИНТЕТИЧЕСКОМ пакете; форму настоящего пакета — отдельным тестом, который честно
пропускается, когда приватного файла нет (публичный клон).
"""
from __future__ import annotations

import pytest
import yaml

import region_pack
import seasonal_produce as sp

pytestmark = pytest.mark.unit

_PACK = {"seasonal": {7: {"fruits": ["клубника"], "vegetables": ["помидоры"], "seafood": ["тунец"]},
                      1: {"fruits": ["апельсины"], "vegetables": ["капуста"], "seafood": ["скумбрия"]}}}


@pytest.fixture
def pack(monkeypatch):
    monkeypatch.setattr(region_pack, "value", lambda k, d=None: _PACK.get(k, d))


def test_month_from_pack(pack):
    assert sp.in_season(7)["fruits"] == ["клубника"]
    assert "клубника" not in sp.in_season(1)["fruits"]


def test_unknown_month_and_no_pack_are_empty(pack, monkeypatch):
    empty = {"fruits": [], "vegetables": [], "seafood": []}
    assert sp.in_season(99) == empty
    monkeypatch.setattr(region_pack, "value", lambda k, d=None: d)
    assert sp.in_season(7) == empty, "без пакета региона сезон пуст, а не чужой"


@pytest.mark.skipif(not region_pack.PATH.exists(), reason="приватного пакета региона нет (публичный клон)")
def test_real_pack_has_twelve_full_months():
    table = yaml.safe_load(region_pack.PATH.read_text(encoding="utf-8"))["seasonal"]
    assert set(table) == set(range(1, 13))
    for m, s in table.items():
        assert set(s) == {"fruits", "vegetables", "seafood"} and s["seafood"], f"месяц {m}"
