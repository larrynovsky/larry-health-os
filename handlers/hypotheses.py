"""
handlers/hypotheses.py — управление гипотезами и протоколами:
/hypotheses, /confirm, /hreject, /protocols, /retire, /hyp.

Вынесено из telegram_bot.py в Sprint 6 (C6, 2026-05-23).
"""
from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

import health_ai as hai
import health_db as db
import i18n
from _fmt_helpers import fmt_count, fmt_label
import notify

from bot.utils import send_long
from bot import actions

log = logging.getLogger(__name__)


async def cmd_hypotheses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать открытые гипотезы с ID для подтверждения/отклонения."""
    await _send_hypotheses(update.message)


async def _send_hypotheses(message):
    hyps = await asyncio.to_thread(hai.get_open_hypotheses, n=10)
    if not hyps:
        await message.reply_text(i18n.t("hypotheses.reply.none_open"))
        return
    lines = [i18n.t("hypotheses.reply.active_heading", hypotheses=fmt_count(len(hyps), "hypotheses"))]
    for h in hyps:
        status_label = fmt_label(h.get("status", ""), "hypotheses.status")
        # Статус последнего консилиума
        outcome = db.get_hypothesis_outcome(h["memory_id"])
        if outcome:
            verdict_ru = {
                "confirmed": i18n.t("hypotheses.verdict.confirmed"),
                "partial":   i18n.t("hypotheses.verdict.partial"),
                "rejected":  i18n.t("hypotheses.verdict.rejected"),
            }.get(outcome["verdict"], i18n.t("hypotheses.verdict.unknown"))
            conf     = outcome.get("confidence") or 0.0
            ev_date  = (outcome.get("evaluated_at") or "")[:10]
            unsent   = outcome.get("sent_at") is None
            consilium_str = i18n.t(
                "hypotheses.reply.consilium_status", verdict_ru=verdict_ru, conf=conf, ev_date=ev_date) \
                + (i18n.t("hypotheses.reply.not_delivered") if unsent else "")
        else:
            consilium_str = ""
        lines.append(i18n.t(
            "hypotheses.reply.entry_heading", hypothesis_id=h['memory_id'],
            status_label=status_label, consilium_str=consilium_str))
        lines.append(f"{h['observation'][:200]}")
        if h.get("prediction"):
            lines.append(i18n.t("hypotheses.reply.prediction", prediction=h['prediction'][:120]))
        lines.append("")
    await message.reply_text("\n".join(lines), reply_markup=actions.keyboard(*[
        [actions.button(f"#{h['memory_id']} {i18n.t(key)}", verb, h["memory_id"])
         for key, verb in (("actions.confirm", "hc"), ("actions.reject", "hr"),
                           ("actions.hypothesis.query", "hq"),
                           ("actions.hypothesis.evaluate", "he"))] for h in hyps]))


async def cmd_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтвердить гипотезу и создать протокол: /confirm <id>"""
    args = context.args or []
    if not args:
        await update.message.reply_text(i18n.t("hypotheses.help.confirm_usage"))
        return
    try:
        hyp_id = int(args[0])
    except ValueError:
        await update.message.reply_text(i18n.t("common.error.id_not_numeric"))
        return

    await _confirm_hypothesis(update.message, hyp_id)


async def _confirm_hypothesis(message, hyp_id):
    await message.chat.send_action("typing")

    confirmed = await asyncio.to_thread(hai.confirm_hypothesis, hyp_id)
    if not confirmed:
        await message.reply_text(i18n.t("hypotheses.error.not_found", hypothesis_id=hyp_id))
        return
    if confirmed.get("_already_confirmed"):
        await message.reply_text(i18n.t("hypotheses.reply.already_confirmed", hypothesis_id=hyp_id))
        return

    await message.reply_text(i18n.t("hypotheses.reply.confirmed_creating_protocol", hypothesis_id=hyp_id))
    await message.chat.send_action("typing")

    try:
        protocol_data = await asyncio.to_thread(
            hai.generate_protocol_from_hypothesis, confirmed
        )
        protocol_id = await asyncio.to_thread(hai.save_protocol, protocol_data)

        lines = [
            i18n.t("protocols.reply.created", protocol_id=protocol_id),
            "",
            f"{protocol_data['title']}",
            "",
            i18n.t("protocols.reply.behavior", behavior=protocol_data['behavior']),
            i18n.t("protocols.reply.frequency", frequency=protocol_data.get('frequency', '—')),
            i18n.t("protocols.reply.rationale", rationale=protocol_data.get('rationale', '—')),
        ]
        await message.reply_text("\n".join(lines),
                                 reply_markup=protocol_keyboard([{"id": protocol_id}]))
    except Exception as e:
        log.error(f"generate_protocol: {e}", exc_info=True)
        await message.reply_text(await asyncio.to_thread(
                notify.fault, f"handlers/hypotheses.py:cmd_confirm id={hyp_id}: {type(e).__name__}: {e}", person_key="protocols.error.plan_failed"))


