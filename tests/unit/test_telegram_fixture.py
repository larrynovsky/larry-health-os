"""Unit-тест на fixture `tg` (telegram_mock)."""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.unit


def test_tg_starts_empty(tg):
    assert tg.outgoing == []


def test_make_update(tg):
    upd = tg.make_update(text="/report", chat_id=42)
    assert upd.message.text == "/report"
    assert upd.effective_chat.id == 42
    assert upd.effective_user.id == 1
    assert upd.update_id == 1


def test_make_update_increments_ids(tg):
    u1 = tg.make_update(text="hi")
    u2 = tg.make_update(text="bye")
    assert u2.update_id == u1.update_id + 1


def test_capture_send_message(tg):
    asyncio.run(tg.send_message(chat_id=10, text="hello"))
    assert len(tg.outgoing) == 1
    m = tg.outgoing[0]
    assert m["type"] == "message"
    assert m["chat_id"] == 10
    assert m["text"] == "hello"


def test_capture_send_photo(tg):
    asyncio.run(tg.send_photo(chat_id=10, photo="data", caption="cap"))
    assert tg.outgoing[0]["type"] == "photo"
    assert tg.outgoing[0]["caption"] == "cap"


def test_messages_to_helper(tg):
    asyncio.run(tg.send_message(chat_id=10, text="A"))
    asyncio.run(tg.send_message(chat_id=20, text="B"))
    asyncio.run(tg.send_message(chat_id=10, text="C"))
    assert len(tg.messages_to(10)) == 2
    assert len(tg.messages_to(20)) == 1
    assert tg.texts_to(10) == ["A", "C"]


def test_reset(tg):
    asyncio.run(tg.send_message(chat_id=1, text="x"))
    tg.reset()
    assert tg.outgoing == []


def test_filter_passes_with_owner_filter(tg):
    """
    Реальная проверка инфры: применяем _owner_filter() из telegram_bot
    к фейковому update. С правильным chat_id — True; с чужим — False.
    """
    from telegram_bot import _owner_filter, owner_chat_id

    # Правильный chat
    upd_owner = tg.make_update(text="/report", chat_id=owner_chat_id())
    assert tg.filter_passes(_owner_filter(), upd_owner) is True

    # Чужой chat
    upd_stranger = tg.make_update(text="/report", chat_id=owner_chat_id() + 999)
    assert tg.filter_passes(_owner_filter(), upd_stranger) is False
