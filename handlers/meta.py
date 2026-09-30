"""
handlers/meta.py — мета-команды бота: /start, /help, /memory,
/checkin, /sleep, /experiment.

Вынесено из telegram_bot.py в Sprint 6 (C2, 2026-05-23).
"""
from __future__ import annotations
import infra_config

import asyncio
import json as _json
import logging
from datetime import date, timedelta
from _time_inject import get_now, get_today  # seam
from _fmt_helpers import fmt_count, fmt_min, fmt_or
from pathlib import Path as _Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CommandHandler, ContextTypes

import checkin_agent as ck
import health_db as db
import i18n
import notify

from bot.filters import save_chat_id
from bot import actions
from bot.utils import send_long, send_md

log = logging.getLogger(__name__)


async def _begin_onboarding(chat) -> None:
    import assessment_bot_handlers as abh
    text, kb, _sid = await asyncio.to_thread(abh.start_onboarding, chat.id)
    markup = abh._kb_to_markup(kb)
    stop = [actions.button(i18n.t("actions.onboarding.stop"), "ob_stop", "")]
    if isinstance(markup, InlineKeyboardMarkup):
        markup = actions.keyboard(*markup.inline_keyboard, stop)
    else:
        await chat.send_message(i18n.t("onboarding.reply.resumed"), reply_markup=actions.keyboard(stop))
    await chat.send_message(text, reply_markup=markup)


async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/about — пройти знакомство (заново или продолжить прерванное). Уже записанное бот
    предлагает кнопкой «Верно: …», переспрашивать не заставляет.
    Инструкция человеку: docs/how-to/redo_onboarding.md."""
    db.init_db()
    await _begin_onboarding(update.effective_chat)


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/stop — прервать знакомство. Уже сказанное остаётся записанным; /about продолжит."""
    await _stop_onboarding(update.message)


async def _stop_onboarding(message):
    import assessment_bot_handlers as abh
    import assessment_dialog as ad
    s = db.get_active_assessment_session(message.chat_id, instrument_id=abh.ONBOARDING_ID)
    if not s:
        await message.reply_text(i18n.t("onboarding.reply.not_active"))
        return
    ad.abandon(s["id"], reason="/stop")
    await message.reply_text(i18n.t("onboarding.reply.stopped"), reply_markup=actions.keyboard([
        actions.button(i18n.t("actions.onboarding.continue"), "ob_redo", "")]))


@actions.action("ob_stop")
async def stop_onboarding(query, context, target):
    await _stop_onboarding(query.message)
    await query.edit_message_reply_markup(reply_markup=None)


@actions.action("ob_redo")
async def redo_onboarding(query, context, target):
    await _begin_onboarding(query.message.chat)
    await query.edit_message_reply_markup(reply_markup=None)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_chat_id(update.effective_chat.id)
    db.init_db()
    # Первый вход (в профиле нет даже имени) — сразу знакомство (решение владельца 23.09:
    # «всё сразу при первом входе»).
    if not db.get_patient_profile().get("identity.name"):
        await _begin_onboarding(update.effective_chat)
        return
    from jobs.scheduled import _brief_time
    await update.message.reply_text(
        i18n.t("start.reply.welcome", brief_time=_brief_time().strftime("%H:%M"))
    )


async def cmd_experiment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать активные эксперименты."""
    db.init_db()
    exps = db.get_active_experiments()
    if not exps:
        await update.message.reply_text(
            i18n.t("experiments.reply.none_active")
        )
        return
    lines = [i18n.t("experiments.reply.active_heading")]
    for e in exps:
        stats = db.get_experiment_stats(e["id"])
        n = (stats.get("adherence") or {}).get("total", 0) or 0
        before = stats.get("before_14d", {})
        after  = stats.get("after", {})
        lines.append(i18n.t("experiments.reply.day", name=e['name'], day_count=n))
        lines.append(f"{e['intervention']}")
        if before.get("deep") and after.get("deep") and n >= 3:
            delta = (after["deep"] - before["deep"]) * 60
            lines.append(i18n.t("experiments.reply.deep_sleep_delta", sign='+' if delta >= 0 else '', delta=delta))
        lines.append("")
    await send_md(update.message.reply_text, text="\n".join(lines))


async def cmd_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запустить вечерний чекин вручную."""
    if ck.checkin_state.active:
        await update.message.reply_text(i18n.t("checkin.reply.already_active"), reply_markup=checkin_keyboard())
        return
    try:
        question = await asyncio.to_thread(ck.generate_opening_question)
        ck.checkin_state.start(question)
        await update.message.reply_text(question + i18n.t("checkin.help.stop"), reply_markup=checkin_keyboard())
    except Exception as e:
        await update.message.reply_text(await asyncio.to_thread(
                notify.fault, f"handlers/meta.py:cmd_checkin: {type(e).__name__}: {e}"))


