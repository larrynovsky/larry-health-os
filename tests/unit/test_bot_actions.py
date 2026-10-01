"""Кнопки вместо команд и технические сбои — оператору (решения владельца 28.09).

Оракулы:
(1) нажатие кнопки act:<verb>:<target> доходит до зарегистрированного действия с target;
(2) чужой чат — тишина; неизвестное действие — человеку «кнопка устарела», не молчание;
(3) ask() → реплай на это сообщение доходит до @on_reply(verb) с текстом и
    переживает «перезапуск» (квитанция в базе), чужой реплай — не наш;
(4) callback_data длиннее 64 байт — отказ сразу, а не молчаливая немая кнопка;
(5) notify.fault: детали — оператору, человеку — строка без деталей.
"""
from __future__ import annotations

import json

import asyncio
from types import SimpleNamespace

import pytest

import bot.actions as actions


def _cb_update(chat_id, data, sent):
    async def answer():
        return None

    async def reply_text(text, **kw):
        sent.append(text)

    query = SimpleNamespace(data=data, answer=answer,
                            message=SimpleNamespace(reply_text=reply_text))
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), callback_query=query)


@pytest.mark.unit
def test_button_press_reaches_registered_action(monkeypatch):
    import bot.filters
    monkeypatch.setattr(bot.filters, "owner_chat_id", lambda: 7)
    got = []

    @actions.action("t_ok")
    async def _handler(query, context, target):
        got.append(target)

    sent = []
    asyncio.run(actions.handle_callback(_cb_update(7, "act:t_ok:57", sent), None))
    assert got == ["57"] and sent == []

    asyncio.run(actions.handle_callback(_cb_update(8, "act:t_ok:58", sent), None))
    assert got == ["57"]                                    # чужой чат — ничего

    asyncio.run(actions.handle_callback(_cb_update(7, "act:nope:1", sent), None))
    assert len(sent) == 1                                   # не молчим


@pytest.mark.unit
def test_callback_data_limit():
    assert actions.callback_data("pa", 57) == "act:pa:57"
    with pytest.raises(ValueError):
        actions.callback_data("x", "я" * 40)


@pytest.mark.unit
def test_ask_reply_roundtrip(db, monkeypatch):
    got = []

    @actions.on_reply("t_reply")
    async def _on(message, context, target, text):
        got.append((target, text))

    class _Bot:
        async def send_message(self, chat_id, text, reply_markup=None):
            return SimpleNamespace(message_id=100)

    mid = asyncio.run(actions.ask(_Bot(), 7, "Причина?", "t_reply", 412))
    assert mid == 100

    def _msg(reply_id, text):
        async def reply_text(*a, **kw):
            return None
        return SimpleNamespace(chat_id=7, text=text, reply_text=reply_text,
                               reply_to_message=SimpleNamespace(message_id=reply_id))

    assert asyncio.run(actions.handle_reply(_msg(99, "не то"), None)) is False
    assert asyncio.run(actions.handle_reply(_msg(100, " сделал "), None)) is True
    assert got == [("412", "сделал")]
    assert asyncio.run(actions.handle_reply(_msg(100, "ещё раз"), None)) is False


@pytest.mark.unit
def test_fault_splits_detail_from_person(monkeypatch, fault_journal):
    import notify
    to_operator = []
    monkeypatch.setattr(notify, "notify_operator", lambda m, fallback=True: to_operator.append(m))
    person = notify.fault("Error code: 529 overloaded")
    assert to_operator == []
    records = [json.loads(line) for line in fault_journal.read_text().splitlines()]
    assert len(records) == 1 and records[0]["where"] == "Error code"
    assert "529" in records[0]["text"]
    assert "529" not in person and person
    assert notify.fault("тихо", person_key=None) is None


@pytest.mark.unit
def test_buttons_vanish_before_the_handler_runs(monkeypatch):
    """01.10: кнопка жила, пока обработчик генерировал протокол (~7 с) — второе «Подтвердить»
    завело второй протокол. Кнопки предмета снимаются ДО работы; поздний remove_target
    обработчика («message is not modified») не роняет его после сделанной работы."""
    from telegram.error import BadRequest
    import bot.filters as filters
    monkeypatch.setattr(filters, "owner_chat_id", lambda: 7)
    edits, order = [], []
    kb = actions.keyboard([actions.button("ok", "t_slow", 5), actions.button("no", "t_slow2", 5)],
                          [actions.button("other", "t_slow", 6)])

    async def answer():
        return None

    async def edit(reply_markup=None):
        if edits:
            raise BadRequest("Message is not modified")   # второй вызов в живом Telegram
        edits.append(reply_markup); order.append("removed")

    query = SimpleNamespace(data="act:t_slow:5", answer=answer, edit_message_reply_markup=edit,
                            message=SimpleNamespace(reply_markup=kb))

    @actions.action("t_slow")
    async def _slow(q, context, target):
        order.append("handler")
        await actions.remove_target(q, target)          # старый обычай — снимать в конце

    asyncio.run(actions.handle_callback(
        SimpleNamespace(effective_chat=SimpleNamespace(id=7), callback_query=query), None))
    assert order == ["removed", "handler"] and len(edits) == 1, (order, edits)
    left = [[b.callback_data for b in row] for row in edits[0].inline_keyboard]
    assert left == [["act:t_slow:6"]], left              # чужой предмет остался
