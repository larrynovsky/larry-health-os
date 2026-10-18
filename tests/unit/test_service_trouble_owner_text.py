"""Сигнал «человеку плохо от системы» в установке на одного человека (фидбэк GitHub 2026-10-18).

Владелец = оператор, поэтому ему сразу эскалация. Но текст оператору — о третьем лице
(«свяжись с ним сейчас»), и на GitHub-установке его получал сам пострадавший. Оракул:
владелец получает текст о себе, тенант по-прежнему вызывает текст оператору.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


def _run(monkeypatch, owner: bool) -> list[str]:
    import i18n
    import notify
    import secrets_paths
    import service_trouble
    from bot import helpers

    sent = []
    v = SimpleNamespace(action="escalate", confidence=0.9, signals={}, why="x")
    monkeypatch.setattr(service_trouble, "assess", lambda *a, **k: v)
    monkeypatch.setattr(service_trouble, "remember", lambda *a, **k: 7)
    monkeypatch.setattr(secrets_paths, "is_owner", lambda: owner)
    monkeypatch.setattr(notify, "notify_operator", lambda m, *a, **k: sent.append(m) or "ok")
    monkeypatch.setattr(helpers.ai, "judge_service_trouble", lambda *a: (False, 0, ""), raising=False)
    asyncio.run(helpers._service_trouble_background("как удалить приём?", "это к поддержке"))
    return sent, i18n


def test_owner_gets_text_about_himself(monkeypatch):
    sent, i18n = _run(monkeypatch, owner=True)
    assert sent == [i18n.t("support.reply.owner_alert")]


def test_tenant_still_alerts_operator(monkeypatch):
    sent, i18n = _run(monkeypatch, owner=False)
    assert sent == [i18n.t("support.reply.operator_alert")]
