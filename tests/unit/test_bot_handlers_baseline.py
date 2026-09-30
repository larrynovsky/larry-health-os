"""
tests/unit/test_bot_handlers_baseline.py — behavioral baseline для handler-ов
перед Sprint 6 декомпозицией telegram_bot.py.

Цель: зафиксировать поведение нескольких атомарных handler-ов (cmd_done,
cmd_dismiss, cmd_tasks) до начала переноса в handlers/*. Если после переноса
эти тесты падают — мы поведенчески сломали handler.

После Sprint 6 эти baseline-тесты остаются жить как guard.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_context(args: list[str] | None = None):
    """Минимальный telegram.ext.ContextTypes.DEFAULT_TYPE-подобный."""
    return SimpleNamespace(args=args or [])


def _attach_reply(upd, tg):
    """
    Прикрепить async reply_text к update.message — оборачивает в tg.send_message,
    чтобы вывод попадал в tg.outgoing.
    """
    chat_id = upd.message.chat.id

    async def reply_text(text, **kwargs):
        return await tg.send_message(chat_id=chat_id, text=text, **kwargs)

    upd.message.reply_text = reply_text
    return upd


# ── Tests: cmd_done ──────────────────────────────────────────────────────────

def test_cmd_done_no_args_returns_hint(tg):
    """/done без аргументов → подсказка."""
    from handlers.tasks import cmd_done
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/done", chat_id=owner_chat_id()), tg)
    ctx = _make_context(args=[])

    asyncio.run(cmd_done(upd, ctx))

    assert len(tg.outgoing) == 1
    assert "Выбери задачу кнопкой" in tg.outgoing[0]["text"]


def test_cmd_done_invalid_id_returns_error(tg):
    """/done abc → подсказка выбрать задачу кнопкой (партия 7: номера — кнопками)."""
    from handlers.tasks import cmd_done
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/done abc", chat_id=owner_chat_id()), tg)
    ctx = _make_context(args=["abc"])

    asyncio.run(cmd_done(upd, ctx))

    assert len(tg.outgoing) == 1
    assert "Выбери задачу и действие кнопкой" in tg.outgoing[0]["text"]


def test_cmd_done_resolves_task_via_db(tg):
    """/done 42 → вызывает health_db.resolve_task(42, ..., 'completed')."""
    from handlers.tasks import cmd_done
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/done 42", chat_id=owner_chat_id()), tg)
    ctx = _make_context(args=["42"])

    with patch("handlers.tasks.db.resolve_task", return_value=True) as mock_resolve, \
         patch("handlers.tasks.ta.complete_macos_reminder", return_value=None) as mock_rem:
        asyncio.run(cmd_done(upd, ctx))

    assert mock_resolve.called
    call_args = mock_resolve.call_args.args
    assert call_args[0] == 42
    assert call_args[2] == "completed"
    assert mock_rem.called

    assert any("закрыта" in m["text"].lower() for m in tg.outgoing)


def test_cmd_done_resolve_returns_false_says_not_found(tg):
    """Если resolve_task вернул False → 'не найдена'."""
    from handlers.tasks import cmd_done
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/done 999", chat_id=owner_chat_id()), tg)
    ctx = _make_context(args=["999"])

    with patch("handlers.tasks.db.resolve_task", return_value=False), \
         patch("handlers.tasks.ta.complete_macos_reminder", return_value=None):
        asyncio.run(cmd_done(upd, ctx))

    assert any("не найдена" in m["text"].lower() for m in tg.outgoing)


# ── Tests: cmd_dismiss ───────────────────────────────────────────────────────

def test_cmd_dismiss_no_args(tg):
    from handlers.tasks import cmd_dismiss
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/dismiss", chat_id=owner_chat_id()), tg)
    ctx = _make_context(args=[])

    asyncio.run(cmd_dismiss(upd, ctx))

    assert len(tg.outgoing) == 1
    assert "под нужной задачей" in tg.outgoing[0]["text"]


def test_cmd_dismiss_resolves_as_dismissed(tg):
    """/dismiss 5 → resolve_task(5, 'dismissed by user', 'dismissed')."""
    from handlers.tasks import cmd_dismiss
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/dismiss 5", chat_id=owner_chat_id()), tg)
    ctx = _make_context(args=["5"])

    with patch("handlers.tasks.db.resolve_task", return_value=True) as mock_resolve, \
         patch("handlers.tasks.ta.complete_macos_reminder", return_value=None):
        asyncio.run(cmd_dismiss(upd, ctx))

    assert mock_resolve.called
    call_args = mock_resolve.call_args.args
    assert call_args[0] == 5
    assert call_args[2] == "dismissed"
    assert any("отклонена" in m["text"].lower() for m in tg.outgoing)


# ── Tests: cmd_tasks ─────────────────────────────────────────────────────────

def test_cmd_tasks_empty_list(tg):
    """/tasks без открытых задач → 'Нет открытых задач'."""
    from handlers.tasks import cmd_tasks
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/tasks", chat_id=owner_chat_id()), tg)
    ctx = _make_context(args=[])

    with patch("handlers.tasks.db.get_open_tasks", return_value=[]):
        asyncio.run(cmd_tasks(upd, ctx))

    assert len(tg.outgoing) == 1
    assert "Нет открытых задач" in tg.outgoing[0]["text"]


# ── Tests: _owner_filter (defensive — уже покрыт в test_telegram_fixture) ────

def test_owner_filter_blocks_strangers(tg):
    """Защита от регрессии: _owner_filter блокирует чужие чаты."""
    from telegram_bot import _owner_filter, owner_chat_id

    upd_owner = tg.make_update(text="/done 1", chat_id=owner_chat_id())
    upd_stranger = tg.make_update(text="/done 1", chat_id=owner_chat_id() + 12345)

    assert tg.filter_passes(_owner_filter(), upd_owner) is True
    assert tg.filter_passes(_owner_filter(), upd_stranger) is False
