"""
tests/unit/test_handlers_meta.py — behavioral тесты для handlers.meta.

Sprint 6 C2 (2026-05-23): handlers/meta extract из telegram_bot.py.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


def _attach_reply(upd, tg):
    chat_id = upd.message.chat.id

    async def reply_text(text, **kwargs):
        return await tg.send_message(chat_id=chat_id, text=text, **kwargs)

    upd.message.reply_text = reply_text
    return upd


def _make_context(args=None):
    return SimpleNamespace(args=args or [])


def test_meta_module_exports_register():
    """handlers.meta должен иметь register(app, owner_filter)."""
    import inspect

    import handlers.meta as m

    assert hasattr(m, "register")
    sig = inspect.signature(m.register)
    assert len(sig.parameters) == 2
    # 7 функций-команд
    for fn in ("cmd_start", "cmd_experiment", "cmd_sleep", "cmd_help",
               "cmd_memory", "cmd_checkin"):  # cmd_app выпилен 2026-07-06 (TD-09)
        assert hasattr(m, fn), f"handlers.meta missing {fn}"


def test_cmd_start_works_with_read_only_secrets(tg):
    """/start ничего не пишет в каталог ключей: в Докере он смонтирован только для чтения.

    До 02.10 первая строка /start писала номер чата в этот каталог и роняла /start у каждой
    установки в Докере (OSError Errno 30); оба прежних теста подменяли ровно эту строку.
    Здесь не подменено ничего, что касается каталога: он реально без права записи.
    """
    import os
    import stat
    from handlers.meta import cmd_start
    from bot.filters import CHAT_ID_FILE, owner_chat_id

    upd = _attach_reply(tg.make_update(text="/start", chat_id=owner_chat_id()), tg)
    secrets = CHAT_ID_FILE.parent
    mode = stat.S_IMODE(secrets.stat().st_mode)
    file_mode = stat.S_IMODE(CHAT_ID_FILE.stat().st_mode)
    os.chmod(CHAT_ID_FILE, 0o400)
    os.chmod(secrets, 0o500)
    try:
        assert not os.access(secrets, os.W_OK), "стенд не воспроизводит монтирование только для чтения"
        with patch("handlers.meta.db.init_db", return_value=None), \
             patch("handlers.meta.db.get_patient_profile", return_value={"identity.name": "Т"}):
            asyncio.run(cmd_start(upd, _make_context()))
    finally:
        os.chmod(secrets, mode)
        os.chmod(CHAT_ID_FILE, file_mode)

    assert any("Привет" in m["text"] for m in tg.outgoing)


def test_cmd_help_lists_commands(tg):
    from handlers.meta import cmd_help
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/help", chat_id=owner_chat_id()), tg)
    ctx = _make_context()

    asyncio.run(cmd_help(upd, ctx))

    assert len(tg.outgoing) == 1
    text = tg.outgoing[0]["text"]
    # выбранные команды должны быть упомянуты
    for cmd in ("/report", "/sleep", "/tasks", "/checkin"):
        assert cmd in text, f"help missing {cmd}"


def test_cmd_app_is_gone():
    """TD-09 (2026-07-06): миниапп убит — cmd_app не должен вернуться молча."""
    import handlers.meta as m
    assert not hasattr(m, "cmd_app")


def test_cmd_checkin_active_says_already_running(tg):
    from handlers.meta import cmd_checkin
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/checkin", chat_id=owner_chat_id()), tg)
    ctx = _make_context()

    # active ⇒ started_at (CheckinState.start ставит оба): кнопка «Хватит на сегодня»
    # привязана к started_at, поэтому тест выставляет состояние целиком.
    from datetime import datetime
    with patch("handlers.meta.ck.checkin_state.active", True), \
         patch("handlers.meta.ck.checkin_state.started_at", datetime(2030, 1, 15, 20, 0)):
        asyncio.run(cmd_checkin(upd, ctx))

    assert any("Уже спрашиваю о твоём дне" in m["text"] for m in tg.outgoing)


@pytest.mark.parametrize("frozen, want", [("2026-01-15T12:00", "UTC-5"), ("2026-07-15T12:00", "UTC-4"),
                                          ("2026-01-15T12:00+00:00", "UTC-5"), ("2026-07-15T12:00+00:00", "UTC-4")])
def test_utc_offset_label_reads_clock_through_seam(frozen, want):
    """Смещение зоны — по подменяемым часам и именно запрошенной зоны (naive и UTC-aware часы)."""
    from zoneinfo import ZoneInfo
    import _time_inject
    from handlers import meta
    _time_inject.set_test_clock(frozen)
    try:
        assert meta._utc_offset_label(ZoneInfo("America/New_York")) == want
    finally:
        _time_inject.set_test_clock(None)
