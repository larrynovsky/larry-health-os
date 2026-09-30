"""
handlers/reports.py — отчёты по запросу: /report, /weekly, /monthly, /labs.

Зависит от refresh_data, _send_tasks_from_report, _send_problem_proposals —
эти helpers пока живут в telegram_bot.py (импортируются локально, чтобы
избежать циркулярного импорта). На Sprint 6 C10 они переезжают в services/.

Вынесено из telegram_bot.py в Sprint 6 (C3, 2026-05-23).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from _time_inject import get_today  # seam

import i18n
from _fmt_helpers import fmt_count
import notify

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

import gp_agent as gp
import health_db as db

from bot.utils import send_long, send_md

log = logging.getLogger(__name__)


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot.helpers import refresh_data
    await update.message.chat.send_action("typing")
    await asyncio.to_thread(refresh_data)
    target = get_today()
    chat_id = update.effective_chat.id
    try:
        report, sn_result = await asyncio.to_thread(gp.generate_daily_report, target)
    except Exception as e:
        log.error(f"Ошибка cmd_report: {e}", exc_info=True)
        report = await asyncio.to_thread(
                notify.fault, f"handlers/reports.py:cmd_report: {type(e).__name__}: {e}", person_key="reports.error.not_ready")
        sn_result = {"max_level": None, "urgent_message": ""}
    if sn_result and sn_result.get("max_level") in ("urgent", "critical"):
        await send_md(update.message.get_bot().send_message, chat_id=chat_id, text=sn_result["urgent_message"])
    await send_long(update.message.get_bot(), chat_id, report)


async def cmd_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Еженедельный отчёт по запросу — запускает MDT + GP."""
    from bot.helpers import refresh_data, _send_tasks_from_report, _send_problem_proposals
    await update.message.chat.send_action("typing")
    await asyncio.to_thread(refresh_data)
    target = get_today() - timedelta(days=1)
    try:
        report = await asyncio.to_thread(gp.generate_weekly_report, target, True)
        header = i18n.t("reports.reply.weekly_heading", report=report)
    except Exception as e:
        log.error(f"Ошибка cmd_weekly: {e}", exc_info=True)
        header = await asyncio.to_thread(
                notify.fault, f"handlers/reports.py:cmd_weekly: {type(e).__name__}: {e}", person_key="reports.error.not_ready")
        await send_long(update.message.get_bot(), update.effective_chat.id, header)
        return
    await send_long(update.message.get_bot(), update.effective_chat.id, header)
    asyncio.create_task(_send_tasks_from_report(
        update.message.get_bot(), update.effective_chat.id,
        report, "gp_weekly", target
    ))
    asyncio.create_task(_send_problem_proposals(
        update.message.get_bot(), update.effective_chat.id
    ))


async def cmd_monthly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ежемесячный отчёт по запросу — GP стратегический взгляд."""
    from bot.helpers import refresh_data, _send_problem_proposals
    await update.message.chat.send_action("typing")
    await asyncio.to_thread(refresh_data)
    target = get_today() - timedelta(days=1)
    try:
        report = await asyncio.to_thread(gp.generate_monthly_report, target)
        header = i18n.t("reports.reply.monthly_heading", report=report)
    except Exception as e:
        log.error(f"Ошибка cmd_monthly: {e}", exc_info=True)
        header = await asyncio.to_thread(
                notify.fault, f"handlers/reports.py:cmd_monthly: {type(e).__name__}: {e}", person_key="reports.error.not_ready")
    await send_long(update.message.get_bot(), update.effective_chat.id, header)
    asyncio.create_task(_send_problem_proposals(
        update.message.get_bot(), update.effective_chat.id
    ))


async def cmd_labs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Последние анализы — ключевые тесты + полный последний визит."""
    db.init_db()

    # Ключевые тесты для постоянного мониторинга (онко + метаболизм)
    key_labs = ['CEA', 'CA19-9', 'WBC', 'HGB', 'PLT', 'CRP', 'Albumin',
                'Creatinine', 'ALT', 'AST', 'Ferritin', 'Glucose', 'LDH']
    recent = db.get_recent_labs(730, key_labs)
    # Все тесты последнего визита (чтобы не скрывать Troponin, HbA1c, Mg и т.д.)
    all_recent = db.get_recent_labs(90)

    if not recent and not all_recent:
        await update.message.reply_text(i18n.t("labs.reply.no_results"))
        return

    all_rows = {r['test_name']: r for r in all_recent}
    # Дополняем key_labs строками из all_recent где они свежее
    for r in recent:
        name = r['test_name']
        if name not in all_rows or r['date'] >= all_rows[name]['date']:
            all_rows[name] = r

    valid_dates = [r['date'] for r in all_rows.values() if r['date'] != 'unknown']
    if not valid_dates:
        await update.message.reply_text(i18n.t("labs.reply.no_results"))
        return
    last_date = max(valid_dates)
    try:
        days_since = (get_today() - date.fromisoformat(last_date)).days
    except ValueError:
        days_since = "?"

    _lab_refs = db.get_lab_refs()

    def _ref(lab):
        # референс строки (бланк этой лаборатории) → мода бланков (кэш) → нет флага
        lo, hi = lab.get('ref_low'), lab.get('ref_high')
        if lo is not None and hi is not None:
            return (lo, hi)
        return _lab_refs.get(lab['test_name'])

    # Блок 1: ключевые тесты
    lines = [i18n.t("labs.reply.visit_heading", last_date=last_date,
                    days_since=fmt_count(days_since, "days") if isinstance(days_since, int) else days_since)]
    key_shown = []
    for name in key_labs:
        lab = all_rows.get(name)
        if not lab:
            continue
        v = lab.get('value')
        ref = _ref(lab)
        flag = " ⚠" if (v is not None and ref and (v < ref[0] or v > ref[1])) else ""
        d = lab['date']
        age = i18n.t("labs.reply.result_date", d=d) if d != last_date else ""
        v = f"{v} {lab['unit']}" if v is not None and lab.get('unit') else v
        lines.append(i18n.t("labs.reply.key_result", name=name, v=v, flag=flag, age=age))
        key_shown.append(name)

    # Блок 2: остальные тесты последнего визита
    extra = [r for r in all_rows.values()
             if r['test_name'] not in key_shown and r['date'] == last_date]
    if extra:
        lines.append(i18n.t("labs.reply.other_tests"))
        for lab in sorted(extra, key=lambda x: x['test_name']):
            v = lab.get('value')
            ref = _ref(lab)
            flag = " ⚠" if (v is not None and ref and (v < ref[0] or v > ref[1])) else ""
            v = f"{v} {lab['unit']}" if v is not None and lab.get('unit') else v
            lines.append(i18n.t("labs.reply.other_result", test_name=lab['test_name'], v=v, flag=flag))

    await send_md(update.message.reply_text, text="\n".join(lines))


def register(app, owner_filter):
    """Регистрирует команды отчётов в Application."""
    app.add_handler(CommandHandler("report",  cmd_report,  filters=owner_filter))
    app.add_handler(CommandHandler("weekly",  cmd_weekly,  filters=owner_filter))
    app.add_handler(CommandHandler("monthly", cmd_monthly, filters=owner_filter))
    app.add_handler(CommandHandler("labs",    cmd_labs,    filters=owner_filter))
