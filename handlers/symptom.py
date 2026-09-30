"""handlers/symptom.py — visual symptom-intake ConversationHandler (WP4).

Вход: фото С ПОДПИСЬЮ, описывающей телесную проблему. Роутинг:
  - нет подписи → не наш кейс, делегируем существующему handle_photo (еда/упаковка);
  - есть подпись → классификатор интента (haiku): симптом? Двусмысленность → ДА
    (безопасная сторона: лишний диалог дешевле пропущенного симптома);
  - симптом → elicitation-диалог (symptom_intake.step), фото через media_intake,
    кейс в visual_db; сходимость/тупик → finalize_to_specialist → живой врач.

State в context.user_data + снапшот в visual_case.session_json (restart-safe).
Регистрируется в bot/main ТОЛЬКО при флаге symptom_intake_enabled, ДО handlers.messages.
"""
from __future__ import annotations

import asyncio
import base64
import logging

import i18n
import notify

from telegram import Update
from telegram.ext import (
    CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters,
)

log = logging.getLogger(__name__)

ELICIT = 1


def _classify_symptom(caption: str) -> bool:
    """Описывает ли подпись телесную проблему/симптом (в отличие от еды/упаковки/документа)?
    Двусмысленность или сбой → True (безопасная сторона: не пропустить симптом)."""
    import hai_core
    try:
        client = hai_core.get_client()
        r = client.messages.create(
            model=hai_core.get_model("haiku"), max_tokens=5,
            system="Отвечай одним словом: да или нет.",
            messages=[{"role": "user", "content":
                "Описывает ли это сообщение телесную проблему, симптом или жалобу на здоровье "
                "(в отличие от фото еды, упаковки, документа, пейзажа)? "
                f"Сообщение: «{caption}»"}],
        )
        ans = (next((b.text for b in r.content if b.type == "text"), "") or "").strip().lower()
        return not ans.startswith("нет")     # всё, кроме явного «нет», → симптом
    except Exception as e:
        log.warning(f"symptom classify fail → safe-side symptom: {e}")
        return True


async def _delegate_to_generic_photo(update, context):
    """Не симптом — отдаём существующему обработчику фото (еда/упаковка)."""
    import handlers.messages as _m
    await _m.handle_photo(update, context)


async def _photo_bytes(update, context) -> bytes:
    photo = update.message.photo[-1]
    tg = await context.bot.get_file(photo.file_id)
    return bytes(await tg.download_as_bytearray())


MODE_KEY = "lab_photo_intake_mode"          # off | shadow | on (system_config тенанта)
TRIAGE_LOG = "LAB_PHOTO_TRIAGE"             # стабильный префикс: по нему собирается сводка


def _receipts() -> dict:
    """Квитанция по классу. Ключи берём из doc_triage, а не переписываем строками —
    иначе метки завели бы здесь ещё один дом и разъехались бы молча."""
    import doc_triage as _dt
    return {
        _dt.LAB: i18n.t("documents.reply.photo_labs_received"),
        _dt.REPORT: i18n.t("documents.reply.photo_report_received"),
    }


def _intake_mode() -> str:
    """off (дефолт) | shadow | on. Сбой чтения конфига → off: включать приём
    втихую из-за недоступной БД хуже, чем не включить."""
    try:
        import config_db as _cfg
        return str(_cfg.get_config(MODE_KEY, "off") or "off").strip().lower()
    except Exception as e:
        log.warning(f"{MODE_KEY} не прочитан ({e}) → off")
        return "off"


