"""
bot/helpers.py — shared helpers для handlers/* и jobs/*:
- refresh_data — pull Oura/HAE, init/migrate БД (вызывается перед любым отчётом)
- _send_problem_proposals — отправка pending GP-предложений по problem list
- _send_tasks_from_report — извлечение и отправка задач из GP-отчёта
- _run_arbiter_background — фоновый арбитр памяти после ответа пользователя
- _finalize_checkin_background — финализация вечернего чекина

Вынесено из telegram_bot.py в Sprint 6 (C10, 2026-05-23).
"""
from __future__ import annotations

import asyncio
import json as _j
import logging
import subprocess
import sys
from pathlib import Path

import i18n

import checkin_agent as ck
import health_ai as ai
import health_db as db
import task_agent as ta
from bot.utils import send_md

log = logging.getLogger(__name__)

_HEALTH_SCRIPTS = Path(__file__).resolve().parent.parent  # корень репо


def refresh_data():
    """Подтягивает свежие данные из Oura перед отчётом. Apple Health тянуть нечего: он сам
    приходит через REST /hae/ingest (iCloud-путь «import_apple_health.py daily» снят 26.09)."""
    for script, arg in [("import_oura.py", "5")]:
        try:
            subprocess.run(   # sys.executable: в образе нет /opt/homebrew (docker-install, этап 4)
                [sys.executable, str(_HEALTH_SCRIPTS / script), arg],
                capture_output=True, timeout=30
            )
        except Exception as e:
            log.warning(f"refresh {script}: {e}")
    db.init_db()
    db.migrate_all_json()
    db.import_all_biochemical()


async def _send_problem_proposals(bot, chat_id: int):
    """Доставляет НЕДОСТАВЛЕННЫЕ предложения по problem list и ставит квитанцию.

    Один путь с outbox-job'ом (jobs.scheduled.deliver_pending_proposals): до 2026-09-23
    эта функция была ЕДИНСТВЕННОЙ доставкой и звалась только ручными /report и /weekly —
    еженедельный GP создавал предложения, а до человека они не доходили (нить
    proposal-delivery). Уже доставленное повторно не шлётся: список ждущих — /problems."""
    async with _PROPOSALS_LOCK:  # job и ручная /report в одном цикле — без двойной отправки
        await _deliver_proposals_locked(bot, chat_id)


_PROPOSALS_LOCK = asyncio.Lock()


async def _deliver_proposals_locked(bot, chat_id: int):
    from bot import actions
    from handlers.problems import proposal_buttons
    proposals = await asyncio.to_thread(db.get_undelivered_proposals)
    for prop in proposals:
        try:
            text = await asyncio.to_thread(db.format_proposal_card, prop)
            msg = await bot.send_message(chat_id=chat_id, text=text,
                                        reply_markup=actions.keyboard(proposal_buttons(prop["id"])))
            await asyncio.to_thread(db.mark_proposal_delivered, prop["id"],
                                    getattr(msg, "message_id", None))
        except Exception as e:  # отказ — квитанции нет, предложение остаётся в outbox
            log.error(f"_send_problem_proposals: #{prop['id']} не доставлено: {e}")


async def _send_tasks_from_report(bot, chat_id: int, report: str,
                                    report_type: str, report_date):
    """Извлекает задачи из отчёта и отправляет отдельным сообщением."""
    log.info(f"_send_tasks_from_report: старт, type={report_type}, "
             f"report_len={len(report)}")
    try:
        tasks, msg = await asyncio.to_thread(
            ta.process_gp_report, report, report_type, report_date
        )
        log.info(f"_send_tasks_from_report: извлечено {len(tasks)} задач, "
                 f"msg={'да' if msg else 'нет'}")
        if msg:
            from handlers.tasks import task_keyboard
            from bot.utils import send_long
            await send_long(bot, chat_id, msg,
                            reply_markup=task_keyboard(ta.summary_tasks(tasks), open_list=True))
            # Помечаем как отправленные
            sent_count = 0
            for t in tasks:
                if t.get("id"):
                    await asyncio.to_thread(db.mark_task_sent, t["id"])
                    sent_count += 1
            log.info(f"_send_tasks_from_report: помечено sent={sent_count}/{len(tasks)}")
        else:
            log.info("_send_tasks_from_report: задач нет — сообщение не отправлено")
    except Exception as e:
        log.warning(f"_send_tasks_from_report: ошибка — {e}", exc_info=True)


