"""
handlers/problems.py — управление problem list: /problems, /approve, /reject.

Вынесено из telegram_bot.py в Sprint 6 (C5, 2026-05-23).
"""
from __future__ import annotations

import asyncio
import json as _j
import logging

import i18n
from _fmt_helpers import fmt_label

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

import health_db as db
from bot import actions

from bot.utils import send_long

log = logging.getLogger(__name__)


def proposal_buttons(proposal_id, prefix=""):
    return [actions.button(prefix + i18n.t("actions.proposal.apply"), "pa", proposal_id),
            actions.button(prefix + i18n.t("actions.reject"), "pr", proposal_id)]


async def _pending_proposals(message):
    proposals = await asyncio.to_thread(db.get_pending_proposals)
    if not proposals:
        await message.reply_text(i18n.t("problems.reply.no_pending"))
    for prop in proposals:
        text = await asyncio.to_thread(db.format_proposal_card, prop)
        await message.reply_text(text, reply_markup=actions.keyboard(proposal_buttons(prop["id"])))


async def cmd_problems(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Текущий список проблем из БД + пендинг пропозалы."""
    problems = await asyncio.to_thread(db.get_problem_list)
    STATUS_ICON = {
        "active_monitoring": "🔴",
        "watchful_waiting":  "🟡",
        "resolved":          "✅",
    }
    lines = [i18n.t("problems.reply.heading")]
    for p in problems:
        icon = STATUS_ICON.get(p.get("status", ""), "⚪")
        lines.append(
            i18n.t(
                "problems.reply.entry", icon=icon, problem_id=p['problem_id'], title=p['title'],
                status=fmt_label(p.get('status', ''), "problems.status"), updated_date=p.get('last_updated', '?')[:10]
            )
        )

    proposals = await asyncio.to_thread(db.get_pending_proposals)
    if proposals:
        lines.append(i18n.t("problems.reply.pending_count", proposal_count=len(proposals)))
        for pr in proposals:
            changes = _j.loads(pr["proposed"])
            lines.append(
                i18n.t(
                    "problems.reply.proposal_entry", proposal_id=pr['id'],
                    created_date=pr['created_at'][:10], change_count=len(changes)
                )
            )

    await send_long(update.message.get_bot(), update.effective_chat.id,
                    "\n".join(lines), reply_markup=actions.keyboard(*[
                        proposal_buttons(p["id"], f"#{p['id']} ") for p in proposals]))


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Применить пропозал: /approve <id>."""
    args = context.args or []
    if not args:
        await _pending_proposals(update.message)
        return
    try:
        prop_id = int(args[0])
        await _approve_proposal(update.message, prop_id)
    except ValueError:
        await update.message.reply_text(i18n.t("common.error.id_not_numeric"))


async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отклонить пропозал: /reject <id> [причина]."""
    args = context.args or []
    if not args:
        await _pending_proposals(update.message)
        return
    try:
        prop_id = int(args[0])
        note = " ".join(args[1:]) if len(args) > 1 else None
        await _reject_proposal(update.message, prop_id, note)
    except ValueError:
        await update.message.reply_text(i18n.t("common.error.id_not_numeric"))


async def _approve_proposal(message, prop_id):
    applied = await asyncio.to_thread(db.apply_proposal, prop_id)
    await message.reply_text(
        i18n.t("problems.reply.applied", prop_id=prop_id, applied=applied) if applied
        else i18n.t("problems.error.proposal_not_found", prop_id=prop_id))


async def _reject_proposal(message, prop_id, note=None):
    await asyncio.to_thread(db.reject_proposal, prop_id, note)
    await message.reply_text(i18n.t("problems.reply.rejected", prop_id=prop_id))


@actions.action("pa")
async def approve_proposal(query, context, target):
    await _approve_proposal(query.message, int(target))
    await actions.remove_target(query, target)


@actions.action("pr")
async def btn_reject_proposal(query, context, target):
    await _reject_proposal(query.message, int(target))
    await actions.remove_target(query, target)


def register(app, owner_filter):
    """Регистрирует команды управления problem list."""
    app.add_handler(CommandHandler("problems", cmd_problems, filters=owner_filter))
    app.add_handler(CommandHandler("approve",  cmd_approve,  filters=owner_filter))
    app.add_handler(CommandHandler("reject",   cmd_reject,   filters=owner_filter))
