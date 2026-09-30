"""
handlers/callbacks.py — CallbackQueryHandler-ы:
- callback_doc_review — обработка inline-кнопок выбора типа документа
- cb_router_with_owner_check — owner-check + делегация в abh.cb_router

CallbackQueryHandler не принимает filters=, поэтому owner-check встроен
в каждый callback inline (fail-closed, UC-I-02). Любой посторонний → silent return.

Вынесено из telegram_bot.py в Sprint 6 (C8b, 2026-05-23).
"""
from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes

import assessment_bot_handlers as abh
import health_db as db
import i18n
import import_all as doc_import

from bot.filters import owner_chat_id
from bot.utils import send_md

log = logging.getLogger(__name__)

# Ключи отображения подтверждённого типа; переводим при обработке запроса.
# (Полный набор LABELS живёт в telegram_bot._send_doc_review_message — переедет в jobs на C10.)
_DOC_TYPE_DISPLAY = {
    "lab":          "documents.type.lab",
    "oncology":     "documents.type.oncology",
    "consultation": "documents.type.consultation",
    "discharge":    "documents.type.discharge",
    "other":        "documents.type.other",
    "skip":         "documents.type.skipped",
}


async def _edit_card(query, as_caption: bool, text: str) -> None:
    """Правит карточку тем методом, что ей подходит: подпись документа или текст."""
    if as_caption:
        await send_md(query.edit_message_caption, caption=text)
    else:
        await send_md(query.edit_message_text, text=text)


async def callback_doc_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает нажатие inline-кнопки выбора типа документа.

    BUG-DOCREV-OWNER-FILTER (2026-05-12): CallbackQueryHandler не принимает
    filters=, поэтому owner-check встроен inline. Паттерн UC-I-02 fail-closed:
    сверяем effective_chat.id с owner_chat_id() (как делает фильтр `owner`
    через filters.Chat). Любой посторонний → молчаливый return без
    раскрытия структуры callback_data.
    """
    if update.effective_chat is None or update.effective_chat.id != owner_chat_id():
        return
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_", 2)  # docrev_{id}_{type}
    if len(parts) != 3 or parts[0] != "docrev":
        return

    _, rid_str, doc_type = parts
    try:
        rid = int(rid_str)
    except ValueError:
        return

    # Получаем source_file до подтверждения (нужен для apply_confirmed_type)
    source_file: str | None = None
    try:
        with db.get_conn() as _conn:
            row = _conn.execute(
                "SELECT source_file FROM pending_doc_reviews WHERE id = ?", (rid,)
            ).fetchone()
            if row:
                source_file = row[0]
    except Exception as _e:
        log.warning(f"callback_doc_review: не удалось получить source_file id={rid}: {_e}")
    # Имя — из записи, не из текста сообщения: карточка с приложенным файлом приходит
    # документом с подписью, у неё message.text = None, и до 28.09 обработчик падал здесь,
    # не подтвердив тип (подтверждала только автоматика через 48 ч).
    fname = source_file.split("/")[-1] if source_file else "?"
    as_caption = query.message is not None and getattr(query.message, "text", None) is None

    if doc_type == "skip":
        db.reject_doc_review(rid)
        await _edit_card(query, as_caption, i18n.t("documents.reply.skipped", fname=fname))
        log.info(f"callback_doc_review: id={rid} → rejected (skip)")
        return

    db.confirm_doc_review(rid, doc_type)

    # Применяем подтверждённый тип к JSON-файлам и в consultations
    if source_file:
        try:
            await asyncio.to_thread(doc_import.apply_confirmed_type, source_file, doc_type)
        except Exception as _e:
            log.error(f"callback_doc_review: apply_confirmed_type failed: {_e}")

    label = i18n.t(_DOC_TYPE_DISPLAY[doc_type]) if doc_type in _DOC_TYPE_DISPLAY else doc_type
    await _edit_card(query, as_caption, i18n.t("documents.reply.type_confirmed", label=label, fname=fname))
    log.info(f"callback_doc_review: id={rid} → {doc_type}")


async def callback_field_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает inline-кнопки маппинга неизвестных маркеров анализов.

    Форматы callback_data:
      fieldrev_{id}_ok_{canonical}  — подтвердить с предложенным именем
      fieldrev_{id}_skip            — пропустить (reject)
    """
    if update.effective_chat is None or update.effective_chat.id != owner_chat_id():
        return
    query = update.callback_query
    await query.answer()

    data = query.data  # fieldrev_{id}_ok_{canonical} OR fieldrev_{id}_skip
    parts = data.split("_", 3)   # ['fieldrev', id, 'ok'/'skip', canonical?]
    if len(parts) < 3 or parts[0] != "fieldrev":
        return
    try:
        rid = int(parts[1])
    except ValueError:
        return

    action = parts[2]

    if action == "skip":
        db.resolve_field_review(rid, canonical=None)
        await query.edit_message_text(i18n.t("labs.reply.marker_skipped"))
        log.info(f"callback_field_review: id={rid} → rejected")
        return

    if action == "ok" and len(parts) == 4:
        canonical = parts[3]
        db.resolve_field_review(rid, canonical=canonical)
        await send_md(query.edit_message_text, text=i18n.t("labs.reply.mapping_saved", canonical=canonical))
        log.info(f"callback_field_review: id={rid} → confirmed as '{canonical}'")
        return

    await query.edit_message_text(i18n.t("common.error.unknown_action"))


