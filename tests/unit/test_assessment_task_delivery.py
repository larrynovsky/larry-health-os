"""Опросник: путь заполнения существует и единственный (2026-09-03).

Если кнопка заполнения не вызывается, задача остаётся недоставленной.
Закрытие через /done без результата не заменяет заполнение опросника:
планировщик снова создаст задачу. Ниже независимо построенный сценарий.
Оракулы: (1) job зарегистрирован; (2) доставка идемпотентна и метит по id;
(3) отказ отправки не метит; (4) /done для type='assessment' → 409, статус цел;
негативный контроль — обычная задача закрывается как раньше.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import jobs.scheduled as sched


@pytest.mark.unit
def test_register_adds_assessment_outbox_job():
    jq = MagicMock()
    sched.register(SimpleNamespace(job_queue=jq))
    matched = [c for c in jq.run_repeating.call_args_list
               if c.kwargs.get("name") == "deliver_unsent_assessment_tasks"
               and c.args and c.args[0] is sched.deliver_unsent_assessment_tasks_job]
    assert matched and matched[0].kwargs.get("interval") == 300


class _FakeBot:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    async def send_message(self, chat_id, text, **kw):
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append((chat_id, text, kw.get("reply_markup")))


def _run(monkeypatch, outbox, bot):
    marked = []
    monkeypatch.setattr(sched, "get_chat_id", lambda: 555)
    monkeypatch.setattr(sched.db, "wake_snoozed_assessment_tasks", lambda today: 0)
    monkeypatch.setattr(sched.db, "get_unsent_assessment_tasks", lambda: list(outbox))
    monkeypatch.setattr(sched.db, "mark_task_sent",
                        lambda tid: (marked.append(tid), outbox.clear()))
    import assessment_bot_handlers as abh
    monkeypatch.setattr(abh, "build_assessment_task_keyboard", lambda tid: f"KB:{tid}")
    app = SimpleNamespace(bot=bot)
    asyncio.run(sched.deliver_unsent_assessment_tasks_job(SimpleNamespace(application=app, bot=bot)))
    return marked


@pytest.mark.unit
def test_job_sends_keyboard_and_marks_by_id(monkeypatch):
    outbox = [{"id": 177, "content": "Пора заполнить опросник ISI"}]
    bot = _FakeBot()
    marked = _run(monkeypatch, outbox, bot)
    assert marked == [177]
    assert len(bot.sent) == 1
    chat, text, kb = bot.sent[0]
    assert chat == 555 and "#177" in text and "ISI" in text and kb == "KB:177"
    # повторный прогон при пустом outbox — тишина
    marked2 = _run(monkeypatch, [], bot)
    assert marked2 == [] and len(bot.sent) == 1


@pytest.mark.unit
def test_job_send_failure_keeps_task_in_outbox(monkeypatch):
    outbox = [{"id": 178, "content": "MFSI"}]
    marked = _run(monkeypatch, outbox, _FakeBot(fail=True))
    assert marked == [] and outbox, "sent_at не ставится при отказе отправки"


@pytest.mark.unit
def test_get_unsent_assessment_tasks_filters_type_and_sent(db):
    import health_db
    with health_db.get_conn() as conn:
        conn.execute("INSERT INTO tasks (source, type, priority, content, status, fingerprint) "
                     "VALUES ('assessment_scheduler','assessment','medium','isi','open','assessment:isi')")
        conn.execute("INSERT INTO tasks (source, type, priority, content, status, fingerprint) "
                     "VALUES ('gp','followup','medium','x','open','f')")
        conn.execute("INSERT INTO tasks (source, type, priority, content, status, fingerprint, sent_at) "
                     "VALUES ('assessment_scheduler','assessment','medium','pro12','open','assessment:pro12','2026-09-01')")
    rows = health_db.get_unsent_assessment_tasks()
    assert [r["content"] for r in rows] == ["isi"]
    health_db.mark_task_sent(rows[0]["id"])
    assert health_db.get_unsent_assessment_tasks() == []


# ── Замок дашборда ────────────────────────────────────────────────────────────

def _fake_tasks(monkeypatch, status, type_):
    import dashboard_routers.api_tasks as api
    writes = []
    monkeypatch.setattr(api, "_q", lambda sql, p=(): [{"status": status, "type": type_}])
    monkeypatch.setattr(api, "_w", lambda sql, p=(): writes.append(sql))
    monkeypatch.setattr(api, "_log_edit", lambda *a, **k: None)
    return api, writes


@pytest.mark.unit
def test_dashboard_done_refuses_assessment_task(monkeypatch):
    from fastapi import HTTPException
    api, writes = _fake_tasks(monkeypatch, "open", "assessment")
    with pytest.raises(HTTPException) as ei:
        api.api_task_done(177)
    assert ei.value.status_code == 409 and "Заполнить" in ei.value.detail
    assert writes == [], "статус задачи не тронут"


@pytest.mark.unit
def test_dashboard_done_still_closes_ordinary_task(monkeypatch):
    """Негативный контроль: замок узкий — обычная задача закрывается как раньше."""
    api, writes = _fake_tasks(monkeypatch, "open", "followup")
    api.api_task_done(5)
    assert len(writes) == 1 and "completed" in writes[0]


# ── «+3д / +7д» возвращают опросник (28.09.2026) ─────────────────────────────
# До фикса snoozed-задача не возвращалась никогда: outbox брал только open.

def _snoozed_task(db, deadline):
    import tasks_db
    tid = tasks_db.save_task(source="assessment_scheduler", type_="assessment",
                             content="Пора заполнить опросник ISI", priority="medium")
    tasks_db.mark_task_sent(tid)
    import health_db
    with health_db.get_conn() as conn:
        conn.execute("UPDATE tasks SET status='snoozed', deadline=? WHERE id=?", (deadline, tid))
    return tid


@pytest.mark.unit
def test_snoozed_assessment_returns_when_due_and_only_once(db):
    import tasks_db
    due = _snoozed_task(db, "2026-01-10")
    later = _snoozed_task(db, "2026-01-20")

    assert tasks_db.wake_snoozed_assessment_tasks("2026-01-10") == 1
    ids = [r["id"] for r in tasks_db.get_unsent_assessment_tasks()]
    assert due in ids and later not in ids

    tasks_db.mark_task_sent(due)          # outbox доставил — второй раз не шлёт
    assert tasks_db.wake_snoozed_assessment_tasks("2026-01-11") == 0
    assert due not in [r["id"] for r in tasks_db.get_unsent_assessment_tasks()]
