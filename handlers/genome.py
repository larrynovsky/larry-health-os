"""
handlers/genome.py — команды работы с геномом: /genome, /genome_update.

Как работает фильтрация вариантов: docs/genome/explanation_genome_variant_filtering.md.
Таблица genetic_variants и view genome(): docs/genome/reference_genetic_variants_table.md.
Перегенерация русских описаний: docs/genome/howto_regenerate_descriptions.md.

Вынесено из telegram_bot.py в Sprint 6 (C7, 2026-05-23).
"""
from __future__ import annotations

import asyncio
import json as _j
import logging

import i18n
from _fmt_helpers import fmt_count, fmt_label
import notify

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

import genome_context as gc
import health_db as db
from bot.utils import send_md
from bot import actions

log = logging.getLogger(__name__)


def _variant_entry(v):
    """Показ варианта: метка справочника отдельно от проверенного носительства."""
    import re
    lang = i18n.lang_of()
    sig = v.get("significance") or ""
    labels = []
    for significance in sig.split(";"):
        label = fmt_label(significance.strip(), "genome.significance", lang, unknown_expected=True)
        if significance and label == i18n.t("genome.significance.unknown", lang):
            label += f" ({significance})"  # неизвестный клинический смысл не угадываем и не теряем
        labels.append(label)
    # Аллель риска проверен — те же два статуса, что стережёт
    # genome_db.carrier_status_null_allele_violations. Иначе носительство НЕИЗВЕСТНО,
    # и это не «чисто» (инвариант genome_effect_allele.null_is_unknown_not_clean).
    resolved = (v.get("effect_allele_status") in ("resolved", "palindromic_het_resolved")
                and bool(v.get("effect_allele")))
    zygosity, _ = gc._zygosity(v.get("genotype"), v.get("effect_allele") if resolved else None)
    carrier_key = ("present" if resolved and zygosity in ("hetero", "homo_risk") else
                   "absent" if resolved and zygosity == "wildtype" else "unknown")
    pathogenic = any(
        part.strip().lower().replace("_", " ").startswith(("pathogenic", "likely pathogenic"))
        for part in re.split(r"[;/,|]", sig))
    # 🔴 носитель патогенного; 🟡 патогенный, носительство не определено — к врачу тоже;
    # отсутствие носительства или непатогенная метка — ⚪.
    attention = pathogenic and carrier_key != "absent"
    conditions = _j.loads(v.get("conditions") or "[]")
    condition = conditions[0] if isinstance(conditions, list) and conditions else ""
    if lang == "ru":
        description = v.get("description_ru") or ""
        translated = fmt_label(condition, "genome.condition", lang, unknown_expected=True)
        if condition and translated == i18n.t("genome.condition.unknown", lang):
            translated = i18n.t("genome.reply.untranslated", lang, original=condition)
        condition = (description if re.search(r"[\u0400-\u04FF]", description) else
                     condition if re.search(r"[\u0400-\u04FF]", condition) else
                     translated)
    else:
        condition = fmt_label(condition, "genome.condition", lang) if condition.lower() == "hereditary hemochromatosis" else condition
        condition = condition or i18n.t("genome.condition.unknown", lang)
    details = condition + "\n  " + i18n.t(f"genome.carrier.{carrier_key}", lang)
    if attention:
        details += "\n  " + i18n.t("genome.reply.doctor", lang)
    icon = ("🔴" if carrier_key == "present" else "🟡") if attention else "⚪"
    return i18n.t("genome.reply.variant_entry", lang, icon=icon,
                  gene=v.get("gene") or "?", rsid=v.get("rsid", "?"), genotype=v.get("genotype") or "?",
                  significance="; ".join(labels), condition=details)


async def cmd_genome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/genome [вопрос] — ответ на вопрос о предрасположенности из генома."""
    db.init_db()
    question = " ".join(context.args) if context.args else ""

    if not question:
        # Показываем сводку значимых вариантов
        await update.message.chat.send_action("typing")
        try:
            variants = db.get_significant_variants(limit=20)
            if not variants:
                await send_md(update.message.reply_text, text=await asyncio.to_thread(
                notify.fault, f"handlers/genome.py:cmd_genome: no significant variants; check genome intake/pipeline", person_key="genome.reply.empty"))
                return

            shown = variants[:15]
            lines = [i18n.t("genome.reply.variants_heading", variant_count=fmt_count(len(shown), "variants"))]
            lines.extend(_variant_entry(v) for v in shown)

            lines.append(i18n.t("genome.help.trait_question"))
            await send_md(update.message.reply_text, text="\n".join(lines), reply_markup=actions.keyboard([
                actions.button(i18n.t("actions.genome.question"), "gq", "")]))
        except Exception as e:
            log.error(f"cmd_genome (summary) error: {e}", exc_info=True)
            await update.message.reply_text(await asyncio.to_thread(
                notify.fault, f"handlers/genome.py:cmd_genome: {type(e).__name__}: {e}"))
        return

    await _answer_genome(update.message, question)


async def _answer_genome(message, question):
    # Отвечаем на конкретный вопрос
    await message.chat.send_action("typing")
    try:
        answer = await asyncio.to_thread(gc.answer_trait_question, question)
        await send_md(message.reply_text, text=i18n.t("genome.reply.answer", answer=answer))
    except Exception as e:
        log.error(f"cmd_genome error: {e}", exc_info=True)
        await message.reply_text(await asyncio.to_thread(
                notify.fault, f"handlers/genome.py:cmd_genome: {type(e).__name__}: {e}"))


@actions.action("gq")
async def ask_genome(query, context, target):
    await actions.ask(context.bot, query.message.chat_id, i18n.t("genome.prompt.question"), "gq", "")
    await actions.remove_target(query, target)


@actions.on_reply("gq")
async def genome_answer(message, context, target, text):
    await _answer_genome(message, text)


async def cmd_genome_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/genome_update — запустить ежемесячный синк ClinVar вручную."""
    await update.message.reply_text(i18n.t("genome.reply.checking_updates"))
    try:
        import genome_update_agent as gua
        result = gua.run_monthly_update()
        changed = result.get("changed", 0)
        significant = result.get("significant", 0)
        narrative = result.get("narrative")

        summary = i18n.t("genome.reply.updated", changed=changed)
        if significant > 0:
            summary += i18n.t("genome.reply.significant_changes", significant=significant)

        await send_md(update.message.reply_text, text=summary)

        if narrative:
            await send_md(update.message.reply_text, text=i18n.t(
                "genome.reply.update_explanation", narrative=narrative
            ))
    except Exception as e:
        log.error(f"cmd_genome_update error: {e}", exc_info=True)
        await update.message.reply_text(await asyncio.to_thread(
                notify.fault, f"handlers/genome.py:cmd_genome_update: {type(e).__name__}: {e}"))


def register(app, owner_filter):
    """Регистрирует команды работы с геномом."""
    app.add_handler(CommandHandler("genome",        cmd_genome,        filters=owner_filter))
    app.add_handler(CommandHandler("genome_update", cmd_genome_update, filters=owner_filter))
