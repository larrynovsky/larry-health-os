"""
handlers/consult.py — MDT консультация: /consult ConversationHandler
+ cmd_consult_continue + cmd_consult_end + cmd_consult_timeout.

ConvHandler без persistence (persistent=False) — state в context.user_data
живёт только в памяти процесса, после restart bot теряется (восстанавливается
из db.load_consultation_session при следующем сообщении).

ВАЖНО: ConvHandler регистрируется в bot/main.py ДО handlers.messages,
иначе текст в state CONSULTING перехватится handle_text.

Вынесено из telegram_bot.py в Sprint 6 (C11, 2026-05-23).
"""
from __future__ import annotations

import asyncio
import logging
import traceback as _tb
from datetime import date, timedelta
from _time_inject import get_today  # seam

import i18n
import notify

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters,
)

import health_db as db

from bot.filters import owner_chat_id
from bot import actions
from bot.helpers import refresh_data
from bot.utils import send_long, send_md

log = logging.getLogger(__name__)

# ConversationHandler state
CONSULTING = 1
ASKING = 2


async def cmd_consult(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """MDT консультация — начало диалоговой сессии (13 участников: 9 врачей + 4 lifestyle-коуча)."""
    raw = update.message.text or ""
    user_message = (
        raw.split(None, 1)[1].strip()
        if len(raw.split(None, 1)) > 1
        else "Общая консультация по текущему состоянию."
    )
    return await _start_consult(update.message, context, user_message)


async def _start_consult(message, context, user_message):
    await message.chat.send_action("typing")
    ack = (
        i18n.t("consult.reply.starting")
    )
    await send_md(message.reply_text, text=ack)
    await asyncio.to_thread(refresh_data)

    import wellally_consult as mdt
    from wellally_consult import ConsultationSession

    session = ConsultationSession()
    try:
        target = get_today() - timedelta(days=1)
        response, session = await mdt.run_consultation_cycle_async(
            user_message=user_message,
            session=session,
            end_date=target,
            period_days=7,
        )
        context.user_data["consult_session"] = session
        db.save_consultation_session(message.chat_id, session.to_dict())
        report = i18n.t("consult.reply.report_heading") + response
        report += i18n.t("consult.help.continue_or_end")
        await send_long(message.get_bot(), message.chat_id, report,
                        reply_markup=consult_keyboard("cs_end"))
        return CONSULTING
    except Exception as e:
        log.error(f"cmd_consult ОШИБКА: {type(e).__name__}: {e}\n{_tb.format_exc()}")
        await message.reply_text(await asyncio.to_thread(
                notify.fault, f"handlers/consult.py:cmd_consult: {type(e).__name__}: {e}"))
        return ConversationHandler.END


async def cmd_consult_continue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Продолжение диалога консилиума — обрабатывает ответ пациента."""
    user_message = (update.message.text or "").strip()
    if not user_message:
        return CONSULTING
    await update.message.chat.send_action("typing")
    session = context.user_data.get("consult_session")
    if not session:
        # Попытка восстановить после перезапуска бота
        import wellally_consult as _mdt_restore
        saved = db.load_consultation_session(update.effective_chat.id)
        if saved:
            try:
                session = _mdt_restore.ConsultationSession.from_dict(saved)
                context.user_data["consult_session"] = session
                log.info(f"cmd_consult_continue: сессия восстановлена из БД (раундов: {len(session.rounds)})")
            except Exception as _e:
                log.warning(f"cmd_consult_continue: не удалось восстановить сессию: {_e}")
                session = None
    if not session:
        await update.message.reply_text(i18n.t("consult.error.session_not_found"),
                                        reply_markup=consult_keyboard("cs_new"))
        return ConversationHandler.END
    await update.message.reply_text(i18n.t("consult.reply.next_round"))
    await asyncio.to_thread(refresh_data)
    import wellally_consult as mdt
    try:
        target = get_today() - timedelta(days=1)
        response, session = await mdt.run_consultation_cycle_async(
            user_message=user_message,
            session=session,
            end_date=target,
            period_days=7,
        )
        context.user_data["consult_session"] = session
        db.save_consultation_session(update.effective_chat.id, session.to_dict())
        report = i18n.t("consult.reply.round_heading", round_count=len(session.rounds)) + response
        report += i18n.t("consult.help.end")
        await send_long(update.message.get_bot(), update.effective_chat.id, report,
                        reply_markup=consult_keyboard("cs_end"))
        return CONSULTING
    except Exception as e:
        log.error(f"cmd_consult_continue ОШИБКА: {type(e).__name__}: {e}\n{_tb.format_exc()}")
        await update.message.reply_text(await asyncio.to_thread(
                notify.fault, f"handlers/consult.py:cmd_consult_continue: {type(e).__name__}: {e}"))
        return CONSULTING


async def cmd_consult_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершение диалоговой консультации."""
    return await _end_consult(update.message, context)


async def _end_consult(message, context):
    session = context.user_data.pop("consult_session", None)
    rounds = len(session.rounds) if session else 0
    db.delete_consultation_session(message.chat_id)
    await message.reply_text(
        i18n.t("consult.reply.completed", rounds=rounds), reply_markup=consult_keyboard("cs_new")
    )
    return ConversationHandler.END


async def cmd_consult_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Срабатывает когда /consult сессия простаивает дольше conversation_timeout (30 мин).
    PTB v20 передаёт синтетический update — reply_text недоступен, используем bot.send_message."""
    context.user_data.pop("consult_session", None)  # очищаем зависшую сессию
    db.delete_consultation_session(owner_chat_id())
    try:
        await context.bot.send_message(
            chat_id=owner_chat_id(),
            text=(
                i18n.t("consult.reply.timed_out")
            ), reply_markup=consult_keyboard("cs_new"),
        )
    except Exception as e:
        log.warning(f"cmd_consult_timeout: не удалось отправить уведомление: {e}")
    return ConversationHandler.END



def consult_keyboard(verb):
    key = "actions.consult.end" if verb == "cs_end" else "actions.consult.new"
    return actions.keyboard([actions.button(i18n.t(key), verb, "")])


@actions.action("cs_end")
async def end_consult(query, context, target):
    state = await _end_consult(query.message, context)
    await actions.remove_target(query, target)
    return state


@actions.action("cs_new")
async def new_consult(query, context, target):
    await actions.ask(context.bot, query.message.chat_id, i18n.t("consult.prompt.question"), "cs_new", "")
    await actions.remove_target(query, target)
    return ASKING


@actions.on_reply("cs_new")
async def consult_question(message, context, target, text):
    return await _start_consult(message, context, text)


class _ConsultReply(filters.MessageFilter):
    def filter(self, message):
        return actions.reply_verb(message) == "cs_new"


async def _consult_reply(update, context):
    if await actions.handle_reply(update.message, context):
        return CONSULTING if context.user_data.get("consult_session") else ConversationHandler.END
    if context.user_data.get("consult_session"):
        return await cmd_consult_continue(update, context)
    from handlers.messages import handle_text
    await handle_text(update, context)
    return ASKING


def register(app, owner_filter):
    """Регистрирует /consult ConversationHandler.

    Должен вызываться ДО handlers.messages чтобы текст в state CONSULTING
    шёл в cmd_consult_continue, а не в handle_text.
    """
    consult_conv = ConversationHandler(
        entry_points=[
            CommandHandler("consult", cmd_consult, filters=owner_filter),
            CallbackQueryHandler(actions.handle_callback, pattern=r"^act:cs_(end|new):"),
            MessageHandler(owner_filter & filters.TEXT & ~filters.COMMAND & filters.REPLY & _ConsultReply(),
                           _consult_reply),
        ],
        states={
            ASKING: [MessageHandler(owner_filter & filters.TEXT & ~filters.COMMAND, _consult_reply)],
            CONSULTING: [
                MessageHandler(owner_filter & filters.TEXT & ~filters.COMMAND & filters.REPLY, _consult_reply),
                MessageHandler(filters.TEXT & ~filters.COMMAND & owner_filter, cmd_consult_continue),
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, cmd_consult_timeout),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(actions.handle_callback, pattern=r"^act:cs_(end|new):"),
            CommandHandler("end", cmd_consult_end, filters=owner_filter),
        ],
        conversation_timeout=1800,
        name="consultation",
        persistent=False,
    )
    app.add_handler(consult_conv)
