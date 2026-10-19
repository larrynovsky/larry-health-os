"""handlers/bug.py — /bug: человек сам сообщает разработчику о сбое (нить bug-report, 19.10).

Решения владельца: команда, а не надпись под ошибкой («фиг поймёшь, когда её писать»); вместо
свободного текста — кнопки-категории; перед отправкой человек видит, что уйдёт; «доставлено» —
только по квитанции приёмника. Форма отчёта — bug_report, путь — bug_outbox.
"""
from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

import i18n
from bot import actions


def _category_keyboard():
    import bug_report
    return actions.keyboard(*[[actions.button(i18n.t(f"bug.category.{c}"), "bug_cat", c)]
                              for c in bug_report.CATEGORIES])


async def cmd_bug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(i18n.t("bug.ask.category"), reply_markup=_category_keyboard())


def _preview(rep: dict, category: str) -> str:
    none = i18n.t("bug.preview.none")
    faults = ", ".join(f"{g['type']} ×{g['n']}" for g in rep["faults"]) or none
    connected = ", ".join(k for k, v in rep["connected"].items() if v) or none
    return i18n.t("bug.preview", release=rep["release"], platform=rep["platform"],
                  category=i18n.t(f"bug.category.{category}"), faults=faults,
                  connected=connected, screen=len(rep["screen"]))


@actions.action("bug_cat")
async def act_category(query, context, target):
    import bug_report
    category = target if target in bug_report.CATEGORIES else "other"
    await actions.clear_markup(query)
    rep = await asyncio.to_thread(bug_report.collect, category, "0" * 16 + ":0")
    await query.message.reply_text(_preview(rep, category), reply_markup=actions.keyboard(
        [actions.button(i18n.t("bug.action.send"), "bug_send", category),
         actions.button(i18n.t("bug.action.cancel"), "bug_no", category)]))


@actions.action("bug_send")
async def act_send(query, context, target):
    import bug_outbox
    import bug_report
    await actions.clear_markup(query)
    category = target if target in bug_report.CATEGORIES else "other"

    def _go():
        return bug_outbox.send(bug_report.collect(category, bug_outbox.next_report_id()))
    status, detail = await asyncio.to_thread(_go)
    if status == "delivered":
        text = i18n.t("bug.reply.delivered", number=detail)
    elif status == "queued":
        text = i18n.t("bug.reply.queued")
    else:
        text = i18n.t({"limit": "bug.reply.limit", "no_receiver": "bug.reply.no_receiver"}.get(
            detail, "bug.reply.refused"))
    await query.message.reply_text(text)


@actions.action("bug_no")
async def act_cancel(query, context, target):
    await actions.clear_markup(query)
    await query.message.reply_text(i18n.t("bug.reply.cancelled"))


def register(app, owner_filter):
    app.add_handler(CommandHandler("bug", cmd_bug, filters=owner_filter))