def checkin_keyboard():
    return actions.keyboard([actions.button(i18n.t("actions.checkin.stop"), "ck_end",
                                            ck.checkin_state.started_at.isoformat())])


@actions.action("ck_end")
async def stop_checkin(query, context, target):
    state = ck.checkin_state
    if not state.active or state.started_at is None or target != state.started_at.isoformat():
        await query.message.reply_text(i18n.t("checkin.reply.already_ended"))
        await query.edit_message_reply_markup(reply_markup=None)
        return
    snapshot = list(state.conversation)
    state.reset()
    from bot.helpers import _finalize_checkin_background
    if any(item["role"] == "user" for item in snapshot):
        asyncio.create_task(_finalize_checkin_background(snapshot))
    await query.message.reply_text(i18n.t("checkin.reply.stopped"))
    await query.edit_message_reply_markup(reply_markup=None)


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает что арбитр сохранил из последних разговоров."""
    db.init_db()

    # Типизированная память memory_facts — единый источник (C, 2026-07-05);
    # раньше легаси get_memory(n=30) по старой таблице вперемешку.
    import memory_facts_db as _mf
    items = _mf.get_facts(active_only=True)  # все классы, subject='self' (R11)

    groups = {}
    for item in items:
        groups.setdefault(item["mem_class"], []).append(item)

    LABELS = {
        "fact":           i18n.t("memory.category.facts"),
        "state":          i18n.t("memory.category.observations"),
        "question":       i18n.t("memory.category.questions"),
        "recommendation": i18n.t("memory.category.recommendations"),
        "experiment":     i18n.t("memory.category.experiments"),
        "preference":     i18n.t("memory.category.preferences"),
    }

    lines = [i18n.t("memory.reply.heading")]

    if not items:
        lines.append(i18n.t("memory.reply.empty"))
    else:
        for cat, label in LABELS.items():
            entries = groups.get(cat, [])
            if not entries:
                continue
            lines.append(i18n.t("memory.reply.category_heading", label=label))
            for e in entries[:5]:
                date_s = (e.get("updated_at") or e.get("valid_from") or "")[:10]
                key_s  = i18n.t("memory.reply.entry_key", entry_key=e['key']) if e.get("key") else ""
                val    = e["value"]
                # Обрезаем длинные строки
                if len(val) > 120:
                    val = val[:117] + "..."
                lines.append(i18n.t("memory.reply.entry", date_s=date_s, key_s=key_s, val=val))
            lines.append("")

    # Показываем ключевые поля профиля
    try:
        # Профиль ТЕНАНТА из базы (до 2026-09-23 — iCloud-файл владельца для любого бота).
        profile = db.get_profile_context()
        med  = profile.get("medical", {})
        loc  = profile.get("current_location", {})
        obs  = profile.get("observations", [])
        oq   = profile.get("open_questions", [])

        lines.append(i18n.t("memory.reply.profile_heading"))
        for field in ("treatment_status", "chemo_ended", "next_appointment"):
            value = med.get(field)
            if value is not None and str(value).strip() not in ("", "—"):
                lines.append(i18n.t(f"memory.reply.{field}", **{field: value}))
        if loc.get("city"):
            lines.append(i18n.t(
                "memory.reply.location", city=loc['city'],
                country=loc.get('country', ''), updated=loc.get('updated', '')))
        if obs:
            lines.append(i18n.t("memory.reply.observation_count", observation_count=len(obs)))
        if oq:
            lines.append(i18n.t("memory.reply.question_count", question_count=len(oq)))
    except Exception as e:  # silent-ok: profile_context.json optional, отсутствие не ломает /memory
        log.debug(f"cmd_memory: profile load failed (ignored): {e}")

    text = "\n".join(lines)
    await send_long(update.message.get_bot(), update.effective_chat.id, text)


def _utc_offset_label(tz) -> str:
    """«UTC+3» вместо имени зоны вида «Europe/<город>»: имя зоны человеку непонятно (холодное чтение 28.09)."""
    # Через seam часов (контракт единого времени); astimezone — чтобы подменённые aware-часы
    # (UTC) дали смещение ЗАПРОШЕННОЙ зоны, а не своё (ревью ночного ремонта 30.09).
    minutes = int(get_now(tz).astimezone(tz).utcoffset().total_seconds() // 60)
    if not minutes:
        return "UTC"
    h, m = divmod(abs(minutes), 60)
    return f"UTC{'+' if minutes > 0 else '-'}{h}" + (f":{m:02d}" if m else "")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from jobs import scheduled as schedule
    text = i18n.t(
        "common.help.commands", brief_time=schedule._brief_time().strftime("%H:%M"),
        weekly_days=", ".join(i18n.t(f"weekday.{day}") for day in schedule.WEEKLY_REPORT_DAYS),
        weekly_time=schedule.WEEKLY_REPORT_TIME.strftime("%H:%M"),
        monthly_day=schedule.MONTHLY_REPORT_DAY,
        monthly_time=schedule.MONTHLY_REPORT_TIME.strftime("%H:%M"),
        report_timezone=_utc_offset_label(schedule._tenant_tz(schedule.TZ)),
    )
    await send_md(update.message.reply_text, text=text)


# cmd_app (Mini App) выпилен 2026-07-06 (TD-09): бекенд :8000 не существует,
# кнопка вела на 502. Решение владельца — убить миниапп; витрина = дашборд :8001.


async def cmd_sleep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сон за 7 дней — таблица."""
    db.init_db()
    lines = []
    for i in range(1, 8):
        d = get_today() - timedelta(days=i)
        row = db.get_day(str(d))
        s   = row.get("sleep") or {}
        sc  = s.get("sleep_score")
        t   = s.get("totalSleep")
        dp = fmt_min(s.get("deep"))
        if t is not None:
            sc_s = i18n.t("sleep.reply.score", sc=sc) if sc is not None else ""
            lines.append(i18n.t("sleep.reply.night", date=d.strftime('%d.%m'), t=t, dp=dp, sc_s=sc_s))
    if not lines:
        await update.message.reply_text(i18n.t("sleep.reply.empty"))
        return
    lines.insert(0, i18n.t("sleep.reply.heading", nights=fmt_count(len(lines), "nights")))
    s7 = db.get_stats(7)
    avg_deep = fmt_min(s7.get("avg_deep"))
    lines.append(i18n.t(
        "sleep.reply.average", avg_sleep=fmt_or(s7.get('avg_sleep')),
        avg_deep=avg_deep, avg_sleep_score=fmt_or(s7.get('avg_sleep_score'))))
    await send_md(update.message.reply_text, text="\n".join(lines))