async def cmd_hreject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отклонить гипотезу: /hreject <id> [причина]"""
    args = context.args or []
    if not args:
        await update.message.reply_text(i18n.t("hypotheses.help.reject_usage"))
        return
    try:
        hyp_id = int(args[0])
    except ValueError:
        await update.message.reply_text(i18n.t("common.error.id_not_numeric"))
        return
    reason = " ".join(args[1:]) if len(args) > 1 else ""
    await _reject_hypothesis(update.message, hyp_id, reason)


async def _reject_hypothesis(message, hyp_id, reason=""):
    ok = await asyncio.to_thread(hai.reject_hypothesis, hyp_id, reason)
    if ok:
        await message.reply_text(i18n.t("hypotheses.reply.rejected", hypothesis_id=hyp_id))
    else:
        await message.reply_text(i18n.t("hypotheses.error.not_found", hypothesis_id=hyp_id))


async def cmd_protocols(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать активные протоколы поведения."""
    protocols = await asyncio.to_thread(hai.get_active_protocols)
    if not protocols:
        await update.message.reply_text(
            i18n.t("protocols.reply.none_active"), reply_markup=actions.keyboard([
                actions.button(i18n.t("actions.hypotheses"), "hyps", "")])
        )
        return
    lines = [i18n.t("protocols.reply.active_heading")]
    for p in protocols:
        lines.append(i18n.t("protocols.reply.entry_heading", protocol_id=p['id'], title=p['title']))
        lines.append(f"{p['behavior']}")
        if p.get("frequency"):
            lines.append(i18n.t("protocols.reply.frequency", frequency=p['frequency']))
        lines.append(i18n.t("protocols.reply.created_date", created_date=p['created_at'][:10]))
        lines.append("")
    await update.message.reply_text("\n".join(lines), reply_markup=protocol_keyboard(protocols))


