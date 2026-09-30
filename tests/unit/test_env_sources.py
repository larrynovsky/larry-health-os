"""Среда С-1: чистый парсер Open-Meteo (сеть не трогаем — фетч верифицируется живьём)."""
from __future__ import annotations

import pytest

import env_sources as es

pytestmark = pytest.mark.unit

_FC = {"daily": {"temperature_2m_max": [39.4], "uv_index_max": [10.2]}}
_AQ = {"hourly": {"pm10": [20, 55, 48], "pm2_5": [10, 12, 11],
                  "dust": [3, 120, 90], "uv_index": [8, 10, 9]}}


def test_parse_takes_daily_and_peaks():
    d = es.parse_open_meteo(_FC, _AQ)
    assert d["temp_max"] == 39.4
    assert d["uv_max"] == 10.2
    assert d["dust"] == 120     # пик пыли за день
    assert d["pm10"] == 55
    assert d["pm25"] == 12


def test_parse_missing_fields_safe():
    d = es.parse_open_meteo({}, {})
    assert d["dust"] is None and d.get("temp_max") is None


def test_parse_ignores_nulls_in_series():
    d = es.parse_open_meteo(_FC, {"hourly": {"dust": [None, 42, None]}})
    assert d["dust"] == 42