async def _maybe_intake_document(update, context, caption: str) -> bool:
    """True — кадр принят как меддокумент и обработан здесь; False — обычный путь.

    shadow всегда возвращает False: классификация происходит и попадает в лог,
    маршрут не меняется. Это единственный способ померить классификатор, не
    включив его в бой.
    """
    mode = await asyncio.to_thread(_intake_mode)
    if mode not in ("shadow", "on"):
        return False
    try:
        data = await _photo_bytes(update, context)
    except Exception as e:
        log.error(f"{TRIAGE_LOG} фото не скачано ({e}) — обычный путь")
        return False

    import doc_intake
    r = await asyncio.to_thread(doc_intake.intake, data, "image/jpeg", None, mode == "on")
    log.info("%s mode=%s form=%s modality=%s fallback=%s stored=%s dup=%s err=%r raw=%r caption=%r",
             TRIAGE_LOG, mode, r["label"], r["modality"], r["fallback"], r["stored"],
             r["is_duplicate"], r["error"][:60], r["raw"][:40], caption[:60])

    if mode == "shadow" or not r["stored"]:
        return False                       # наблюдение, personal или отказ хранилища
    text = _receipts().get(r["label"], i18n.t("documents.reply.photo_document_saved"))
    if r["is_duplicate"]:
        text = i18n.t("documents.reply.photo_duplicate")
    await _delegate_to_generic_photo(update, context)   # разбор в чат остаётся мгновенным
    await update.message.reply_text(text)
    return True


async def on_photo_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # этап2: ждём свежее фото по открытому кейсу? Тогда это follow-up (подпись необязательна).
    import visual_db as _vdb_fu
    try:
        _awaiting = _vdb_fu.get_awaiting_followup_case(update.effective_chat.id)
    except Exception:  # silent-ok: сбой чтения кейса не должен ломать приём фото
        _awaiting = None
    if _awaiting:
        return await _handle_followup_photo(update, context, _awaiting)

    caption = (update.message.caption or "").strip()

    # ПОРЯДОК ЗДЕСЬ — ЗАЩИТА, а не стиль (решение владельца 2026-07-28).
    # Жалоба человека главнее суждения модели о кадре: если подпись описывает
    # телесную проблему, симптом-диалог выигрывает у ЛЮБОЙ метки классификатора.
    # Иначе ошибка классификации в сторону документа отвечала бы «сохранил
    # заключение» человеку, который написал «третий день болит, не проходит».
    # Приём меддокументов решается по кадру до проверки подписи:
    # документ без caption тоже должен попадать в маршрут приёма.
    symptom_claimed = bool(caption) and await asyncio.to_thread(_classify_symptom, caption)

    if not symptom_claimed:
        if await _maybe_intake_document(update, context, caption):
            return ConversationHandler.END
        await _delegate_to_generic_photo(update, context)
        return ConversationHandler.END

    await update.message.chat.send_action("typing")
    try:
        data = await _photo_bytes(update, context)
    except Exception as e:
        log.error(f"symptom photo download: {e}")
        await update.message.reply_text(await asyncio.to_thread(
                notify.fault, f"handlers/symptom.py:on_photo_entry: {type(e).__name__}: {e}", person_key="photos.error.download_failed"))
        return ConversationHandler.END

    import os
    import media_intake
    import visual_db
    import symptom_intake as si
    try:
        stored = media_intake.sanitize_and_store(data)
    except media_intake.ImageRejected:
        await update.message.reply_text(
            i18n.t("photos.error.unsupported_image"))
        return ConversationHandler.END

    chat_id = update.effective_chat.id
    tenant = os.environ.get("HEALTH_TENANT") or None
    case_id = visual_db.open_visual_case(chat_id, tenant)
    visual_db.add_visual_photo(case_id, stored["path"], stored["sha256"], stored["exif_stripped"])
    state = si.new_state(case_id)
    state["image_path"] = stored["path"]   # фото видно модели КАЖДЫЙ ход (не слепнет)
    b64 = base64.standard_b64encode(data).decode()
    state, action = await asyncio.to_thread(si.step, state, caption, b64)
    return await _handle_action(update, context, state, action)


