"""
bot/utils.py — общие утилиты для отправки сообщений Telegram-ботом.

send_long — разбивает текст на части по 4000 символов и отправляет
последовательно. Используется во всех handlers/* и jobs/*.

Вынесено из telegram_bot.py в Sprint 6 (C1, 2026-05-23).
"""
from __future__ import annotations

import logging

from telegram.constants import ParseMode
from telegram.error import BadRequest

log = logging.getLogger(__name__)


async def _send_one(bot, chat_id, text, parse_mode, reply_markup=None):
    """Отправить один кусок. Возвращает message_id (или None, если недоступен).

    Fallback: при ошибке разбора Markdown-сущностей повторить plain-text
    (parse_mode=None). Несбалансированная разметка может вызвать BadRequest
    и сорвать доставку. Это единая точка отправки для handlers/jobs.

    Receipt-лог хранит подтверждённый Telegram message_id:
    успешное завершение корутины само по себе не доказывает отправку.
    Логируем message_id, chat и длину, без тела сообщения.
    Срабатывание fallback тоже логируем, чтобы видеть отказы разметки.
    """
    kw = {"reply_markup": reply_markup} if reply_markup is not None else {}
    msg, used_fallback = await _md_call(bot.send_message, parse_mode,
                                        chat_id=chat_id, text=text, **kw)
    mid = getattr(msg, "message_id", None)
    log.info("SEND_RECEIPT chat=%s message_id=%s len=%d pm=%s fallback=%s",
             chat_id, mid, len(text), parse_mode, used_fallback)
    return mid


async def _md_call(fn, parse_mode, **kw):
    """Вызвать метод Telegram с разметкой; при ошибке разбора — тот же вызов plain.

    Возвращает (результат, был_ли_fallback). Единственное место в проекте, где
    parse_mode с разметкой уходит в Telegram (сторож: test_tg_parse_mode_choke).
    """
    try:
        return await fn(parse_mode=parse_mode, **kw), False
    except BadRequest as e:
        if parse_mode is None or "parse" not in str(e).lower():
            raise
        log.warning("md fallback→plain: %s parse-ошибка: %s",
                    getattr(fn, "__name__", "?"), str(e)[:80])
        return await fn(parse_mode=None, **kw), True


async def send_md(fn, **kw):
    """Markdown-вызов любого метода отправки (send_message, reply_text,
    edit_message_text, send_document с caption) с plain-fallback.

    Зачем: текст с данными/моделью (имя теста, ответ модели, `_` в названии)
    ронял прямой вызов с parse_mode → BadRequest → сообщение не доходило.
    Пример: `await send_md(update.message.reply_text, text=answer)`.

    Квитанция (2026-09-26, BL-PUB-15 г): тот же SEND_RECEIPT, что у _send_one, —
    подтверждённый message_id, а не «корутина не упала»; тело не пишется.
    """
    msg, used_fallback = await _md_call(fn, ParseMode.MARKDOWN, **kw)
    chat = getattr(getattr(msg, "chat", None), "id", None) or kw.get("chat_id")
    body = kw.get("text") or kw.get("caption") or ""
    log.info("SEND_RECEIPT via=%s chat=%s message_id=%s len=%d fallback=%s",
             getattr(fn, "__name__", "?"), chat, getattr(msg, "message_id", None),
             len(body), used_fallback)
    return msg


async def send_long(bot, chat_id: int, text: str, parse_mode=ParseMode.MARKDOWN,
                    reply_markup=None) -> list:
    """Разбивает текст на части по 4000 символов и отправляет последовательно.
    Каждый кусок — с plain-text fallback на ошибку разбора Markdown (см. _send_one).
    Клавиатура reply_markup прикрепляется к последнему куску.
    Возвращает список message_id по кускам (receipt); элемент None = id недоступен.
    """
    limit = 4000
    ids: list = []
    if len(text) <= limit:
        ids.append(await _send_one(bot, chat_id, text, parse_mode, reply_markup))
        return ids
    # Режем по абзацам чтобы не рвать на середине строки
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            if current:
                ids.append(await _send_one(bot, chat_id, current, parse_mode))
            current = line
        else:
            current = (current + "\n" + line) if current else line
    if current:
        ids.append(await _send_one(bot, chat_id, current, parse_mode, reply_markup))
    return ids
