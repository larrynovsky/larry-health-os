"""
Мультитенант-сторож календаря (Bug 2, 2026-07-14).

Класс бага: кэш партнёра оказался копией календаря ВЛАДЕЛЬЦА — токен партнёра
авторизует чужой Google-аккаунт (доказано: оба кэша — аккаунт владельца). secrets_dir()
fail-closed по КАТАЛОГУ, но слеп к «правильный каталог, чужой аккаунт в токене».
Сторож: fetcher пишет account в кэш; calendar_client блокирует, если account кэша
≠ ожидаемого аккаунта тенанта.

Тест ПРОВОДКИ (RST): сторож реально стоит на живом пути чтения (_read_cache),
через который идут ОБА публичных API. Падает → чужой календарь снова потечёт в бриф.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

import pytest

import calendar_client as cc

pytestmark = pytest.mark.unit


def _write_cache(tmp_path, account, monkeypatch):
    # Дата события — динамическая, в будущем относительно "сейчас". _read_cache
    # рубит завершившиеся (end < now_utc); хардкод конкретной даты делал тест
    # тайм-бомбой — "serve"-кейсы гнили, когда дата уходила в прошлое (регресс
    # 2026-07-21: событие стояло на 2026-07-20). Формат start/end — "…Z", как
    # писал бы fetcher (_parse_iso в calendar_client рассчитан на этот суффикс).
    _start = datetime.now(tz=timezone.utc) + timedelta(days=5)
    _end   = _start + timedelta(hours=4)
    cache = {
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
        "account": account,
        "calendars": [{"id": account or "x", "summary": "primary"}],
        "events": [{
            "summary": "XX 106 Homecity to Yerevan",
            "start": _start.isoformat().replace("+00:00", "Z"),
            "end": _end.isoformat().replace("+00:00", "Z"),
            "is_all_day": False,
            "location": "Homecity", "event_type": "default",
        }],
    }
    p = tmp_path / "calendar_cache.json"
    p.write_text(json.dumps(cache))
    monkeypatch.setattr(cc, "_cache_path", lambda: p)
    return p


def test_mismatch_blocks_leak(tmp_path, monkeypatch):
    # Кэш нафетчен ВЛАДЕЛЬЦЕМ, тенант ждёт своего аккаунта → блок, событий НЕТ.
    _write_cache(tmp_path, "owner@example.com", monkeypatch)
    monkeypatch.setattr(cc, "_expected_account", lambda: "partner@example.com")
    assert cc.get_travel_events(60) == []
    assert cc.format_calendar_context(14) == ""


def test_match_serves(tmp_path, monkeypatch):
    # Аккаунт совпал → события отдаются.
    _write_cache(tmp_path, "partner@example.com", monkeypatch)
    monkeypatch.setattr(cc, "_expected_account", lambda: "partner@example.com")
    ev = cc.get_travel_events(60)
    assert len(ev) == 1 and "Yerevan" in ev[0]["title"]


def test_missing_account_field_blocks_when_configured(tmp_path, monkeypatch):
    # Старый кэш без поля account, но тенант сконфигурирован → fail-closed.
    _write_cache(tmp_path, None, monkeypatch)
    monkeypatch.setattr(cc, "_expected_account", lambda: "partner@example.com")
    assert cc.get_travel_events(60) == []


def test_unconfigured_serves_with_warning(tmp_path, monkeypatch, caplog):
    # Нет ожидаемого аккаунта → обратная совместимость: отдаём, но громко предупреждаем.
    _write_cache(tmp_path, "whoever@example.com", monkeypatch)
    monkeypatch.setattr(cc, "_expected_account", lambda: None)
    import logging
    with caplog.at_level(logging.WARNING):
        ev = cc.get_travel_events(60)
    assert len(ev) == 1
    assert any("CAL_TENANT_UNVERIFIED" in r.message for r in caplog.records)