async def callback_treatment_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Человек-гейт лечения: подтвердить/отклонить извлечённый онкорежим.

    Форматы callback_data:
      medrev_{id}_ok    — подтвердить (войдёт в историю лечения)
      medrev_{id}_skip  — отклонить
    UC-I-02 fail-closed: owner-check inline.
    """
    if update.effective_chat is None or update.effective_chat.id != owner_chat_id():
        return
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_", 2)  # ['medrev', id, 'ok'/'skip']
    if len(parts) < 3 or parts[0] != "medrev":
        return
    try:
        mid = int(parts[1])
    except ValueError:
        return
    action = parts[2]

    if action == "ok":
        db.set_medication_confirmation(mid, "confirmed")
        await query.edit_message_text(i18n.t("medications.reply.regimen_confirmed"))
        log.info(f"callback_treatment_review: id={mid} → confirmed")
    elif action == "skip":
        db.set_medication_confirmation(mid, "rejected")
        await query.edit_message_text(i18n.t("medications.reply.regimen_rejected"))
        log.info(f"callback_treatment_review: id={mid} → rejected")
    else:
        await query.edit_message_text(i18n.t("common.error.unknown_action"))


async def callback_mem_consolidation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Человек-гейт консолидации памяти (A): применить/отклонить SUPERSEDE.
      memcons_{id}_ok   — пометить факт устаревшим (valid_to, обратимо)
      memcons_{id}_skip — оставить как есть
    Owner-check inline (CallbackQueryHandler не принимает filters=)."""
    if update.effective_chat is None or update.effective_chat.id != owner_chat_id():
        return
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_", 2)  # ['memcons', id, 'ok'/'skip']
    if len(parts) < 3 or parts[0] != "memcons":
        return
    try:
        pid = int(parts[1])
    except ValueError:
        return
    import memory_consolidation as mc
    if parts[2] == "ok":
        ok = mc.apply_supersede(pid)
        await query.edit_message_text(
            i18n.t("memory.reply.fact_superseded") if ok else i18n.t("memory.error.already_outdated"))
        log.info(f"callback_mem_consolidation: id={pid} → applied={ok}")
    elif parts[2] == "skip":
        mc.reject(pid)
        await query.edit_message_text(i18n.t("memory.reply.unchanged"))
        log.info(f"callback_mem_consolidation: id={pid} → rejected")
    else:
        await query.edit_message_text(i18n.t("common.error.unknown_action"))


async def callback_visual_followup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """этап2: решение по follow-up кейсу серии.
      visfu_{id}_continue — продолжить наблюдение (снова напоминание прислать фото)
      visfu_{id}_close    — закрыть кейс
    UC-I-02 fail-closed: owner-check inline (CallbackQueryHandler не принимает filters=)."""
    if update.effective_chat is None or update.effective_chat.id != owner_chat_id():
        return
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_", 2)  # ['visfu', id, 'continue'/'close']
    if len(parts) < 3 or parts[0] != "visfu":
        return
    try:
        cid = int(parts[1])
    except ValueError:
        return
    import visual_db as _vdb
    action = parts[2]
    if action == "close":
        _vdb.close_visual_case(cid)
        await query.edit_message_text(i18n.t("symptoms.reply.case_closed"))
        log.info(f"callback_visual_followup: {cid} → closed")
    elif action == "continue":
        _vdb.mark_followup_reminded(cid)   # снова awaiting_followup, таймер решения перевзведён
        await query.edit_message_text(
            i18n.t("symptoms.reply.observation_continues"))
        log.info(f"callback_visual_followup: {cid} → continue")
    else:
        await query.edit_message_text(i18n.t("common.error.unknown_action"))


async def cb_router_with_owner_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """SX-17 (UC-I-02 fail-closed): inline owner_chat_id() check перед делегацией в abh.cb_router.

    CallbackQueryHandler не принимает filters=, поэтому owner-check встроен здесь.
    Любой посторонний chat → молчаливый return.
    """
    if update.effective_chat is None or update.effective_chat.id != owner_chat_id():
        return
    await abh.cb_router(update, context)


def register(app, owner_filter=None):
    """Регистрирует CallbackQueryHandler-ы. owner_filter не используется
    (CallbackQueryHandler не поддерживает filters=, owner-check внутри функций)."""
    app.add_handler(CallbackQueryHandler(callback_doc_review,  pattern=r"^docrev_"))
    app.add_handler(CallbackQueryHandler(callback_field_review, pattern=r"^fieldrev_"))
    app.add_handler(CallbackQueryHandler(callback_treatment_review, pattern=r"^medrev_"))
    app.add_handler(CallbackQueryHandler(callback_mem_consolidation, pattern=r"^memcons_"))
    app.add_handler(CallbackQueryHandler(callback_visual_followup, pattern=r"^visfu_"))
    app.add_handler(CallbackQueryHandler(cb_router_with_owner_check, pattern=r"^cb_(a|hyp|cc)"))
