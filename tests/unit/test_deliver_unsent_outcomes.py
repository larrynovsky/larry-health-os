"""Outbox заключений консилиума (2026-08-29).

Дефект: заключение консилиума, запущенного из дашборда, ложилось в
hypothesis_outcomes с sent_at=NULL и ждало РЕСТАРТА бота — единственным
читателем outbox был post_init. Починка: тот же outbox гоняется job'ом
каждые 5 мин. Оракул механизма — регистрация job'а; на старом коде красный
(deliver_unsent_outcomes_job не существует).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import jobs.scheduled as sched


@pytest.mark.unit
def test_register_adds_deliver_unsent_outcomes_repeating_job():
    jq  = MagicMock()
    app = SimpleNamespace(job_queue=jq)
    sched.register(app)

    matched = [
        c for c in jq.run_repeating.call_args_list
        if c.kwargs.get("name") == "deliver_unsent_outcomes"
        and c.args and c.args[0] is sched.deliver_unsent_outcomes_job
    ]
    assert matched, (
        f"deliver_unsent_outcomes не зарегистрирован: "
        f"{[c.kwargs.get('name') for c in jq.run_repeating.call_args_list]}"
    )
    assert matched[0].kwargs.get("interval") == 300


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)


@pytest.mark.unit
def test_job_delivers_and_marks_sent(monkeypatch):
    """Строка с coordinator_text → send_long → mark_sent; повторный вызов при
    пустом outbox ничего не шлёт."""
    outbox = [{"id": 23, "memory_id": 868, "verdict": "partial", "confidence": 0.7,
               "evaluated_at": "2026-08-29", "coordinator_text": "текст заключения"}]
    marked_delivering, marked_sent, sent_long = [], [], []

    monkeypatch.setattr(sched, "get_chat_id", lambda: 123)
    monkeypatch.setattr(sched.db, "get_unsent_hypothesis_outcomes", lambda: list(outbox))
    monkeypatch.setattr(sched.db, "mark_hypothesis_outcome_delivering",
                        lambda mid, chat, oid=None: marked_delivering.append((mid, chat, oid)))
    monkeypatch.setattr(sched.db, "mark_hypothesis_outcome_sent",
                        lambda mid, oid=None: (marked_sent.append((mid, oid)), outbox.clear()))

    async def _fake_send_long(bot, chat_id, text):
        sent_long.append((chat_id, text))
    monkeypatch.setattr(sched, "send_long", _fake_send_long)

    app = SimpleNamespace(bot=_FakeBot())
    ctx = SimpleNamespace(application=app, bot=app.bot)

    asyncio.run(sched.deliver_unsent_outcomes_job(ctx))
    assert marked_delivering == [(868, 123, 23)]
    assert marked_sent == [(868, 23)]
    assert len(sent_long) == 1 and "#868" in sent_long[0][1] and "текст заключения" in sent_long[0][1]
    assert "Восстановлено" not in sent_long[0][1]

    asyncio.run(sched.deliver_unsent_outcomes_job(ctx))
    assert marked_sent == [(868, 23)] and len(sent_long) == 1


@pytest.mark.unit
def test_job_send_failure_does_not_mark_sent(monkeypatch):
    """send_long упал → sent_at не ставится: строка остаётся в outbox (не теряется)."""
    outbox = [{"memory_id": 5, "verdict": "partial", "confidence": 0.7,
               "evaluated_at": "2026-08-29", "coordinator_text": "t"}]
    marked_sent = []
    monkeypatch.setattr(sched, "get_chat_id", lambda: 123)
    monkeypatch.setattr(sched.db, "get_unsent_hypothesis_outcomes", lambda: list(outbox))
    monkeypatch.setattr(sched.db, "mark_hypothesis_outcome_delivering", lambda mid, chat, oid=None: None)
    monkeypatch.setattr(sched.db, "mark_hypothesis_outcome_sent", lambda mid, oid=None: marked_sent.append(mid))

    async def _boom(bot, chat_id, text):
        raise RuntimeError("telegram down")
    monkeypatch.setattr(sched, "send_long", _boom)

    app = SimpleNamespace(bot=_FakeBot())
    asyncio.run(sched.deliver_unsent_outcomes_job(SimpleNamespace(application=app, bot=app.bot)))
    assert marked_sent == []


@pytest.mark.unit
def test_mark_sent_by_outcome_id_marks_exact_row(db):
    """Две outcome одной гипотезы: метка по outcome_id гасит СТАРШУЮ строку,
    метка без id (как раньше) — только MAX(id). На старом коде первый assert красный:
    строка 1 оставалась sent_at=NULL и уходила в Telegram каждые 5 мин."""
    import health_db
    health_db._ensure_hypothesis_outcomes()
    with health_db.get_conn() as conn:
        for _ in range(2):
            conn.execute("INSERT INTO hypothesis_outcomes (memory_id, verdict, coordinator_text) "
                         "VALUES (868, 'partial', 't')")
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM hypothesis_outcomes WHERE memory_id=868 ORDER BY id")]
    older, newer = ids
    health_db.mark_hypothesis_outcome_sent(868, older)
    unsent = {r["id"] for r in health_db.get_unsent_hypothesis_outcomes()}
    assert older not in unsent and newer in unsent
    health_db.mark_hypothesis_outcome_sent(868)          # без id → MAX(id)
    assert not {older, newer} & {r["id"] for r in health_db.get_unsent_hypothesis_outcomes()}