async def cmd_map(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/map <raw_name> <canonical_name> — явный маппинг маркера анализа.

    Пример: /map HomocysteineNew Homocysteine
    Fallback когда пользователь не использовал Reply на уведомление.
    """
    args = context.args or []
    if len(args) < 2:
        await send_md(update.message.reply_text, text=i18n.t("labs.help.map_usage"))
        return

    raw_name  = args[0]
    canonical = " ".join(args[1:])

    # Ищем pending review с этим raw_name
    pending = db.get_pending_field_reviews()
    match = next((r for r in pending if r["raw_name"].lower() == raw_name.lower()), None)

    if match:
        db.resolve_field_review(match["id"], canonical=canonical)
        await send_md(update.message.reply_text, text=i18n.t(
            "labs.reply.mapping_saved_with_name", raw_name=raw_name, canonical=canonical))
    else:
        # Нет pending — всё равно сохраняем в aliases (формат Synevo по умолчанию)
        fmt = db.get_lab_format_by_name("synevo")
        fmt_id = fmt["id"] if fmt else None
        if fmt_id:
            db.confirm_field_alias(fmt_id, raw_name, canonical)
            await send_md(update.message.reply_text, text=i18n.t(
                "labs.reply.alias_added", raw_name=raw_name, canonical=canonical))
        else:
            await asyncio.to_thread(
                notify.fault, "handlers/meta.py:cmd_map: pending review and lab format not found", person_key=None)


def register(app, owner_filter):
    """Регистрирует мета-команды в Application."""
    app.add_handler(CommandHandler("start",      cmd_start,      filters=owner_filter))
    app.add_handler(CommandHandler("about",      cmd_about,      filters=owner_filter))
    app.add_handler(CommandHandler("stop",       cmd_stop,       filters=owner_filter))
    app.add_handler(CommandHandler("experiment", cmd_experiment, filters=owner_filter))
    app.add_handler(CommandHandler("sleep",      cmd_sleep,      filters=owner_filter))
    app.add_handler(CommandHandler("help",       cmd_help,       filters=owner_filter))
    app.add_handler(CommandHandler("memory",     cmd_memory,     filters=owner_filter))
    app.add_handler(CommandHandler("checkin",    cmd_checkin,    filters=owner_filter))
    app.add_handler(CommandHandler("map",        cmd_map,        filters=owner_filter))
