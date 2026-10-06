#!/usr/bin/env python3.11
"""assessment_bot_handlers — callback handlers для Telegram-бота.

Чтобы не раздувать telegram_bot.py, всё что касается inline-callbacks для
assessment/hypothesis/constitution_conflict вынесено сюда.

Регистрация в telegram_bot.py main():
    import assessment_bot_handlers as abh
    application.add_handler(CallbackQueryHandler(abh.cb_router, pattern="^cb_(a|hyp|cc)"))

Расширение handle_text:
    active = abh.get_active_assessment(chat_id)
    if active:
        await abh.handle_text_in_assessment(update, context, active)
        return  # не передаём дальше в Claude
"""
from __future__ import annotations
from _time_inject import get_today  # seam

import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import asyncio
import i18n
from _fmt_helpers import fmt_count
import notify
import assessment_dialog as ad
import health_db as db
from bot import actions

log = logging.getLogger(__name__)

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
except ImportError:
    InlineKeyboardButton = None
    InlineKeyboardMarkup = None
    KeyboardButton = None
    ReplyKeyboardMarkup = None
    ReplyKeyboardRemove = None

SKIP_LABEL = "Пропустить"


def _skip_labels() -> set[str]:
    """Надпись кнопки «пропустить» на любом языке: человек мог сменить язык посреди знакомства."""
    return {SKIP_LABEL} | {i18n.t("common.button.skip", lang) for lang in i18n.LANGS}


def _kb_to_markup(keyboard):
    """Конвертирует list[list[(label, data)]] → InlineKeyboardMarkup.

    Вопрос-геопозиция (маркер ad.LOCATION_BUTTON) — reply-клавиатура: только она умеет
    request_location; inline и reply в одном сообщении не смешиваются, поэтому «пропустить»
    там — текстовая кнопка, её ловит маршрутизация текста."""
    if not keyboard or InlineKeyboardButton is None:
        return None
    if any(data == ad.LOCATION_BUTTON for row in keyboard for _, data in row):
        rows = [[KeyboardButton(label, request_location=(data == ad.LOCATION_BUTTON))
                 if data == ad.LOCATION_BUTTON else KeyboardButton(i18n.t("common.button.skip"))
                 for label, data in row] for row in keyboard]
        return ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True)
    rows = []
    for row in keyboard:
        rows.append([InlineKeyboardButton(label, callback_data=data) for label, data in row])
    return InlineKeyboardMarkup(rows)


ONBOARDING_ID = "onboarding"


async def _drop_reply_keyboard(chat) -> None:
    """Снять reply-клавиатуру вопроса-геопозиции. Telegram держит её внизу чата, пока её явно не
    уберут: inline-разметка следующего сообщения и reply_markup=None её НЕ снимают. После первого
    прохождения под чатом висели «📍 Отправить место» и «Пропустить» — бот будто спрашивал место,
    а «Пропустить» уходило в обычный чат (видно по conversation_history).
    Снять можно только сообщением с ReplyKeyboardRemove; оно служебное — сразу удаляется."""
    if ReplyKeyboardRemove is None:
        return
    m = await chat.send_message("…", reply_markup=ReplyKeyboardRemove())
    try:
        await m.delete()
    except Exception as e:  # silent-ok: клавиатура уже снята, осталось лишь «…» в чате
        log.warning(f"drop_reply_keyboard: служебное сообщение не удалено: {e}")


async def _send_answer(update, context, session: dict, reply: str, kb, done: bool) -> None:
    """Ответ опроса человеку + действия после знакомства: пересчёт расписания отчёта по новому
    времени/дому и первый утренний отчёт сразу (урок: «видимый результат в конце»)."""
    markup = None if done else _kb_to_markup(kb)
    if done and session.get("instrument_id") == ONBOARDING_ID:
        markup = actions.keyboard([actions.button(i18n.t("actions.onboarding.redo"), "ob_redo", "")])
    answered = ad.current_item(session)          # пункт, на который только что ответили
    if (answered or {}).get("kind") == "location" and not isinstance(markup, ReplyKeyboardMarkup or ()):
        await _drop_reply_keyboard(update.effective_chat)
    await update.effective_chat.send_message(reply, reply_markup=markup)
    if done and session.get("instrument_id") == ONBOARDING_ID and context is not None:
        try:
            import jobs.scheduled as js
            js._apply_morning_schedule(context.application)
            context.application.job_queue.run_once(js.send_morning_report, when=15,
                                                   name="onboarding_first_brief")
            await update.effective_chat.send_message(i18n.t("onboarding.reply.preparing_first_brief"))
        except Exception as e:  # noqa: BLE001 — знакомство записано; отчёт придёт по расписанию
            log.error(f"after onboarding: {e}")


