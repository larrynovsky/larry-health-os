"""Маршрут задач: дела — ассистенту, анкеты и вопросы — в бот, без ответа — одна просьба напомнить.

Решение владельца 07.11.2026: «Зачем задача в ремайндер мне, если можно прислать сразу опросник?
И ассистенту, если я не ответил, чтобы напомнил». Синтетическая БД, письма перехвачены.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import agent_mail
import agent_reply
import config_db
import health_db
import task_agent
import task_route

pytestmark = pytest.mark.unit
NOW = datetime(2040, 1, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "t.db")
    health_db.init_db()
    sent = []
    monkeypatch.setattr(agent_mail, "send",
                        lambda to, subject, body, *, kind, reply_to=None, task_ids=None: sent.append((to, subject, body, kind)) or "k")
    monkeypatch.setattr(agent_reply, "allowed_peers", lambda: {"assistant-example"})
    monkeypatch.setattr(task_route, "get_now", lambda tz=None: NOW)
    conf = {"tasks.route": "assistant"}
    monkeypatch.setattr(config_db, "get_config", lambda k, d=None, **kw: conf.get(k, d))
    return sent, conf


def test_assistant_route_hands_over_deeds_not_questionnaires(env, monkeypatch):
    sent, _ = env
    monkeypatch.setattr(task_agent, "create_macos_reminder",
                        lambda t: pytest.fail("при маршруте assistant напоминаний в телефон нет"))
    tasks = [{"id": 1, "type": "action", "content": "Узнать у клиники дату ПЭТ-КТ", "deadline": "2040-02-01"},
             {"id": 2, "type": "assessment", "content": "Анкета"},
             {"id": 3, "type": "lab_test", "content": "Сдать HbA1c", "reason": "контроль"}]
    assert task_agent.create_reminders_for_tasks(tasks) == 2
    (to, subject, body, kind), = sent
    assert to == ["assistant-example"] and kind == "task"
    assert "#1" in body and "#3" in body and "#2" not in body and "Срок: 2040-02-01" in body


def test_default_route_keeps_reminders(env, monkeypatch):
    sent, conf = env
    conf.pop("tasks.route")
    made = []
    monkeypatch.setattr(task_agent, "create_macos_reminder", lambda t: made.append(t["id"]) or True)
    assert task_agent.create_reminders_for_tasks([{"id": 7, "type": "action", "content": "x"}]) == 1
    assert made == [7] and not sent


def test_nudge_once_for_unanswered_bot_task(env):
    sent, _ = env
    old = health_db.save_task(source="t", type_="assessment", content="Пора заполнить анкету")
    fresh = health_db.save_task(source="t", type_="question", content="Как самочувствие?")
    deed = health_db.save_task(source="t", type_="action", content="Сдать кровь")
    with health_db.get_conn() as conn:
        conn.execute("UPDATE tasks SET sent_at='2040-01-07 08:00:00' WHERE id IN (?,?)", (old, deed))
        conn.execute("UPDATE tasks SET sent_at='2040-01-10 08:00:00' WHERE id=?", (fresh,))
    assert task_route.nudge() == 1
    (to, subject, body, kind), = sent
    assert kind == "nudge" and f"#{old}" in body and f"#{fresh}" not in body and f"#{deed}" not in body
    assert task_route.nudge() == 0 and len(sent) == 1   # один раз, не каждый час


def test_assistant_letter_closes_deed_only(env):
    deed = health_db.save_task(source="t", type_="action", content="Узнать дату обследования")
    survey = health_db.save_task(source="t", type_="assessment", content="Анкета")
    letter = {"peer": "assistant-example",
              "wrapped": f"<письмо>Задача #{deed}: сделано. Задача #{survey}: сделано</письмо>"}
    assert task_route.close_from_letter({**letter, "peer": "stranger"}) == []
    assert task_route.close_from_letter(letter) == [deed]
    assert health_db.get_task(deed)["status"] == "completed"
    assert health_db.get_task(survey)["status"] == "open"   # анкета — только ответом в боте


@pytest.fixture
def overdue(env, monkeypatch):
    import ast
    import asyncio
    import json
    import logging
    from pathlib import Path
    from types import SimpleNamespace
    from telegram.error import TelegramError
    import i18n
    import notify

    sent, conf = env
    health_db._migrate_agent_mail()
    delivered, faults = [], []
    async def send(bot, chat, text, **kwargs):
        delivered.append((chat, text, kwargs))
        return [123]
    monkeypatch.setattr(notify, "fault", lambda text, **kw: faults.append((text, kw)))
    monkeypatch.setattr(i18n, "lang_of", lambda profile=None: "ru")
    monkeypatch.setattr(task_agent, "get_today", lambda: NOW.date())
    tree = ast.parse(Path("jobs/scheduled.py").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "task_nudge_job")
    ns = dict(asyncio=asyncio, Path=Path, db=health_db, ta=task_agent, notify=notify,
              i18n=i18n, log=logging.getLogger(__name__), get_chat_id=lambda: 42, send_long=send, TelegramError=TelegramError)
    exec(compile(ast.Module(body=[node], type_ignores=[]), "jobs/scheduled.py", "exec",
                 flags=__import__('__future__').annotations.compiler_flag), ns)
    def deed(*, age=25, accepted=False, kind="task", status="open", type_="action", deadline="2040-01-07", handed=True):
        task_id = health_db.save_task(source="synthetic", type_=type_, content="Arrange a visit", deadline=deadline)
        with health_db.get_conn() as conn:
            conn.execute("UPDATE tasks SET status=?,created_at='2040-01-09 00:00:00' WHERE id=?", (status, task_id))
            conn.execute("INSERT INTO agent_mail(id,direction,peer,kind,subject,state,detail,created_at,updated_at) "
                         "VALUES (?,'out',?,?, 'synthetic', 'requested',?,?,?)",
                         (str(task_id), json.dumps(["assistant-example"]), kind,
                          json.dumps(dict(task_ids=[task_id] if handed else [], accepted=["assistant-example"] if accepted else [])),
                          NOW.timestamp() - age * 3600, NOW.timestamp()))
        return task_id
    def run():
        asyncio.run(ns["task_nudge_job"](SimpleNamespace(bot=object())))
    return SimpleNamespace(deed=deed, run=run, delivered=delivered, faults=faults, conf=conf, ns=ns)


def test_overdue_delivers_once_without_fault(overdue):
    task_id = overdue.deed()
    overdue.run()
    assert len(overdue.delivered) == 1
    chat, text, kwargs = overdue.delivered[0]
    assert chat == 42 and "срок прошёл, ассистент не отметил выполнение" in text
    assert text.count("Arrange a visit") == 1 and kwargs["reply_markup"]
    assert health_db.get_task(task_id)["followup_sent_at"]
    assert not overdue.faults
    overdue.run()
    assert len(overdue.delivered) == 1 and not overdue.faults


@pytest.mark.parametrize("changes", [dict(deadline="2040-01-08"), dict(deadline="2040-01-10"),
                                     dict(deadline="2040-01-11", age=90 * 24), dict(status="completed"),
                                     dict(status="dismissed"), dict(kind="nudge"),
                                     dict(type_="assessment"), dict(handed=False)])
def test_overdue_excludes_fresh_closed_and_other_domains(overdue, changes):
    overdue.deed(**changes)
    overdue.run()
    assert not overdue.delivered and not overdue.faults


def test_overdue_acceptance_does_not_hide_unfinished_task(overdue):
    overdue.deed(accepted=True)
    overdue.run()
    assert len(overdue.delivered) == 1 and not overdue.faults


@pytest.mark.parametrize("age,expected", [(14 * 24 + 1, 1), (14 * 24, 0), (13 * 24, 0)])
def test_overdue_no_deadline_uses_handover_age(overdue, age, expected):
    overdue.deed(deadline=None, age=age)
    overdue.run()
    assert len(overdue.delivered) == expected and not overdue.faults
    if expected:
        assert "срок ожидания прошёл" in overdue.delivered[0][1]


def test_overdue_reminders_route_does_not_read_mail(overdue, monkeypatch):
    overdue.conf["tasks.route"] = "reminders"
    monkeypatch.setattr(agent_mail, "task_handover_times", lambda: pytest.fail("read mail on reminders route"))
    overdue.run()
    assert not overdue.delivered and not overdue.faults


@pytest.mark.parametrize("key,value,changes", [
    ("tasks.assistant_overdue_grace_days", 0, dict(deadline="2040-01-09")),
    ("tasks.assistant_no_deadline_days", 3, dict(deadline=None, age=4 * 24))])
def test_overdue_config_override(overdue, key, value, changes):
    overdue.conf[key] = value
    overdue.deed(**changes)
    overdue.run()
    assert len(overdue.delivered) == 1


@pytest.mark.parametrize("key", ["tasks.assistant_overdue_grace_days", "tasks.assistant_no_deadline_days"])
@pytest.mark.parametrize("value", [False, -1, "24", 1.5, float("nan"), float("inf")])
def test_overdue_bad_config_is_loud(overdue, key, value):
    overdue.conf[key] = value
    overdue.deed()
    overdue.run()
    assert not overdue.delivered and len(overdue.faults) == 1


@pytest.mark.parametrize("receipt", [[], [None]])
def test_overdue_missing_receipt_retries(overdue, receipt):
    task_id = overdue.deed()
    async def missing(*args, **kw):
        return receipt
    original = overdue.ns["send_long"]
    overdue.ns["send_long"] = missing
    overdue.run()
    assert health_db.get_task(task_id)["followup_sent_at"] is None
    overdue.ns["send_long"] = original
    overdue.run()
    assert len(overdue.delivered) == 1 and health_db.get_task(task_id)["followup_sent_at"]


def test_overdue_telegram_failure_retries_and_nudge_failure_is_independent(overdue, monkeypatch):
    from telegram.error import NetworkError
    task_id = overdue.deed()
    monkeypatch.setattr(task_route, "nudge", lambda: (_ for _ in ()).throw(OSError("bridge down")))
    original = overdue.ns["send_long"]
    async def broken(*args, **kwargs):
        raise NetworkError("offline")
    overdue.ns["send_long"] = broken
    overdue.run()
    assert health_db.get_task(task_id)["followup_sent_at"] is None
    overdue.ns["send_long"] = original
    overdue.run()
    assert len(overdue.delivered) == 1


def test_overdue_all_batches_are_visible_before_marking(overdue):
    ids = [overdue.deed() for _ in range(21)]
    overdue.run()
    assert len(overdue.delivered) == 2
    assert all(health_db.get_task(i)["followup_sent_at"] for i in ids)
    keyboard = overdue.delivered[-1][2]["reply_markup"].inline_keyboard
    assert keyboard[0][0].callback_data == f"act:td:{ids[-1]}"


def test_overdue_closed_by_assistant_letter(overdue):
    task_id = overdue.deed()
    assert task_route.close_from_letter(dict(peer="assistant-example", wrapped=f"задача #{task_id}: сделано")) == [task_id]
    overdue.run()
    assert not overdue.delivered


def test_handover_preserves_exact_task_ids(env, monkeypatch):
    calls = []
    monkeypatch.setattr(agent_mail, "send", lambda *a, **kw: calls.append(kw) or "key")
    task_route.hand_over([dict(id=17, type="action", content="synthetic"),
                          dict(id=18, type="assessment", content="synthetic")])
    assert calls[0]["task_ids"] == [17]


def test_overdue_overlapping_pass_is_skipped(overdue):
    import fcntl
    from pathlib import Path
    overdue.deed()
    with (Path(health_db.DB_PATH).parent / "task_overdue.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        overdue.run()
    assert not overdue.delivered
    overdue.run()
    assert len(overdue.delivered) == 1


def test_followup_is_a_deed_with_completion_not_a_bot_answer(monkeypatch):
    """Synthetic objects only: followup remains deliverable/closable by assistant."""
    rows = {1: dict(id=1, type='followup', status='open', content='Arrange a synthetic check'),
            2: dict(id=2, type='question', status='open', content='Synthetic question?')}
    sent, closed = [], []
    monkeypatch.setattr(agent_reply, 'allowed_peers', lambda: {'example-assistant'})
    monkeypatch.setattr(config_db, 'get_config', lambda key, default=None: 'assistant')
    monkeypatch.setattr(agent_mail, 'send', lambda *args, **kw: sent.append(kw))
    monkeypatch.setattr(health_db, 'get_task', rows.get)
    monkeypatch.setattr(health_db, 'resolve_task', lambda tid, text, status: closed.append((tid, status)) or True)
    assert task_agent.create_reminders_for_tasks(list(rows.values())) == 1
    assert sent[0]['kind'] == 'task' and sent[0]['task_ids'] == [1]
    assert task_route.close_from_letter(dict(peer='example-assistant',
                                           wrapped='задача #1: сделано; задача #2: сделано')) == [1]
    assert closed == [(1, 'completed')]
