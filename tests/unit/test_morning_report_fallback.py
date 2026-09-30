"""
tests/unit/test_morning_report_fallback.py — регресс на инцидент 2026-07-03.

A2-фикс: когда GP daily report падает, fallback-сообщение об ошибке обязано
уходить с parse_mode=None. Иначе текст ошибки, содержащий Markdown-спецсимволы
(`_build_clinical_history`), валит парсер Telegram → даже сообщение об ошибке
не доходит → партнёр получает полную тишину.

Тест мокает gp.generate_daily_report так, чтобы он кинул исключение с `_` в
тексте: оператор получает исключение, человек — jobs.morning.failed через
единственный вызов send_long с parse_mode=None.
"""
from __future__ import annotations

import json

import asyncio
import types

import i18n
import notify
import jobs.scheduled as sched
import triage_agent as _tri


def _no_block(monkeypatch):
    """Бриф с 2026-08-12 спрашивает вердикт монитора. Здесь проверяется другое —
    поэтому вердикт задаём явно, а не берём боевой logs/integrity_latest.json:
    иначе эти два теста краснели бы от критического падения в проде (§20)."""
    monkeypatch.setattr(_tri, "latest_verdict", lambda require_today=False: ({}, ""))


def test_gp_failure_falls_back_to_plain_text(monkeypatch, tg, fault_journal):
    _no_block(monkeypatch)
    operator = []
    monkeypatch.setattr(notify, "notify_operator", lambda msg: operator.append(msg) or "telegram")
    def boom(*a, **k):
        # `_` как в _build_clinical_history — под Markdown это открытие italic
        raise RuntimeError("_build_clinical_history: миграция не запускалась")

    monkeypatch.setattr(sched.gp, "generate_daily_report", boom)
    monkeypatch.setattr(sched, "refresh_data", lambda *a, **k: None)
    monkeypatch.setattr(sched, "get_chat_id", lambda: 111222333)

    calls = []

    async def fake_send_long(bot, chat_id, text, **kw):
        calls.append({"chat_id": chat_id, "text": text, "kwargs": kw})

    monkeypatch.setattr(sched, "send_long", fake_send_long)

    ctx = types.SimpleNamespace(bot=tg)
    asyncio.run(sched.send_morning_report(ctx))

    assert len(calls) == 1, f"ожидали одно fallback-сообщение, получили {calls}"
    assert calls[0]["kwargs"]["parse_mode"] is None, \
        "fallback обязан идти plain-text (parse_mode=None), иначе Markdown валит отправку"
    assert calls[0]["chat_id"] == 111222333
    assert calls[0]["text"] == i18n.t("jobs.morning.failed")
    assert "_build_clinical_history" not in calls[0]["text"]
    assert "RuntimeError" not in calls[0]["text"]
    assert operator == []
    records = [json.loads(line) for line in fault_journal.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["where"] == 'scheduled.send_morning_report'
    assert "RuntimeError: _build_clinical_history: миграция не запускалась" in records[0]["text"]


def test_no_chat_id_skips_silently(monkeypatch, tg):
    monkeypatch.setattr(sched, "get_chat_id", lambda: None)
    called = []

    async def fake_send_long(*a, **k):
        called.append(a)

    monkeypatch.setattr(sched, "send_long", fake_send_long)

    ctx = types.SimpleNamespace(bot=tg)
    asyncio.run(sched.send_morning_report(ctx))
    assert called == [], "без chat_id функция должна выйти до любой отправки"
