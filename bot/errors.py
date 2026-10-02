"""
bot/errors.py — глобальный error_handler для PTB Application.

Вынесено из telegram_bot.py в Sprint 6 (C1, 2026-05-23).
"""
from __future__ import annotations

import logging

from telegram.ext import ContextTypes

log = logging.getLogger(__name__)


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Логирует все ошибки бота, не даёт им падать молча."""
    from telegram.error import Forbidden, BadRequest
    err = context.error
    if isinstance(err, Forbidden):
        log.warning(f"Bot blocked or chat unavailable: {err}. Нужно /start от пользователя.")
    elif isinstance(err, BadRequest) and "Message is too long" in str(err):
        log.error("Message too long — используй send_long()")
    else:
        log.error(f"Telegram error: {err}", exc_info=context.error)
        await _tell_person(update, err)


async def _tell_person(update, err) -> None:
    """Необработанное исключение в команде/кнопке: человеку — строка, оператору — детали.

    До 28.09 такие ошибки только логировались: человек нажимал кнопку или слал
    команду и не получал ничего (холодное чтение сообщений, решение владельца).
    """
    import asyncio
    import hai_core
    import notify
    from bot.filters import owner_chat_id
    chat = getattr(update, "effective_chat", None)
    if chat is None or chat.id != owner_chat_id():
        return
    person_key = ("person.llm.role_not_admitted" if isinstance(err, hai_core.ModelNotAdmitted)
                  else "common.error.our_side")
    try:
        text = await asyncio.to_thread(
            notify.fault, f"необработанная ошибка бота: {type(err).__name__}: {err}",
            person_key=person_key)
        await update.get_bot().send_message(chat_id=chat.id, text=text)
    except Exception as e:  # silent-ok: сообщить о сбое не удалось — лог уже есть выше
        log.warning(f"_tell_person: не удалось сообщить о сбое: {e}")
