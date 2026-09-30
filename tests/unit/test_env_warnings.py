"""
Официальные предупреждения MeteoAlarm (фид региона) → карточки среды (2026-07-14).

Источник — MeteoAlarm CAP Atom-фид (замена скрейпа национальной метеослужбы). Severe/Extreme →
safety-полоса (перебивает слот + гасит зов на тропу). Активность по onset/expires.
"""
from __future__ import annotations

from datetime import date

import pytest

import env_sources as es
import env_context as ec

pytestmark = pytest.mark.unit

_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:cap="urn:oasis:names:tc:emergency:cap:1.2">
  <entry>
    <cap:event>High Temperature Yellow</cap:event>
    <cap:severity>Moderate</cap:severity>
    <cap:onset>2026-07-13T08:00:00+00:00</cap:onset>
    <cap:expires>2026-07-15T13:59:59+00:00</cap:expires>
    <title>Yellow High-temperature Warning issued for Iceland</title>
  </entry>
  <entry>
    <cap:event>Thunderstorm Orange</cap:event>
    <cap:severity>Severe</cap:severity>
    <cap:onset>2026-07-14T06:00:00+00:00</cap:onset>
    <cap:expires>2026-07-14T20:00:00+00:00</cap:expires>
    <title>Orange Thunderstorm Warning</title>
  </entry>
</feed>"""


def test_parse_met_warnings():
    ws = es.parse_met_warnings(_SAMPLE)
    assert len(ws) == 2
    assert ws[0]["event"] == "High Temperature Yellow"
    assert ws[1]["severity"] == "Severe"


def test_from_warnings_active_and_lanes():
    data = {"warnings": es.parse_met_warnings(_SAMPLE)}
    cards = ec.from_warnings(data, date(2026, 7, 14))
    keys = {c.semantic_key: c for c in cards}
    # оба активны 14.07 → две карты
    assert any("high_temperature" in k for k in keys)
    assert any("thunderstorm" in k for k in keys)
    # Severe (гроза) → safety-полоса
    storm = next(c for k, c in keys.items() if "thunderstorm" in k)
    assert storm.lane == "safety"
    # Moderate (жара) → routine
    heat = next(c for k, c in keys.items() if "high_temperature" in k)
    assert heat.lane == "routine"
    assert "предупреждение" in heat.evidence_summary


def test_warning_not_active_outside_window():
    data = {"warnings": es.parse_met_warnings(_SAMPLE)}
    # 20.07 — вне окон обоих
    assert ec.from_warnings(data, date(2026, 7, 20)) == []


def test_empty_feed_no_cards():
    empty = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    assert es.parse_met_warnings(empty) == []
    assert ec.from_warnings({"warnings": []}, date(2026, 7, 14)) == []
