"""Окно рекомендаций 09–21 считается в поясе тенанта (28.09.2026).

До фикса check_recommendations_scheduled брал TZ модуля (пояс владельца): у тенанта
с HEALTH_TZ в другом поясе окно съезжало — сообщение могло прийти ночью.
Оракул: момент, когда у владельца день, а у тенанта ночь, — функция молчит и даже
не спрашивает протоколы; обратный случай — доходит до протоколов.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import jobs.scheduled as sched

pytestmark = pytest.mark.unit

_INSTANT = datetime(2026, 1, 15, 3, 0, tzinfo=timezone.utc)   # выдуманный момент


class _FrozenDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return _INSTANT.astimezone(tz) if tz else _INSTANT.replace(tzinfo=None)


def _run(monkeypatch, owner_tz, tenant_tz):
    asked = []
    monkeypatch.setattr(sched, "_dt", _FrozenDT)
    monkeypatch.setattr(sched, "TZ", ZoneInfo(owner_tz))
    monkeypatch.setenv("HEALTH_TZ", tenant_tz)
    monkeypatch.setattr(sched, "get_chat_id", lambda: 555)
    monkeypatch.setattr(sched.db, "get_active_protocols", lambda: asked.append(1) or [])
    ctx = SimpleNamespace(bot=SimpleNamespace(), bot_data={})
    asyncio.run(sched.check_recommendations_scheduled(ctx))
    return bool(asked)


def test_tenant_night_is_quiet_even_when_owner_has_day(monkeypatch):
    # 03:00 UTC: в Токио 12:00 (день), в Нью-Йорке 22:00 (вне окна)
    assert _run(monkeypatch, "Asia/Tokyo", "America/New_York") is False


def test_tenant_day_passes_even_when_owner_has_night(monkeypatch):
    assert _run(monkeypatch, "America/New_York", "Asia/Tokyo") is True