async def cmd_retire(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Снять протокол: /retire <id> [причина]"""
    args = context.args or []
    if not args:
        await update.message.reply_text(i18n.t("protocols.help.retire_usage"))
        return
    try:
        protocol_id = int(args[0])
    except ValueError:
        await update.message.reply_text(i18n.t("common.error.id_not_numeric"))
        return
    note = " ".join(args[1:]) if len(args) > 1 else ""
    await _retire_protocol(update.message, protocol_id, note)


async def _retire_protocol(message, protocol_id, note=None):
    ok = await asyncio.to_thread(hai.retire_protocol, protocol_id, note)
    if ok:
        await message.reply_text(i18n.t("protocols.reply.retired", protocol_id=protocol_id))
    else:
        await message.reply_text(i18n.t("protocols.error.not_found", protocol_id=protocol_id))


async def cmd_hyp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/hyp <id> — клинический запрос по гипотезе для специалиста.
    ID гипотезы из /hypotheses."""
    args = (update.message.text or "").split()[1:]
    if not args:
        await update.message.reply_text(
            i18n.t("hypotheses.help.clinical_query_usage")
        )
        return
    try:
        hyp_id = int(args[0])
    except ValueError:
        await update.message.reply_text(i18n.t("common.error.id_not_numeric"))
        return

    await _hypothesis_query(update.message, hyp_id)


async def _hypothesis_query(message, hyp_id):
    import consult_prep as cp
    await message.reply_text(i18n.t("hypotheses.reply.preparing_clinical_query", hypothesis_id=hyp_id))
    try:
        report = await asyncio.to_thread(cp.prepare_hypothesis_query, hyp_id)
        bot = message.get_bot()
        await send_long(bot, message.chat_id, report)
    except Exception as e:
        log.error(f"cmd_hyp: {e}", exc_info=True)
        await message.reply_text(await asyncio.to_thread(
                notify.fault, f"handlers/hypotheses.py:cmd_hyp id={hyp_id}: {type(e).__name__}: {e}"))



def protocol_keyboard(protocols):
    return actions.keyboard(*[[actions.button(
        f"#{p['id']} {i18n.t('actions.protocol.retire')}", "pret", p["id"])] for p in protocols])


@actions.action("hc")
async def btn_confirm_hypothesis(query, context, target):
    await _confirm_hypothesis(query.message, int(target))
    await actions.remove_target(query, target)


@actions.action("hr")
async def btn_reject_hypothesis(query, context, target):
    await _reject_hypothesis(query.message, int(target))
    await actions.remove_target(query, target)


@actions.action("hq")
async def hypothesis_query(query, context, target):
    await _hypothesis_query(query.message, int(target))
    await actions.remove_target(query, target)


@actions.action("hyps")
async def open_hypotheses(query, context, target):
    await _send_hypotheses(query.message)
    await actions.remove_target(query, target)


@actions.action("pret")
async def btn_retire_protocol(query, context, target):
    await _retire_protocol(query.message, int(target))
    await actions.remove_target(query, target)


def register(app, owner_filter):
    """Регистрирует команды управления гипотезами и протоколами."""
    app.add_handler(CommandHandler("hypotheses", cmd_hypotheses, filters=owner_filter))
    app.add_handler(CommandHandler("confirm",    cmd_confirm,    filters=owner_filter))
    app.add_handler(CommandHandler("hreject",    cmd_hreject,    filters=owner_filter))
    app.add_handler(CommandHandler("protocols",  cmd_protocols,  filters=owner_filter))
    app.add_handler(CommandHandler("retire",     cmd_retire,     filters=owner_filter))
    app.add_handler(CommandHandler("hyp",        cmd_hyp,        filters=owner_filter))
    app.add_handler(CommandHandler("eval_hypothesis", cmd_eval_hypothesis, filters=owner_filter))


async def cmd_eval_hypothesis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/eval_hypothesis <id> — запустить консилиум для оценки гипотезы."""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            i18n.t("hypotheses.help.evaluate_usage")
        )
        return
    try:
        hyp_id = int(args[0])
    except ValueError:
        await update.message.reply_text(i18n.t("common.error.id_not_numeric"))
        return

    await _evaluate_hypothesis(update.message, hyp_id)


@actions.action("he")
async def evaluate_hypothesis(query, context, target):
    await _evaluate_hypothesis(query.message, int(target))


async def _evaluate_hypothesis(message, hyp_id):
    await message.reply_text(
        i18n.t("hypotheses.reply.starting_consilium", hypothesis_id=hyp_id)
    )
    await message.chat.send_action("typing")

    try:
        from hypothesis_consilium_eval import evaluate_hypothesis_via_consilium
        from hypothesis_resolution import resolve_hypothesis

        verdict_dict = await evaluate_hypothesis_via_consilium(hyp_id)
        resolution   = await asyncio.to_thread(resolve_hypothesis, hyp_id, verdict_dict)

        chat_id    = message.chat_id
        coord_text = verdict_dict.get("coordinator_text", "")

        if coord_text:
            # Outbox: помечаем намерение отправить — до send_long
            await asyncio.to_thread(
                db.mark_hypothesis_outcome_delivering, hyp_id, chat_id
            )
            await send_long(
                message.get_bot(),
                chat_id,
                i18n.t("hypotheses.reply.consilium_report", coord_text=coord_text),
            )
            # Подтверждаем доставку
            await asyncio.to_thread(db.mark_hypothesis_outcome_sent, hyp_id)

        # Итог резолюции
        await message.reply_text(resolution["message"])

    except ValueError as e:
        try:
            from hypothesis_resolution import _read_hypothesis_row
            missing = await asyncio.to_thread(_read_hypothesis_row, hyp_id) is None
        except Exception as lookup_error:
            missing = False
            log.warning("hypothesis lookup failed: %s", lookup_error)
        text = (i18n.t("hypotheses.error.not_found", hypothesis_id=hyp_id) if missing else
                await asyncio.to_thread(notify.fault,
                    f"handlers/hypotheses.py:cmd_eval_hypothesis id={hyp_id}: {type(e).__name__}: {e}"))
        await message.reply_text(text)
    except Exception as e:
        log.error(f"cmd_eval_hypothesis #{hyp_id}: {e}", exc_info=True)
        await message.reply_text(await asyncio.to_thread(
                notify.fault, f"handlers/hypotheses.py:cmd_eval_hypothesis id={hyp_id}: {type(e).__name__}: {e}"))