def start_onboarding(chat_id: int):
    """Знакомство: задача-опросник в ЕДИНОМ доме вопросов (tasks, single_home_of_questions),
    помеченная доставленной — outbox опросников не пришлёт её второй раз. Продолжает
    прерванную сессию, если она есть."""
    active = ad.db.get_active_assessment_session(chat_id, instrument_id=ONBOARDING_ID)
    if active and active.get("task_id"):
        return ad.start(chat_id, int(active["task_id"]))
    tid = db.save_task(source="onboarding", type_="assessment", content=i18n.t("assessment.name.onboarding"), priority="high")
    with db.get_conn() as conn:
        conn.execute("UPDATE tasks SET fingerprint=? WHERE id=?", (f"assessment:{ONBOARDING_ID}", tid))
    db.mark_task_sent(tid)
    return ad.start(chat_id, tid)


def get_active_assessment(chat_id):
    """Прокси для удобства в handle_text routing."""
    return ad.get_active(chat_id)


# ── CallbackQueryHandler router ────────────────────────────────────────────

async def cb_router(update, context):
    """Маршрутизатор cb_a* callbacks (cb_hyp/cb_cc сняты 28.09.2026 — см. ниже).

    SX-17 / UC-I-02: CallbackQueryHandler не принимает filters=, поэтому
    owner-check встроен inline (fail-closed, по паттерну callback_doc_review).
    Любой посторонний chat → молчаливый return.
    """
    # SX-17 inline owner-check (UC-I-02 fail-closed):
    # F2 (2026-07-17): резолв недоступен → _owner=None → БЛОКИРУЕМ всех (fail-CLOSED).
    # Прежний код `if OWNER_CHAT_ID is not None and ...` при None короткозамыкался
    # в fail-OPEN (пускал не-владельца). owner_chat_id() сам fail-closed (raise),
    # но ловим на случай отсутствия секрета в тестовом окружении.
    from bot.filters import owner_chat_id
    try:
        _owner = owner_chat_id()
    except Exception:  # секрет недоступен → блокируем всех
        _owner = None
    if _owner is None or update.effective_chat is None or update.effective_chat.id != _owner:
        return
    q = update.callback_query
    data = q.data or ""
    chat_id = q.message.chat.id if q.message else update.effective_chat.id

    try:
        await q.answer()  # ACK Telegram
    except Exception:  # silent-ok: ACK иногда failед без последствий
        pass

    if data.startswith("cb_aa:"):
        await _cb_assessment_answer(update, context, data, chat_id)
    elif data.startswith("cb_as:"):
        await _cb_assessment_action(update, context, data, chat_id)
    else:
        log.warning(f"unknown callback data: {data[:40]}")


async def _cb_assessment_action(update, context, data, chat_id):
    """cb_as:<action>:<task_id> — action by user on assessment task."""
    parts = data.split(":")
    if len(parts) < 3:
        return
    action, task_id = parts[1], int(parts[2])

    if action == "n":  # now
        text, kb, sid = await asyncio.to_thread(ad.start, chat_id, task_id)
        markup = _kb_to_markup(kb)
        await update.effective_chat.send_message(text, reply_markup=markup)
    elif action in ("s3", "s7"):  # snooze
        days = 3 if action == "s3" else 7
        from datetime import date, timedelta
        new_deadline = (get_today() + timedelta(days=days)).isoformat()
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE tasks SET status='snoozed', deadline=? WHERE id=?",
                (new_deadline, task_id),
            )
        active = ad.get_active(chat_id)
        if active and active.get("task_id") == task_id:
            ad.abandon(active["id"], reason="snoozed")
        await update.callback_query.edit_message_reply_markup(reply_markup=None)
        await update.effective_chat.send_message(i18n.t("cards.assessment.snoozed", days=fmt_count(days, "days")))
    elif action == "x":  # skip
        try:
            db.resolve_task(task_id, resolved_text="не актуально", status="dismissed")
        except Exception as e:
            log.warning(f"dismiss task failed: {e}")
        await update.effective_chat.send_message(i18n.t("cards.assessment.skipped"))


async def _cb_assessment_answer(update, context, data, chat_id):
    """cb_aa:<item_id>:<value> — answer in assessment dialog."""
    parts = data.split(":")
    if len(parts) < 3:
        return
    item_id, value_str = parts[1], parts[2]
    try:
        value = int(value_str)
    except ValueError:
        return

    active = ad.get_active(chat_id)
    if not active:
        await update.effective_chat.send_message(i18n.t("cards.assessment.no_active_session"))
        return
    text, kb, done = await asyncio.to_thread(ad.answer, active["id"], item_id, value)
    await _send_answer(update, context, active, text, kb, done)


