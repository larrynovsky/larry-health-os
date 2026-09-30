#!/usr/bin/env python3.11
"""
test_notify — маршрутизация дублирующего канала (anti Telegram-SPOF).

Проверяем РЕШЕНИЕ «куда ушло», не реальный сетевой вызов (мокаем оба канала).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import notify as n


def test_telegram_ok_no_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(n, "_telegram", lambda m, secrets=None: True)
    monkeypatch.setattr(n, "_healthcheck_fail", lambda m, secrets=None: calls.append(m) or True)
    assert n.notify("x") == "telegram"
    assert calls == [], "fallback не должен вызываться при успехе Telegram"


def test_fallback_on_telegram_failure(monkeypatch):
    monkeypatch.setattr(n, "_telegram", lambda m, secrets=None: False)
    monkeypatch.setattr(n, "_healthcheck_fail", lambda m, secrets=None: True)
    assert n.notify("x") == "fallback"


def test_none_when_both_down(monkeypatch):
    monkeypatch.setattr(n, "_telegram", lambda m, secrets=None: False)
    monkeypatch.setattr(n, "_healthcheck_fail", lambda m, secrets=None: False)
    assert n.notify("x") == "none"


def test_fallback_disabled(monkeypatch):
    monkeypatch.setattr(n, "_telegram", lambda m, secrets=None: False)
    monkeypatch.setattr(n, "_healthcheck_fail", lambda m, secrets=None: True)
    assert n.notify("x", fallback=False) == "none", \
        "при fallback=False резервный канал не используется"


def test_notify_never_raises(monkeypatch):
    def boom(m, secrets=None): raise RuntimeError("network")
    monkeypatch.setattr(n, "_telegram", boom)
    monkeypatch.setattr(n, "_healthcheck_fail", boom)
    # notify не должен пробрасывать — доставка алерта не роняет вызывающего
    try:
        # _telegram бросает внутри notify → notify обязан проглотить?
        # _telegram сам ловит и возвращает False, поэтому boom тут имитирует
        # худший случай: проверяем, что внешний вызов не падает.
        res = n.notify("x")
    except Exception as e:
        raise AssertionError(f"notify пробросил исключение: {e!r}")
    assert res in ("telegram", "fallback", "none")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
