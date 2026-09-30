"""
handlers/tasks.py — команды управления задачами и визитами:
/tasks, /done, /dismiss, /visit.

Вынесено из telegram_bot.py в Sprint 6 (C4, 2026-05-23).
"""
from __future__ import annotations
from _time_inject import get_today  # seam

import asyncio
import logging
from datetime import date as _date

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

import health_db as db
import i18n
import notify
import task_agent as ta

from bot.utils import send_long
from bot import actions

log = logging.getLogger(__name__)


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Закрыть задачу: /done <id> [ответ/комментарий]."""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            i18n.t("tasks.help.done_usage")
        )
        return
    try:
        task_id = int(args[0])
        resolved_text = " ".join(args[1:]) if len(args) > 1 else None

        await _complete_task(update.message, context, task_id, resolved_text)
    except ValueError:
        await update.message.reply_text(i18n.t("tasks.error.id_not_numeric_with_example"))


async def cmd_dismiss(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отклонить задачу: /dismiss <id>."""
    args = context.args or []
    if not args:
        await update.message.reply_text(i18n.t("tasks.help.dismiss_usage"))
        return
    try:
        task_id = int(args[0])
        await _dismiss_task(update.message, task_id)
    except ValueError:
        await update.message.reply_text(i18n.t("common.error.id_not_numeric"))


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать все открытые задачи."""
    await send_open_tasks(update.message)


def task_keyboard(tasks, *, open_list=False):
    rows = []
    for number, task in enumerate(tasks[:19 if open_list else 20], 1):
        verbs = [("actions.task.answer", "ta")] if task.get("type") == "question" else [
            ("actions.task.done", "td"), ("actions.task.dismiss", "tx")]
        rows.append([actions.button(f"{number} {i18n.t(key)}", verb, task["id"])
                     for key, verb in verbs])
    if open_list:
        rows.append([actions.button(i18n.t("actions.tasks"), "tasks", "")])
    return actions.keyboard(*rows)


async def send_open_tasks(message):
    tasks = await asyncio.to_thread(db.get_open_tasks, 20)
    if not tasks:
        await message.reply_text(i18n.t("tasks.reply.none_open"))
        return
    await send_long(message.get_bot(), message.chat_id, ta.format_open_tasks_message(tasks),
                    reply_markup=task_keyboard(tasks))


async def _ask_task(message, context, task_id):
    await actions.ask(context.bot, message.chat_id,
                      i18n.t("tasks.help.answer_required", task_id=task_id), "ta", task_id)


async def _complete_task(message, context, task_id, resolved_text=None):
    # Вопрос закрывается ответом, а не фактом нажатия: без текста база всё
    # равно отвергнет закрытие (триггер trg_question_answer_required_upd),
    # поэтому просим текст здесь — иначе человек увидит ошибку SQLite.
    task = next((t for t in await asyncio.to_thread(db.get_open_tasks, 1000)
                 if t["id"] == task_id), None)
    if task and task.get("type") == "question":
        if not resolved_text:
            await _ask_task(message, context, task_id)
            return
        ok = await asyncio.to_thread(ta.record_answer, task_id,
                                     resolved_text, "telegram_done")
        await message.reply_text(
            i18n.t("tasks.reply.answer_recorded", task_id=task_id)
            if ok else await asyncio.to_thread(
            notify.fault, f"handlers/tasks.py:record_answer failed task_id={task_id}", person_key="tasks.error.answer_not_saved")
        )
        return   # вопрос закрыт ответом (record_answer) — второй раз не закрываем
        return

    ok = await asyncio.to_thread(db.resolve_task, task_id, resolved_text, "completed")
    if ok:
        # Закрываем напоминание в macOS Reminders
        await asyncio.to_thread(ta.complete_macos_reminder, task_id)
        await message.reply_text(i18n.t("tasks.reply.completed", task_id=task_id))
    else:
        await message.reply_text(i18n.t("tasks.error.not_found", task_id=task_id))


async def _dismiss_task(message, task_id):
    ok = await asyncio.to_thread(db.resolve_task, task_id, "dismissed by user", "dismissed")
    if not ok:
        await message.reply_text(i18n.t("tasks.error.not_found", task_id=task_id))
        return
    # Закрываем напоминание в macOS Reminders
    await asyncio.to_thread(ta.complete_macos_reminder, task_id)
    await message.reply_text(i18n.t("tasks.reply.dismissed", task_id=task_id))


@actions.action("td")
async def task_done(query, context, target):
    await _complete_task(query.message, context, int(target))
    await actions.remove_target(query, target)


@actions.action("tx")
async def task_dismiss(query, context, target):
    await _dismiss_task(query.message, int(target))
    await actions.remove_target(query, target)


@actions.action("ta")
async def task_ask(query, context, target):
    await _ask_task(query.message, context, int(target))
    await actions.remove_target(query, target)


@actions.on_reply("ta")
async def task_answer(message, context, target, text):
    ok = await asyncio.to_thread(ta.record_answer, int(target), text, "telegram_reply")
    await message.reply_text(i18n.t("tasks.reply.answer_recorded", task_id=target) if ok else
                             i18n.t("tasks.error.empty_answer"))


@actions.action("tasks")
async def open_tasks(query, context, target):
    await send_open_tasks(query.message)
    await actions.remove_target(query, target)


@actions.action("ans_yes")
async def confirm_answer(query, context, target):
    original = query.message.reply_to_message
    text = original.text if original else None
    if not text:
        await query.message.reply_text(i18n.t("tasks.error.empty_answer"))
        return
    await task_answer(query.message, context, target, text)
    await actions.remove_target(query, target)


@actions.action("ans_no")
async def decline_answer(query, context, target):
    await actions.remove_target(query, target)


async def cmd_visit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/visit [YYYY-MM-DD] [тип] — краткая сводка для визита к врачу.
    Без типа — дефолтный специалист тенанта (config_db.default_visit_specialist,
    не зашитый онколог; нить diagnosis-hardcode).
    Пример: /visit 2030-01-15"""
    import consult_prep as cp
    import config_db as _cfg
    args = (update.message.text or "").split()[1:]

    visit_date = args[0] if args else str(get_today())
    spec_type  = args[1] if len(args) > 1 else _cfg.default_visit_specialist()

    # Валидация даты
    try:
        _date.fromisoformat(visit_date)
    except ValueError:
        await update.message.reply_text(
            i18n.t("visits.error.invalid_date")
        )
        return

    await update.message.reply_text(
        i18n.t("visits.reply.preparing", spec_type=spec_type, visit_date=visit_date)
    )
    try:
        report = await asyncio.to_thread(cp.prepare_visit_report, visit_date, spec_type)
        bot = update.message.get_bot()
        await send_long(bot, update.effective_chat.id, report)
    except Exception as e:
        log.error(f"cmd_visit: {e}", exc_info=True)
        await update.message.reply_text(await asyncio.to_thread(
                notify.fault, f"handlers/tasks.py:cmd_visit: {type(e).__name__}: {e}"))


def register(app, owner_filter):
    """Регистрирует команды задач и визитов."""
    app.add_handler(CommandHandler("tasks",   cmd_tasks,   filters=owner_filter))
    app.add_handler(CommandHandler("done",    cmd_done,    filters=owner_filter))
    app.add_handler(CommandHandler("dismiss", cmd_dismiss, filters=owner_filter))
    app.add_handler(CommandHandler("visit",   cmd_visit,   filters=owner_filter))
