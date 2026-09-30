"""
bot.utils.send_long — plain-text fallback на ошибку разбора Markdown.

Инцидент 2026-07-03: catch-up владельца сгенерировал отчёт с несбалансированной
Markdown-сущностью (offset 3090) → BadRequest → сообщение НЕ доставлено вообще.
A2 дал fallback только тексту ошибки; здесь — основному каналу доставки.
"""
from __future__ import annotations

import asyncio

import pytest
from telegram.error import BadRequest

from bot.utils import send_long

pytestmark = pytest.mark.unit


class _Bot:
    def __init__(self, fail_markdown=True):
        self.calls = []
        self.fail_markdown = fail_markdown

    async def send_message(self, chat_id, text, parse_mode=None):
        self.calls.append({"text": text, "parse_mode": parse_mode})
        if self.fail_markdown and parse_mode is not None:
            raise BadRequest(
                "Can't parse entities: can't find end of the entity "
                "starting at byte offset 3090")


def test_short_message_falls_back_to_plain():
    bot = _Bot()
    txt = "текст с _незакрытым курсивом"
    asyncio.run(send_long(bot, 42, txt))
    assert len(bot.calls) == 2, "ожидали Markdown-попытку + plain-повтор"
    assert bot.calls[0]["parse_mode"] is not None
    assert bot.calls[1]["parse_mode"] is None
    assert bot.calls[1]["text"] == txt, "plain-повтор должен нести тот же текст"


def test_success_no_fallback():
    bot = _Bot(fail_markdown=False)
    asyncio.run(send_long(bot, 42, "чистый текст"))
    assert len(bot.calls) == 1, "успешная отправка не должна повторяться"


def test_non_parse_badrequest_reraised():
    class _Bot2:
        async def send_message(self, chat_id, text, parse_mode=None):
            raise BadRequest("chat not found")  # НЕ parse-ошибка

    with pytest.raises(BadRequest):
        asyncio.run(send_long(_Bot2(), 42, "x"))


class _MsgBot:
    """Мок, возвращающий Message с инкрементным message_id."""
    def __init__(self):
        self.calls = []
        self._n = 100

    async def send_message(self, chat_id, text, parse_mode=None):
        self.calls.append({"text": text, "parse_mode": parse_mode})
        self._n += 1
        m = type("M", (), {})()
        m.message_id = self._n
        return m


def test_send_long_returns_message_ids():
    bot = _MsgBot()
    ids = asyncio.run(send_long(bot, 42, "короткий текст"))
    assert ids == [101], "send_long должен вернуть receipt (message_id) от Telegram"


def test_receipt_none_safe():
    # мок возвращает None (как некоторые тесты/пути) — getattr не должен падать
    bot = _Bot(fail_markdown=False)
    ids = asyncio.run(send_long(bot, 42, "текст"))
    assert ids == [None], "None-возврат не должен ронять, receipt=None"


def test_long_message_returns_multiple_ids():
    bot = _MsgBot()
    ids = asyncio.run(send_long(bot, 42, "абзац\n" * 2000))
    assert len(ids) >= 2 and all(isinstance(i, int) for i in ids)


def test_long_message_each_chunk_gets_fallback():
    bot = _Bot()
    long = "абзац _плохой\n" * 700  # заведомо >4000 символов
    asyncio.run(send_long(bot, 42, long))
    md = [c for c in bot.calls if c["parse_mode"] is not None]
    plain = [c for c in bot.calls if c["parse_mode"] is None]
    assert len(md) == len(plain) and len(plain) >= 2, \
        "каждый кусок должен получить plain-повтор при parse-ошибке"


def test_send_md_retries_plain_and_keeps_markup():
    """send_md (2026-09-25): любой метод отправки — reply_text, edit_message_text,
    send_message с клавиатурой — при parse-ошибке повторяется тем же текстом без
    разметки, и клавиатура не теряется (иначе карточка ревью придёт без кнопок)."""
    from bot.utils import send_md
    calls = []

    async def reply_text(text, parse_mode=None, reply_markup=None):
        calls.append((text, parse_mode, reply_markup))
        if parse_mode is not None:
            raise BadRequest("Can't parse entities: can't find end of the entity")
        return "ok"

    got = asyncio.run(send_md(reply_text, text="Ферритин_new *", reply_markup="KB"))
    assert got == "ok"
    assert calls == [("Ферритин_new *", "Markdown", "KB"),
                     ("Ферритин_new *", None, "KB")]


def test_send_md_does_not_swallow_other_errors():
    from bot.utils import send_md

    async def reply_text(text, parse_mode=None):
        raise BadRequest("Chat not found")

    with pytest.raises(BadRequest):
        asyncio.run(send_md(reply_text, text="x"))


def test_send_md_writes_receipt_without_body(caplog):
    """BL-PUB-15 (г): у send_md есть квитанция доставки, как у send_long; тела в логе нет."""
    import logging
    from bot.utils import send_md

    class _Msg:
        message_id = 777

        class chat:
            id = 42

    async def reply_text(text, parse_mode=None):
        return _Msg()

    with caplog.at_level(logging.INFO, logger="bot.utils"):
        asyncio.run(send_md(reply_text, text="секрет_тела"))
    rec = [r.getMessage() for r in caplog.records if "SEND_RECEIPT" in r.getMessage()]
    assert rec and "message_id=777" in rec[0] and "chat=42" in rec[0] and "via=reply_text" in rec[0]
    assert "секрет_тела" not in rec[0]
