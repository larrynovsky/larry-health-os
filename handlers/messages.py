"""
handlers/messages.py — обработчики сообщений (не command-based):
handle_text — главный free-text роутер (через ai.chat) + checkin + assessment
handle_photo — анализ фото через ai.chat_with_image
handle_location — гео из Telegram → memory (текущее место)

handle_text зависит от _finalize_checkin_background и _run_arbiter_background
(private helpers пока в telegram_bot.py) — импортируется локально, чтобы
избежать циркулярного импорта.

Вынесено из telegram_bot.py в Sprint 6 (C8a, 2026-05-23).
"""
from __future__ import annotations

import asyncio
import hashlib
import json as _j
import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo as _ZI

import i18n
import notify

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

import assessment_bot_handlers as abh
import checkin_agent as ck
import hai_core
import health_ai as ai
import health_db as db

from bot.utils import send_long, send_md
from bot import actions

log = logging.getLogger(__name__)


def country_flag(country_code: str | None) -> str:
    """Флаг из ISO-кода страны (региональные индикаторы Unicode) — любой страны, без списка:
    прежний словарь из десяти стран был картой чьих-то поездок (вычитка Kimi 30.09).
    Не двухбуквенный латинский код → «📍»."""
    cc = (country_code or "").upper()
    if len(cc) == 2 and cc.isascii() and cc.isalpha():
        return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in cc)
    return "📍"

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot.helpers import (_finalize_checkin_background, _run_arbiter_background,
                             _service_trouble_background)

    chat_id = update.effective_chat.id
    text    = update.message.text.strip()

    # «Пропустить» без активного опроса может прийти с устаревшей reply-клавиатуры.
    # Telegram держит её до явного удаления. Снимаем клавиатуру и
    # идём дальше обычным маршрутом: смысл сообщения (например, пропуск чекина) не трогаем.
    if text == abh.SKIP_LABEL:
        try:
            if not abh.get_active_assessment(chat_id):
                await abh._drop_reply_keyboard(update.effective_chat)
        except Exception as _e:  # silent-ok: клавиатура останется, маршрут сообщения не страдает
            log.warning(f"handle_text: снять reply-клавиатуру не удалось: {_e}")

    # ── Field review: reply-to-message → canonical name ───────────────────
    # Если пользователь отвечает на уведомление о новом маркере анализа —
    # трактуем текст как canonical name и закрываем review без LLM.
    reply_to = getattr(update.message, 'reply_to_message', None)
    if reply_to is not None:
        # Ответ на вопрос кнопки (bot.actions.ask): квитанция в bot_prompts.
        try:
            import bot.actions as _actions
            if await _actions.handle_reply(update.message, context):
                return
        except Exception as _e:
            log.warning(f"handle_text: reply на кнопочный вопрос упал: {_e}", exc_info=True)
            await update.message.reply_text(await asyncio.to_thread(
                notify.fault, f"handlers/messages.py: button reply failed: {type(_e).__name__}: {_e}"))
            return
        try:
            review = db.get_field_review_by_tg_message(reply_to.message_id)
            if review:
                canonical = text.strip()
                db.resolve_field_review(review["id"], canonical=canonical)
                await send_md(update.message.reply_text, text=i18n.t(
                    "labs.reply.mapping_recorded", raw_name=review['raw_name'], canonical=canonical
                ))
                log.info(f"handle_text: field_review id={review['id']} → '{canonical}' via reply")
                return
        except Exception as _e:
            log.warning(f"handle_text: field_review reply check failed: {_e}")

        # ── Ответ на вопрос системы: реплай на сообщение задачи ───────────
        # Тот же образец, что выше: предмет опознан по КВИТАНЦИИ доставки
        # (tg_message_id), а не по догадке о теме текста. До 2026-09-12 ответа
        # на вопрос не существовало как события: 7 вопросов за историю, 0 ответов.
        try:
            task = db.get_task_by_tg_message(reply_to.message_id)
            if task:
                import task_agent as _ta
                ok = await asyncio.to_thread(_ta.record_answer, task["id"], text,
                                             "telegram_reply")
                if ok:
                    await update.message.reply_text(
                        i18n.t("tasks.reply.answer_recorded_from_reply", task_id=task['id'])
                    )
                else:
                    await update.message.reply_text(
                        i18n.t("tasks.error.empty_answer")
                    )
                return
        except Exception as _e:
            log.warning(f"handle_text: task reply check failed: {_e}")

    # ── Ссылка на файл в облаке или путь к файлу на диске (genome-link, 24.09) ──
    # Большой геном не пролезает в Telegram (20 МБ): человек присылает ссылку или путь.
    # Только ссылки на облака из списка — статья, упомянутая в разговоре, не скачивается.
    import link_fetch
    try:
        _req = link_fetch.parse(text)
    except link_fetch._HumanError as _e:
        await update.message.reply_text(str(_e))
        return
    except Exception as _e:
        log.warning(f"handle_text: link_fetch.parse: {_e}")
        _req = None
    if _req:
        receipt = link_fetch.enqueue(_req, _tenant_inbox())
        paused = await asyncio.to_thread(_intake_down_note, person_key="intake.link.paused")
        await update.message.reply_text(paused or receipt)
        return

    await update.message.chat.send_action("typing")

    # ── Текст без реплая при висящем вопросе ──────────────────────────────
    # R1 плана: с телефона отвечают обычным сообщением, и ответ уходил бы в общий
    # чат, а вопрос оставался открытым — человек уверен, что ответил. Спрашиваем
    # ОДИН раз и только когда вопрос ровно один: при нескольких догадка вредна.
    if reply_to is None:
        try:
            pending = [t for t in await asyncio.to_thread(db.get_open_tasks, 50)
                       if t.get("type") == "question" and t.get("tg_message_id")]
            hinted = context.user_data.get("question_hint_shown_for")
            if len(pending) == 1 and len(text) > 10 and hinted != pending[0]["id"]:
                # Подсказка ОДНА на вопрос: повтор при каждом сообщении — это и есть
                # тот шум, из-за которого каналы выключают (§13, banner-blindness).
                context.user_data["question_hint_shown_for"] = pending[0]["id"]
                await send_md(update.message.reply_text, text=i18n.t(
                    "tasks.reply.confirm_answer_target", task_id=pending[0]['id'],
                    question=(pending[0].get('content') or '')[:120]
                ), reply_to_message_id=update.message.message_id, reply_markup=actions.keyboard([
                    actions.button(i18n.t("actions.answer.yes"), "ans_yes", pending[0]["id"]),
                    actions.button(i18n.t("actions.answer.no"), "ans_no", pending[0]["id"])]))
                return
        except Exception as _e:
            log.warning(f"handle_text: pending question hint failed: {_e}")

    # ── Чекин-режим ───────────────────────────────────────────────────────
    if ck.checkin_state.active:
        ck.checkin_state.add_user(text)

        force_end = ck.checkin_state.should_force_end()

        if force_end:
            # Финализируем без лишнего вопроса
            reply = i18n.t("checkin.reply.goodnight")
            is_done = True
        else:
            try:
                reply, is_done = await asyncio.to_thread(
                    ck.continue_checkin, ck.checkin_state.conversation
                )
            except Exception as e:
                log.error(f"Checkin error: {e}", exc_info=True)
                reply = i18n.t("checkin.reply.recorded")
                is_done = True

        ck.checkin_state.add_assistant(reply)
        from handlers.meta import checkin_keyboard
        await update.message.reply_text(
            reply if is_done else reply + i18n.t("checkin.help.stop"),
            reply_markup=None if is_done else checkin_keyboard())

        if is_done:
            # Сохраняем асинхронно
            conv_snapshot = list(ck.checkin_state.conversation)
            ck.checkin_state.reset()
            asyncio.create_task(_finalize_checkin_background(conv_snapshot))
        return
    # ── Assessment-режим (опросник через inline-кнопки) ──────────────────
    try:
        _ad_active = abh.get_active_assessment(chat_id)
    except Exception as _e:  # silent-ok: assessment-state недоступен — продолжаем
        _ad_active = None
    if _ad_active:
        await abh.handle_text_in_assessment(update, context, _ad_active)
        return
    # ── Обычный чат ───────────────────────────────────────────────────────

    try:
        reply = ai.chat(text)
    except hai_core.ModelNotAdmitted:
        raise
    except Exception as e:
        log.error(f"Claude API error: {e}")
        reply = await asyncio.to_thread(
                notify.fault, f"handlers/messages.py:handle_text: {type(e).__name__}: {e}", person_key="chat.error.api_failed")

    bot = update.message.get_bot()
    cid = update.effective_chat.id
    try:
        await send_long(bot, cid, reply)
    except Exception as md_err:
        log.warning(f"Markdown failed in send_long, retrying plain: {md_err}")
        try:
            await send_long(bot, cid, reply, parse_mode=None)
        except Exception as e2:
            log.error(f"send_long plain also failed: {e2}")
            await update.message.reply_text(await asyncio.to_thread(
                notify.fault, f"handlers/messages.py:handle_text: {type(e2).__name__}: {e2}", person_key="chat.error.delivery_failed"))

    # Арбитр — фоновая задача, не блокирует ответ
    asyncio.create_task(_run_arbiter_background(text, reply))
    # Второй судья, отдельный от отвечавшего (§17): не жалуется ли человек
    # на саму систему. Фоном — ответ человеку уже ушёл.
    asyncio.create_task(_service_trouble_background(
        text, reply, send_to_user=update.message.reply_text))


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Анализирует фото (упаковка, продукт, анализы) с учётом контекста здоровья."""
    caption = (update.message.caption or "").strip()
    if not caption:
        caption = "Что это такое и есть ли что-то важное для моего здоровья?"

    await update.message.chat.send_action("typing")

    try:
        photo = update.message.photo[-1]  # наивысшее разрешение
        tg_file = await context.bot.get_file(photo.file_id)
        image_bytes = await tg_file.download_as_bytearray()

        reply = await asyncio.to_thread(
            ai.chat_with_image, caption, bytes(image_bytes), "image/jpeg"
        )
    except hai_core.ModelNotAdmitted:
        raise
    except Exception as e:
        log.error(f"handle_photo error: {e}", exc_info=True)
        reply = await asyncio.to_thread(
                notify.fault, f"handlers/messages.py:handle_photo: {type(e).__name__}: {e}", person_key="photos.error.processing_failed")

    await update.message.reply_text(reply)

    # Арбитр — фоновая задача (как в handle_text), но в режиме карантина: разбирается
    # ОТВЕТ МОДЕЛИ О КАРТИНКЕ, а не слова человека. Заземляться тут не во что по
    # построению — «[фото] Что это такое?» не содержит ни одного факта. До 2026-07-28
    # извлечённые отсюда числа писались как показания пользователя (инцидент с анализами).
    from bot.helpers import _run_arbiter_background
    asyncio.create_task(_run_arbiter_background(f"[фото] {caption}", reply, unverified=True))


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получает локацию из Telegram, геокодирует и сохраняет в профиль."""
    # Опрос ждёт геопозицию (знакомство: «где дом») — точка уходит в ответ, а не в память.
    try:
        _ad_active = abh.get_active_assessment(update.effective_chat.id)
    except Exception:  # silent-ok: состояние опроса недоступно — обычная обработка точки
        _ad_active = None
    if _ad_active and await abh.handle_location_in_assessment(update, context, _ad_active):
        return
    loc = update.message.location
    lat, lon = loc.latitude, loc.longitude

    await update.message.chat.send_action("typing")

    city, country, country_code = "—", "—", ""
    try:
        import urllib.request
        url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json"
        req = urllib.request.Request(url, headers={"User-Agent": "HealthBot/1.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = _j.loads(r.read())
        addr = data.get("address", {})
        city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality") or "—"
        country = addr.get("country", "—")
        country_code = addr.get("country_code", "").upper()
    except Exception as e:
        log.warning(f"Geocoding error: {e}")

    # До 2026-09-23 здесь была запись в profile_context.json по ЗАШИТОМУ пути iCloud владельца —
    # для любого бота: геопозиция второго тенанта перезаписала бы файл владельца его профилем
    # (нить profile-home). Текущее место — факт памяти ниже; дом тенанта — system_config
    # location.home_* (location_signal), его задаёт знакомство, а не случайная точка.
    import region_pack
    from _time_inject import get_now
    now_str = get_now(_ZI(region_pack.value("timezone", "UTC"))).strftime("%Y-%m-%d %H:%M")

    # Сохраняем в память AI
    db.save_memory(
        category="observation",
        value=f"Локация {now_str}: {city}, {country} (lat={lat:.4f}, lon={lon:.4f})",
        key="current_location",
        confidence=1.0,
        source="telegram_location"
    )

    flag = country_flag(country_code)
    await send_md(update.message.reply_text, text=i18n.t(
        "location.reply.saved", flag=flag, city=city, country=country
    ))


def _tenant_inbox() -> Path:
    """Инбокс документов тенанта: HEALTH_DATA_DIR/incoming (per-tenant)."""
    base = os.environ.get("HEALTH_DATA_DIR") or str(Path.home() / "health")
    d = Path(base) / "incoming"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _inbox_dest(inbox: Path, filename: str, data: bytes) -> tuple[Path, bool]:
    """(путь назначения, is_duplicate). Дедуп по sha256 содержимого — хеш в имени.
    У дубля путь — УЖЕ лежащий файл (сайдкары `<файл>.failed` и т.п. длиннее его имени):
    по нему вотчер решает, был ли прошлый разбор отказом и можно ли взять файл заново."""
    h = hashlib.sha256(data).hexdigest()[:16]
    seen = list(inbox.glob(f"*__{h}.*"))
    if seen:
        return min(seen, key=lambda p: len(p.name)), True
    safe = (filename or f"document_{h}").replace("/", "_").replace("\\", "_")
    stem = Path(safe).stem or "document"
    suffix = Path(safe).suffix or ".bin"
    return inbox / f"{stem}__{h}{suffix}", False


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приём документа (PDF/изображение-файл) → инбокс тенанта на разбор.

    Анализы/меддокументы шлют боту → сохраняем в HEALTH_DATA_DIR/incoming
    (per-tenant), дедуп по содержимому. Распознавание (lab_recognizer)
    запускает watcher отдельно; значения подтверждаются отдельным human-gate.
    Здесь — только приём + квитанция, без блокировки бота на разбор.
    """
    doc = update.message.document
    if doc is None:
        return
    # Bot API отдаёт боту файлы до 20 МБ (grammy.dev/guide/files, сверено 24.09). Больше —
    # get_file падает, и прежний ответ «Не смог скачать, пришли ещё раз» гнал человека по кругу.
    if (doc.file_size or 0) > BOT_DOWNLOAD_LIMIT:
        await update.message.reply_text(
            i18n.t("documents.error.too_large", file_name=doc.file_name))
        return
    await update.message.chat.send_action("typing")
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        data = bytes(await tg_file.download_as_bytearray())
    except Exception as e:
        log.error(f"handle_document download error: {e}", exc_info=True)
        await update.message.reply_text(await asyncio.to_thread(
                notify.fault, f"handlers/messages.py:handle_document: {type(e).__name__}: {e}", person_key="documents.error.download_failed"))
        return
    try:
        inbox = _tenant_inbox()
        dest, is_dup = _inbox_dest(inbox, doc.file_name or "", data)
        if is_dup:
            # Повтор после отказа (не бланк, сбой, пустой ключ) — единственный способ человека
            # сказать «попробуй ещё»; до 05.10 он упирался в «пропускаю дубль» навсегда.
            from lab_intake_watcher import retry_after_refusal
            again = await asyncio.to_thread(retry_after_refusal, dest)
            _mark_doc_reply(context)
            await update.message.reply_text(
                i18n.t("documents.reply.retry" if again else "documents.reply.duplicate"))
            return
        paused = await asyncio.to_thread(_intake_down_note)
        dest.write_bytes(data)
        receipt = paused or _receipt_text(dest.name, dest)
        if not paused:
            import genome_intake
            kind = genome_intake.sniff(dest)
            if kind and not kind["supported"]:
                genome_intake._set_state(dest, status="unsupported", format=kind["format"])
        log.info(f"handle_document: saved {dest} ({len(data)} bytes)")
    except Exception as e:
        log.error(f"handle_document save error: {e}", exc_info=True)
        await update.message.reply_text(await asyncio.to_thread(
                notify.fault, f"handlers/messages.py:handle_document: {type(e).__name__}: {e}", person_key="documents.error.save_failed"))
        return
    _mark_doc_reply(context)
    await update.message.reply_text(receipt)


def _mark_doc_reply(context) -> None:
    """Метка «бот только что ответил на файл»: следующая реплика человека — скорее о файле, и
    открытое знакомство не должно записать её ответом (assessment_bot_handlers._aside)."""
    import time
    data = getattr(context, "chat_data", None)
    if isinstance(data, dict):
        data["doc_reply_at"] = time.time()


BOT_DOWNLOAD_LIMIT = 20 * 1024 * 1024
INTAKE_STALE_SEC = 10 * 60   # разборщик трогает свой пульс каждые 60 с (lab_intake_watcher.POLL_SEC)


def _intake_down_note(now: float | None = None, *,
                      person_key: str = "documents.reply.intake_paused") -> str:
    """Квитанция обещает разбор, а разбирает lab_intake_watcher — отдельный фоновый сервис.
    Не запущен (урок установки его не поднимал до 24.09) — обещание ложное; говорим прямо.
    Сигнал — пульс самого разборщика (§14), а не наличие плиста."""
    import time
    from lab_intake_watcher import heartbeat_path
    try:
        age = (now or time.time()) - heartbeat_path().stat().st_mtime
    except OSError:
        age = None
    if age is not None and age < INTAKE_STALE_SEC:
        return ""
    return notify.fault(i18n.t("documents.operator.intake_down", "ru"), person_key=person_key)


def _receipt_text(name: str, path=None) -> str:
    """Квитанция за файл обещает только тот разбор, который реально есть (нить empty-profile, 24.09).

    До этого бот любому файлу отвечал «распознаю и пришлю на подтверждение». Замер по коду:
    разборщик входящих берёт только форматы lab_intake_watcher.DOC_EXTS и только лабораторные
    таблицы — геном (.txt/.vcf/.zip) не берёт никто, заключение врача пропускается как «не
    таблица» молча. Ложная квитанция хуже отказа: человек считает, что система его знает."""
    from pathlib import Path as _P
    from lab_intake_watcher import DOC_EXTS   # один дом списка форматов — у разборщика
    from link_fetch import display_filename
    name = display_filename(name)
    ext = _P(name).suffix.lower()
    if ext in DOC_EXTS:
        import import_medical_events as _ime   # разбор заключений берёт свой набор форматов
        tail = (i18n.t("documents.reply.medical_report_supported") if ext in _ime.DOC_EXTS else
                i18n.t("documents.reply.medical_report_unsupported"))
        return i18n.t("documents.reply.labs_received", name=name, tail=tail)
    if path is not None:
        import genome_intake   # геном узнаётся по содержимому, не по расширению
        k = genome_intake.sniff(path)
        if k and k["supported"]:
            return (i18n.t("genome.reply.raw_file_received", name=name))
        if k:
            return genome_intake.unsupported_receipt(name, k["format"])
    return (i18n.t("documents.reply.unsupported_format", name=name))


def register(app, owner_filter):
    """Регистрирует MessageHandler-ы: text, photo, document, location.

    Порядок важен: ConvHandler /consult (regestered раньше в bot/main.py)
    должен ловить текст в state CONSULTING до handle_text.
    """
    # Весь свободный текст — в Claude (только владелец)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & owner_filter, handle_text))
    # Фото
    app.add_handler(MessageHandler(filters.PHOTO & owner_filter, handle_photo))
    # Документы (PDF / изображения-файлы) — приём анализов в инбокс тенанта
    app.add_handler(MessageHandler(filters.Document.ALL & owner_filter, handle_document))
    # Локация
    app.add_handler(MessageHandler(filters.LOCATION & owner_filter, handle_location))