# ⚰ 28.09.2026: _cb_hypothesis_action (cb_hyp:) и _cb_constitution_conflict_action (cb_cc:) сняты —
# кнопок с этими данными никто не слал. Гипотезы получили кнопки партии 5 (handlers/hypotheses.py,
# act:hc/hr/hq); конфликтов с конституцией больше нет (survivorship_curator, решение владельца).
# Устаревшая кнопка cb_* уходит в «unknown callback data» и лог, человеку — ничего.


ASIDE_SEC = 120   # системная механика: столько после ответа бота на файл текст считается репликой о файле


def _aside(update, context, item: dict) -> bool:
    """Текст — реплика о другом, а не ответ на пункт опроса (нить lab-intake-retry, 05.10).

    Живой случай: бот ответил на файл «уже получал — пропускаю дубль», человек написал «это не
    дубль», и знакомство, ждавшее ответа про здоровье, записало «это не дубль» проблемой со
    здоровьем. Два признака: ответ (reply) на сообщение бота, которое не этот вопрос; или текст
    пришёл вскоре после ответа бота на присланный файл (метку ставит handle_document)."""
    msg = getattr(update, "message", None)
    quoted = getattr(getattr(msg, "reply_to_message", None), "text", None)
    question = i18n.pick(item.get("text", "")) or ""
    if quoted and question and question not in quoted:
        return True
    import time
    data = getattr(context, "chat_data", None)
    # pop: метка гасит ОДНУ реплику — повторённый ответ на вопрос уже принимается
    at = data.pop("doc_reply_at", None) if isinstance(data, dict) else None
    return bool(at) and time.time() - at < ASIDE_SEC


async def handle_text_in_assessment(update, context, active_session):
    """Свободный текст при активной сессии.

    Пункт, на который отвечают словами (text/date/number), принимает текст как ответ; «Пропустить»
    на необязательном пункте — пропуск. До 2026-09-23 текст всегда отбивался напоминанием
    «нажми кнопку» — у опросника знакомства на текстовые вопросы нельзя было бы ответить вообще."""
    item = ad.current_item(active_session)
    text = (update.message.text or "").strip() if update.message else ""
    if item and (item.get("kind", "scale") in ("text", "date", "number")
                 or (item.get("optional") and text in _skip_labels())):
        # Вопрос человека посреди знакомства — не ответ (ключевой момент плана): «что с моим
        # сном?» не должно записаться лекарством. Свободный рассказ о здоровье — исключение.
        if text.endswith("?") and item.get("target") != "problems":
            await update.effective_chat.send_message(i18n.t(
                "onboarding.reply.question_deferred", question=i18n.pick(item.get("text", ""))),
                reply_markup=actions.keyboard([
                    actions.button(i18n.t("actions.onboarding.stop"), "ob_stop", "")]))
            return
        if _aside(update, context, item):
            await update.effective_chat.send_message(i18n.t(
                "onboarding.reply.aside_deferred", question=i18n.pick(item.get("text", ""))),
                reply_markup=actions.keyboard([
                    actions.button(i18n.t("actions.onboarding.stop"), "ob_stop", "")]))
            return
        value = -1 if (item.get("optional") and text in _skip_labels()) else text
        reply, kb, done = await asyncio.to_thread(ad.answer, active_session["id"], item["id"], value)
        await _send_answer(update, context, active_session, reply, kb, done)
        return
    instrument_id = active_session.get("instrument_id", "?")
    await update.effective_chat.send_message(
        i18n.t("cards.assessment.active", instrument_id=instrument_id),
        reply_markup=actions.keyboard([InlineKeyboardButton(
            i18n.t("cards.assessment.snooze_3", days=fmt_count(3, "days")), callback_data=f"cb_as:s3:{active_session['task_id']}")])
    )


async def handle_location_in_assessment(update, context, active_session) -> bool:
    """Геопозиция при активной сессии, если текущий пункт её ждёт. True — принято опросом."""
    item = ad.current_item(active_session)
    if not item or item.get("kind") != "location":
        return False
    loc = update.message.location
    reply, kb, done = await asyncio.to_thread(ad.answer, active_session["id"], item["id"], [loc.latitude, loc.longitude])
    await _send_answer(update, context, active_session, reply, kb, done)
    return True


def build_assessment_task_keyboard(task_id):
    """Inline-кнопки для assessment-задачи в утреннем отчёте."""
    if InlineKeyboardButton is None:
        return None
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(i18n.t("cards.assessment.fill"), callback_data=f"cb_as:n:{task_id}"),
            InlineKeyboardButton(i18n.t("cards.assessment.snooze_3", days=fmt_count(3, "days")), callback_data=f"cb_as:s3:{task_id}"),
        ],
        [
            InlineKeyboardButton(i18n.t("cards.assessment.snooze_7", days=fmt_count(7, "days")), callback_data=f"cb_as:s7:{task_id}"),
            InlineKeyboardButton(i18n.t("common.button.skip"), callback_data=f"cb_as:x:{task_id}"),
        ],
    ])
