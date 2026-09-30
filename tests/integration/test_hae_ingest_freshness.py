"""Гейт маркера свежести apple_health в REST /hae/ingest (api_hae_ingest._payload_is_fresh).

Инцидент 2026-07-11: экспорт мигрировал file→REST. Файловый _export_is_fresh смотрел
iCloud-каталоги, пустые после миграции → датчик врал «мёртво» при живом REST-потоке.
REST метит свежесть сам, но ТОЛЬКО на свежем payload — иначе heartbeat-стук (пустое тело)
или исторический backfill зеленили бы датчик (ложно-зелёное, ровно чего он не должен).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.integration

from dashboard_routers.api_hae_ingest import _payload_is_fresh


def _d(days_ago):
    return (date.today() - timedelta(days=days_ago)).isoformat()


def test_fresh_today_marks():
    assert _payload_is_fresh({_d(0): {"steps": 100}}) is True


def test_empty_payload_not_fresh():
    # heartbeat/пустой стук → НЕ метим, иначе датчик зеленеет на стуке
    assert _payload_is_fresh({}) is False


def test_stale_backfill_not_fresh():
    assert _payload_is_fresh({_d(30): {"steps": 100}}) is False


def test_newest_day_wins():
    assert _payload_is_fresh({_d(30): {"x": 1}, _d(0): {"x": 1}}) is True


def test_boundary_two_days():
    assert _payload_is_fresh({_d(2): {"x": 1}}) is True
    assert _payload_is_fresh({_d(3): {"x": 1}}) is False