async def on_photo_during_elicit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Фото прислано ПОСРЕДИ диалога (в state ELICIT) — не отдаём старому handle_photo.
    Обновляем фото кейса, показываем модели, продолжаем разбор."""
    import media_intake
    import visual_db
    import symptom_intake as si
    state = context.user_data.get("symptom_state")
    if not state:
        # состояние потеряно — трактуем как новый вход
        return await on_photo_entry(update, context)
    await update.message.chat.send_action("typing")
    try:
        data = await _photo_bytes(update, context)
        stored = media_intake.sanitize_and_store(data)
    except media_intake.ImageRejected:
        await update.message.reply_text(i18n.t("photos.error.image_unreadable"))
        return ELICIT
    except Exception as e:
        log.error(f"elicit photo download: {e}")
        await update.message.reply_text(await asyncio.to_thread(
                notify.fault, f"handlers/symptom.py:on_photo_during_elicit: {type(e).__name__}: {e}", person_key="photos.error.download_failed"))
        return ELICIT
    try:
        visual_db.add_visual_photo(state["case_id"], stored["path"], stored["sha256"],
                                   stored["exif_stripped"])
    except Exception as e:
        log.warning(f"elicit add photo: {e}")
    state["image_path"] = stored["path"]
    caption = (update.message.caption or "Прислал новое фото — смотри.").strip()
    b64 = base64.standard_b64encode(data).decode()
    state, action = await asyncio.to_thread(si.step, state, caption, b64)
    return await _handle_action(update, context, state, action)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import json as _j
    import symptom_intake as si
    import visual_db
    text = (update.message.text or "").strip()
    if not text:
        return ELICIT
    state = context.user_data.get("symptom_state")
    if not state:
        case = visual_db.get_open_visual_case(update.effective_chat.id)
        if case and case.get("session_json"):
            try:
                state = _j.loads(case["session_json"])
            except Exception:
                state = None
        if not state:
            await update.message.reply_text(
                i18n.t("symptoms.error.session_not_found"))
            return ConversationHandler.END
    await update.message.chat.send_action("typing")
    state, action = await asyncio.to_thread(si.step, state, text)
    return await _handle_action(update, context, state, action)


def _render_verdict(action: dict) -> str:
    """Читаемый финальный разбор для показа врачу (НЕ диагноз): что видно + версии + что различит."""
    contour = action.get("hypothesis") or action.get("contour") or {}
    alive = [c for c in (action.get("candidates") or []) if (c or {}).get("status") != "excluded"]
    if action.get("type") == "handoff":
        lines = [i18n.t("symptoms.reply.handoff")]
    else:
        lines = [i18n.t("symptoms.reply.uncertain")]
    if (contour.get("observation") or "").strip():
        lines.append(i18n.t("symptoms.reply.observation_heading") + contour["observation"].strip())
    if alive:
        lines.append(i18n.t("symptoms.reply.candidates_heading"))
        for c in alive:
            lab = (c.get("label") or "").strip()
            gr = (c.get("grounding") or "").strip()
            lines.append(i18n.t("symptoms.reply.candidate", lab=lab) + (i18n.t(
                "symptoms.reply.grounding", gr=gr
            ) if gr else ""))
    if (contour.get("test") or "").strip():
        lines.append(i18n.t("symptoms.reply.discriminators_heading") + contour["test"].strip())
    lines.append(i18n.t("symptoms.reply.doctor"))
    return "\n".join(lines)


async def _handle_action(update, context, state, action):
    import symptom_intake as si
    import visual_db
    context.user_data["symptom_state"] = state
    try:
        visual_db.update_visual_case_session(state["case_id"], state)   # снапшот для restart
    except Exception as e:
        log.warning(f"symptom session snapshot: {e}")

    t = action["type"]
    if t == "question":
        await update.message.reply_text(action["text"])
        return ELICIT
    if t in ("handoff", "escalate"):
        # Запись в БД (для /visit-агрегата и истории) — но вердикт шлём ВСЕГДА, даже если запись сорвётся.
        try:
            await asyncio.to_thread(si.finalize_to_specialist, state["case_id"], action)
        except Exception as e:
            log.error(f"symptom finalize (вердикт всё равно отправлю): {e}")
        context.user_data.pop("symptom_state", None)
        verdict = _render_verdict(action)
        from bot.utils import send_long
        try:
            await send_long(update.message.get_bot(), update.effective_chat.id, verdict, parse_mode=None)
        except Exception as e:
            log.warning(f"verdict send failed, plain: {e}")
            await update.message.reply_text(verdict[:3900])
        return ConversationHandler.END
    # error
    await update.message.reply_text(await asyncio.to_thread(
                notify.fault, f"handlers/symptom.py:_handle_action: analysis failed case_id={state.get('case_id')}", person_key="symptoms.error.analysis_failed"))
    return ELICIT


async def on_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("symptom_state", None)
    await update.message.reply_text(i18n.t("symptoms.reply.stopped"))
    return ConversationHandler.END


def register(app, owner_filter):
    """ДО handlers.messages. Гейт флага — в bot/main (регистрируем только если ON)."""
    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.PHOTO & owner_filter, on_photo_entry)],
        states={ELICIT: [
            MessageHandler(filters.PHOTO & owner_filter, on_photo_during_elicit),
            MessageHandler(filters.TEXT & ~filters.COMMAND & owner_filter, on_text),
        ]},
        fallbacks=[CommandHandler("cancel", on_cancel, filters=owner_filter)],
        conversation_timeout=1800,
        name="symptom_intake",
        persistent=False,
    )
    app.add_handler(conv)


async def _handle_followup_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, case: dict):
    """этап2: свежее фото по кейсу, ждущему follow-up. Привязка к серии, vision-сравнение
    с прошлым снимком, запись динамики в гипотезу, кейс → снова handed_off."""
    import media_intake
    import visual_db
    import symptom_intake as si
    import hai_hypotheses as hh
    await update.message.chat.send_action("typing")
    try:
        data = await _photo_bytes(update, context)
        stored = media_intake.sanitize_and_store(data)
    except media_intake.ImageRejected:
        await update.message.reply_text(i18n.t("photos.error.image_unreadable"))
        return ConversationHandler.END
    except Exception as e:
        log.error(f"followup photo download: {e}")
        await update.message.reply_text(await asyncio.to_thread(
                notify.fault, f"handlers/symptom.py:_handle_followup_photo: {type(e).__name__}: {e}", person_key="photos.error.download_failed"))
        return ConversationHandler.END

    cid = case["id"]
    pid = visual_db.add_visual_photo(cid, stored["path"], stored["sha256"], stored["exif_stripped"])
    if pid is None:
        await update.message.reply_text(i18n.t("symptoms.reply.duplicate_photo"))
        return ConversationHandler.END

    # предыдущий снимок серии (самый свежий, кроме только что добавленного)
    old_bytes = None
    for p in visual_db.get_case_photos(cid):
        if p["sha256"] != stored["sha256"]:
            try:
                with open(p["path"], "rb") as fh:
                    old_bytes = fh.read()
            except Exception:  # silent-ok: старый файл недоступен — сравнение пропустим
                old_bytes = None

    caption = (update.message.caption or "свежее фото").strip()
    note = None
    if old_bytes:
        try:
            note = await asyncio.to_thread(
                si.compare_series, old_bytes, data, case.get("region"), caption)
        except Exception as e:
            log.warning(f"compare_series: {e}")

    mid = case.get("hypothesis_memory_id")
    if note and mid:
        try:
            await asyncio.to_thread(hh.append_dynamics_to_hypothesis, mid, note)
        except Exception as e:
            log.warning(f"append_dynamics: {e}")

    visual_db.resume_case_after_followup(cid)
    if note:
        await update.message.reply_text(
            i18n.t("symptoms.reply.comparison_saved", note=note))
    else:
        await update.message.reply_text(i18n.t("symptoms.reply.followup_photo_saved"))
    return ConversationHandler.END