async def _run_arbiter_background(user_msg: str, assistant_reply: str,
                                  unverified: bool = False):
    """Запускает арбитр в фоне после ответа пользователю.
    Ждёт 90 сек чтобы не конкурировать с основным запросом за rate limit.

    unverified=True — разбирается ответ модели о КАРТИНКЕ, а не слова человека:
    всё извлечённое уходит в карантин и не трогает profile_context (hai_chat._provenance)."""
    try:
        await asyncio.sleep(90)  # rate limit window: 10k tokens/min
        loop = asyncio.get_event_loop()
        saved = await loop.run_in_executor(None, ai.run_arbiter, user_msg, assistant_reply,
                                           unverified)
        if saved:
            log.info(f"Арбитр: {saved}")
    except Exception as e:
        # exc_info: тихий отказ экстрактора памяти — известный класс (бот отвечает,
        # но ничего не запоминает). Трассировка обязательна для диагностики.
        log.warning(f"Арбитр фоновая ошибка: {e}", exc_info=True)


async def _finalize_checkin_background(conversation: list):
    """Извлекает и сохраняет данные завершённого чекина."""
    try:
        extracted = await asyncio.to_thread(ck.finalize_checkin, conversation)
        log.info(f"Чекин финализирован: score={extracted.get('day_score')}, "
                 f"energy={extracted.get('energy_level')}")
    except Exception as e:
        log.warning(f"Ошибка финализации чекина: {e}", exc_info=True)


async def _service_trouble_background(user_msg: str, assistant_reply: str, send_to_user=None):
    """Гипотеза «человеку плохо от СИСТЕМЫ» — фоном, после ответа человеку.

    Жалоба на недоступный учебный отчёт может потеряться в объяснении интерфейса.
    Это независимо придуманный пример: отвечающая модель участвует в разговоре,
    поэтому её ответ не заменяет отдельного суждения о жалобе (§17).

    Лестница §13: средняя уверенность → бот спрашивает САМ (дёшево и обратимо,
    и ответ человека и есть разметка); высокая → оператору немедленно.
    Владельцу вопросов не задаём — решение владельца 2026-08-02.

    Никогда не бросает: подозрение на поломку не имеет права уронить бота.
    """
    try:
        import service_trouble as st
        import health_db as _db
        import lab_intake_watcher as _liw   # DASHBOARD_URL — один дом константы
        from secrets_paths import is_owner
        from urllib.parse import urlparse

        tenant = _db.DB_PATH.parent.parent.name or "self"
        host = urlparse(_liw.DASHBOARD_URL).hostname or ""
        v = await asyncio.to_thread(
            st.assess, user_msg, assistant_reply,
            tenant=tenant, dashboard_host=host, is_owner=is_owner(),
            judge=ai.judge_service_trouble)

        if v.action == "silent":
            return
        hid = await asyncio.to_thread(st.remember, v, tenant)

        if v.action == "ask" and send_to_user is not None:
            await send_to_user(
                i18n.t("support.reply.clarify_problem"))
            return

        import notify
        await asyncio.to_thread(
            notify.notify_operator,
            i18n.t(
                "support.reply.operator_alert", tenant=tenant, confidence=v.confidence, hypothesis_id=hid,
                signals=', '.join((k for (k, ok) in v.signals.items() if ok)) or '—', reason=v.why or '—',
                guide='docs/how-to/service_trouble_alert.md'
            ))
    except Exception as e:
        log.warning(f"service_trouble фоновая ошибка: {e}", exc_info=True)
