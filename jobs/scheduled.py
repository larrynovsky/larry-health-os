"""
jobs/scheduled.py — все scheduled JobQueue-задачи бота.

Jobs: send_morning_report, run_specialists_scheduled,
      send_weekly_report, send_monthly_check, send_genome_update_monthly,
      check_prs_updates_monthly (Block D1, 2026-06-27),
      check_recommendations_scheduled,
      check_hypothesis_evaluations,
      check_pending_doc_reviews, check_pending_field_reviews,
      check_pending_treatments.

Sprint 6 (C10, 2026-05-23): вынесено из telegram_bot.py.
2026-06-27: Block B+C (genome→конституции+задача), Block D1 (PRS monthly check).
"""
from __future__ import annotations
import infra_config
import i18n
from _fmt_helpers import fmt_count, fmt_label
import notify

import asyncio
import json as _j
import logging
from datetime import date, datetime as _dt, time, timedelta
from _time_inject import get_today  # seam
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import checkin_agent as ck
import gp_agent as gp
import health_ai as hai
import health_db as db
import pgs_discovery as pgsd
import task_agent as ta

from bot.filters import owner_chat_id, get_chat_id
from secrets_paths import is_owner
from bot.helpers import refresh_data, _send_tasks_from_report
from bot.utils import send_long, send_md
from services.recommendations import evaluate_domain_need

import region_pack

log = logging.getLogger(__name__)

# Таймзоны — данные региона (private/region.yaml; до 2026-09-23 литералы, pub-prep). TZ — зона
# расписаний Studio, дом — `timezone`; нет пакета → UTC.
TZ = ZoneInfo(region_pack.value("scheduler_timezone", "UTC"))
_HOME_TZ_NAME = region_pack.value("timezone", "UTC")


def _tenant_tz(default):
    """Таймзона тенанта для расписаний: env HEALTH_TZ (IANA, напр. Europe/Berlin)
    переопределяет; иначе default (текущая per-job таймзона владельца — не ломаем)."""
    import os
    name = os.environ.get("HEALTH_TZ")
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            log.warning(f"HEALTH_TZ={name!r} невалиден — беру default")
    return default
_RECO_SENT_KEY = "reco_last_sent"  # keyed by domain


# ── Этап 2 (morning-brief-timing): местные расписания брифа ──────────────────
# Время брифа — в system_config (параметр в ТАБЛИЦЕ, не литерал: треб. владельца). tz — из
# свежего GPS (location_signal.tenant_timezone()). run_daily биндит tz на РЕГИСТРАЦИИ →
# смена пояса (поездка) требует reschedule → job _reschedule_local каждые 12ч (выбор
# владельца: «не так часто меняется за день»). Backstop: провал tz-резолва → держим last-good
# (schedule.active_tz), НЕ молча-UTC + громкий сенсор (урок тихого гапа этапа 1). Per-tenant
# автоматом: owner/partner — разные процессы и разные health.db, каждый резолвит своё.
_BRIEF_TIME_KEY = "schedule.morning_brief"   # value_json {"hour":H,"minute":M}
WEEKLY_REPORT_TIME = time(hour=7, minute=0)
WEEKLY_REPORT_DAYS = (0,)  # PTB: воскресенье = 0
MONTHLY_REPORT_TIME = time(hour=9, minute=30)
MONTHLY_REPORT_DAY = 1
_ACTIVE_TZ_KEY  = "schedule.active_tz"       # value_text — last-good IANA (backstop + наблюдаемость)
_RESCHED_INTERVAL_S = 12 * 3600
# Первый пульс — вскоре после старта, а не через 12ч (2026-09-01). Пульс живости
# `schedule.reschedule_last_run` ставится ТОЛЬКО этим джобом, а бот перезапускается
# post-commit хуком на КАЖДЫЙ коммит: в день с 14 коммитами (31.08) 12-часовое
# окно ни разу не дожило до первого срабатывания, и датчик check_reschedule_liveness
# кричал «партнёр молчит >26ч» про артефакт частоты деплоя, не про поломку.
# Сам джоб идемпотентен (тот же пояс → no-op), ранний запуск безвреден.
_RESCHED_FIRST_S = 120
# Суточные джобы бота — их ритм, объявленный в квитанции (см. _rhythm ниже).
_DAY_S = 24 * 3600
_PROMOTE_EVERY_S = _DAY_S


def _rhythm(every_s: int, started: "_dt") -> dict:
    """Квитанция джоба объявляет свой ритм (решение владельца 23.09 «общее правило»:
    тревога после ПЕРВОГО пропущенного запуска). Следующий запуск обязан начаться не
    позже started_at + every_s — так судит integrity_tests._rhythm_missed. Порог у
    датчика не живёт: ритм пишет тот, кто регистрирует джоб, тем же числом, каким
    регистрирует. started — момент СТАРТА прогона (UTC), а не конца: длительность
    работы в ритм не входит."""
    return {"every_s": int(every_s), "started_at": started.isoformat()}


def _brief_time() -> time:
    """Время утреннего брифа из system_config. Нет ключа/битый формат → 08:30 + warning
    (не молчим: дефолт виден в логе)."""
    try:
        import config_db as _cfg
        v = _cfg.get_config(_BRIEF_TIME_KEY)
        if isinstance(v, dict) and "hour" in v and "minute" in v:
            return time(hour=int(v["hour"]), minute=int(v["minute"]))
        if v is not None:
            log.warning("_brief_time: %s=%r не {hour,minute} — дефолт 08:30", _BRIEF_TIME_KEY, v)
    except Exception as e:  # noqa: BLE001 — конфиг недоступен → дефолт (не молчим)
        log.warning("_brief_time: чтение конфига упало (%r) — дефолт 08:30", e)
    return time(hour=8, minute=30)


def _resolve_brief_tz():
    """(ZoneInfo, name, ok). Текущая политика считает UTC отказом GPS-резолвера.
    Fallback: last-good → env → домашний пояс из пакета региона, ok=False.
    Это ограничение политики, а не утверждение о местонахождении пользователя."""
    import os
    name = None
    try:
        import location_signal as _ls
        name = _ls.tenant_timezone()
    except Exception as e:  # noqa: BLE001
        log.warning("_resolve_brief_tz: tenant_timezone упал: %r", e)
    if name and name != "UTC":
        try:
            return ZoneInfo(name), name, True
        except Exception as e:  # noqa: BLE001 — невалидная зона → в fallback
            log.warning("_resolve_brief_tz: ZoneInfo(%r) упал: %r", name, e)
    try:
        import config_db as _cfg
        last = _cfg.get_config(_ACTIVE_TZ_KEY)
    except Exception:  # noqa: BLE001
        last = None
    fb = last or os.environ.get("HEALTH_TZ") or _HOME_TZ_NAME
    try:
        return ZoneInfo(fb), fb, False
    except Exception:  # noqa: BLE001 — даже fallback битый → жёсткий дефолт
        return TZ, str(TZ.key), False


def _store_active_tz(name: str) -> None:
    try:
        import config_db as _cfg
        _cfg.upsert_config(_ACTIVE_TZ_KEY, value_text=name, category="schedule",
                           source="morning_brief_timing")
    except Exception as e:  # noqa: BLE001
        log.warning("_store_active_tz(%s): %r", name, e)


async def _alert_tz_fallback(context: ContextTypes.DEFAULT_TYPE):
    """Громкий сенсор: свежий GPS-tz не получен, держим last-good (не молча-UTC)."""
    tzn = (context.job.data or {}).get("tz", "?")
    await asyncio.to_thread(notify.fault,
        f"scheduled._alert_tz_fallback: GPS timezone unavailable, using last-good {tzn}; check timezonefinder and GPS",
        person_key=None)


def _apply_morning_schedule(app) -> str:
    """(Пере)регистрирует morning_report на время-из-таблицы + tz-из-GPS. Идемпотентно
    (снимает прежний job по имени). Провал tz → держим last-good + сенсор в Telegram."""
    tz, tz_name, ok = _resolve_brief_tz()
    if ok:
        _store_active_tz(tz_name)
    else:
        log.error("BRIEF_TZ_FALLBACK: свежий GPS-tz не получен — держу last-good %s", tz_name)
        try:
            app.job_queue.run_once(_alert_tz_fallback, when=5, name="tz_fallback_alert",
                                   data={"tz": tz_name})
        except Exception as e:  # noqa: BLE001
            log.warning("tz_fallback_alert schedule failed: %r", e)
    for j in app.job_queue.get_jobs_by_name("morning_report"):
        j.schedule_removal()
    bt = _brief_time()
    app.job_queue.run_daily(
        send_morning_report,
        time=time(hour=bt.hour, minute=bt.minute, tzinfo=tz),
        name="morning_report",
        job_kwargs={"misfire_grace_time": 1800},
    )
    log.info("morning_report @ %02d:%02d %s (gps_ok=%s)", bt.hour, bt.minute, tz_name, ok)
    return tz_name


def _bump_reschedule_heartbeat():
    """Heartbeat живости 12ч-джоба reschedule_local (читает check_reschedule_liveness
    в integrity_tests). Бампается при КАЖДОМ запуске — доказывает, что джоб фактически
    срабатывает, а не тихо умер (иначе бриф застынет в старом поясе при поездке)."""
    try:
        import config_db as _cfg
        from datetime import timezone as _tz_utc
        _cfg.upsert_config("schedule.reschedule_last_run", value_text="ok",
                           value_json=_rhythm(_RESCHED_INTERVAL_S, _dt.now(_tz_utc.utc)),
                           category="schedule", source="reschedule_local")
    except Exception as e:  # noqa: BLE001
        log.warning("reschedule heartbeat bump failed: %r", e)


async def _reschedule_local(context: ContextTypes.DEFAULT_TYPE):
    """Каждые 12ч: сменился GPS-tz (поездка) → переставить morning_report. Сравнение с
    last-good из config (не лезем во внутренности APScheduler). Провал tz → держим текущее."""
    app = context.application
    _bump_reschedule_heartbeat()  # живость: доказать, что джоб срабатывает
    _tz, tz_name, ok = _resolve_brief_tz()
    if not ok:
        log.error("RESCHEDULE_LOCAL: tz-резолв провалился — держу текущее расписание")
        return
    try:
        import config_db as _cfg
        cur = _cfg.get_config(_ACTIVE_TZ_KEY)
    except Exception:  # noqa: BLE001
        cur = None
    if tz_name == cur:
        return
    _apply_morning_schedule(app)
    log.info("RESCHEDULE_LOCAL: tz %s → %s", cur, tz_name)
    chat_id = get_chat_id()
    if chat_id:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=i18n.t("jobs.timezone_changed", previous=cur or '?', current=tz_name))
        except Exception as e:  # noqa: BLE001
            log.warning("reschedule notify failed: %r", e)


# ── Утренний отчёт ─────────────────────────────────────────────────────────

def _commit_gate_state(specs):
    """Коммит FSM-состояния анти-повтор-гейта в context_cards тенанта — ТОЛЬКО после
    подтверждённой доставки брифа (квитанция: кулдаун-часы = реально доставленное)."""
    import health_db as _db
    import brief_state as _bs
    with _db.get_conn() as _c:
        _bs.commit(specs, _c)


def _commit_note_receipts(fact_ids):
    """Отметки «квитанция показана» по фактам arbiter_unverified — ТОЛЬКО после доставки
    (RYW), рядом с FSM гейта и watermark. Нить brief-repeat, 2026-08-04."""
    import memory_facts_db as _mf
    _mf.mark_receipts_shown(fact_ids)


def _commit_location_watermark(state):
    """Watermark локации (последний объявленный город/дом) → config тенанта, ТОЛЬКО после
    доставки (RYW): бриф упал → watermark не сдвинут → событие повторится завтра (A)."""
    import location_signal as _ls
    _ls.set_location_watermark(state)


def critical_header(crit: list) -> str:
    """Шапка недостоверности человеку. Пустой список → пустая строка.

    Без имён датчиков и без команды починки (решение владельца 28.09): человеку
    они ничего не говорят, а починку ведёт оператор — триаж ему уже написал.
    Имена датчиков — в файле отчёта (critical_header_file)."""
    if not crit:
        return ""
    import i18n
    return i18n.t("jobs.brief.data_doubt_header")


def critical_header_file(crit: list) -> str:
    """Шапка недостоверности для ФАЙЛА брифа. Пустой список → пустая строка.

    Только ИМЕНА датчиков: detail несёт значения анализов, а шапка едет и в
    Telegram, и в файл отчёта (§19). Усечение до пяти названо вслух — молчаливый
    предел читался бы как «столько и было».
    """
    if not crit:
        return ""
    shown = "\n".join(f"• {n}" for n, _ in crit[:5])
    more = f"\n…и ещё {len(crit) - 5}" if len(crit) > 5 else ""
    return (f"🛑 Монитор нашёл критическое ({len(crit)}). Цифрам в отчёте ниже "
            f"верить нельзя, пока не починено:\n{shown}{more}\n\n"
            "Отчёт не задержан намеренно: сутки без наблюдения дороже отчёта с "
            "меткой. Починка: ./run_checks.sh --scheduled на Studio.")


async def send_morning_report(context: ContextTypes.DEFAULT_TYPE):
    chat_id = get_chat_id()
    if not chat_id:
        log.warning("chat_id не известен — пропускаю")
        return

    # Метка недостоверности нужна в самом отчёте: отдельный алерт
    # о блокировке не доказывает, что производитель отчёта остановлен.
    # Критический отказ монитора должен быть виден читателю.
    # Метка сохраняет наблюдение и сообщает ограничение там,
    # где принимается решение; задержка отчёта лишает этого наблюдения.
    # Показываем ИМЕНА датчиков, не detail: detail несёт значения анализов (§19).
    import triage_agent as _triage
    _verdict, _why = await asyncio.to_thread(_triage.latest_verdict, True)
    _crit = _verdict.get("critical") or []
    if _crit:
        log.error("BRIEF_CRITICAL_HEADER critical=%d", len(_crit))
    elif _why:
        # Метки нет — и говорим, почему. Молчание здесь неотличимо от «спросил и
        # всё чисто», а это разные вещи (§14: «датчик мёртв» ≠ «данные целы»).
        log.warning("BRIEF_NOT_GATED %s", _why)

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    await asyncio.to_thread(refresh_data)

    # Oura пишет сон на дату пробуждения = сегодня
    target = get_today()
    # Инструмент (morning-brief-timing): пришла ли ночь Oura К МОМЕНТУ брифа — мерим
    # публикационный лаг Oura за неделю живых прогонов. Только лог (текст брифа НЕ трогаем —
    # формулировка confab = нить brief-neutralization). Grep: OURA_NIGHT_AT_BRIEF.
    try:
        import os as _os3
        with db.get_conn() as _c3:
            _row = _c3.execute("SELECT sleep_score FROM daily_metrics WHERE date=?",
                               (str(target),)).fetchone()
        _present = bool(_row and _row[0] is not None)
        log.info("OURA_NIGHT_AT_BRIEF date=%s present=%s tenant=%s",
                 target, _present, _os3.environ.get("HEALTH_DATA_DIR", "owner"))
        # BL-BRIEF-TIMING-1: лог ротируется за дни — замер копится в БД тенанта (60 дней).
        # Первый бриф дня — его и судим: повторный запуск (catchup, после опросника)
        # идёт позже, ночь к нему уже доехала, и «True» затёр бы настоящий промах.
        from config_db import get_config, upsert_config
        _hist = get_config("brief.oura_night_at_brief", {}) or {}
        _hist.setdefault(str(target), _present)
        upsert_config("brief.oura_night_at_brief",
                      value_json=dict(sorted(_hist.items())[-60:]),
                      category="brief", source="morning_brief")
    except Exception as _e3:  # noqa: BLE001
        log.warning("OURA_NIGHT_AT_BRIEF measure failed: %r", _e3)
    try:
        _gate_sink = {}
        report, sn_result = await asyncio.to_thread(gp.generate_daily_report, target, _gate_sink)
    except Exception as e:
        log.error(f"Ошибка GP daily report: {e}", exc_info=True)
        # parse_mode=None: текст ошибки может содержать _/*/[] (напр.
        # _build_clinical_history) → Markdown-парс валит отправку и даже это
        # сообщение об ошибке тоже может не дойти.
        await send_long(context.bot, chat_id,
                        await asyncio.to_thread(notify.fault,
                            f"scheduled.send_morning_report: {type(e).__name__}: {e}",
                            person_key="jobs.morning.failed"),
                        parse_mode=None)
        return

    # Шапка недостоверности — ПЕРВОЙ, раньше срочных алертов: они опираются на
    # те же данные. Отдельным сообщением с parse_mode=None, а не склейкой с
    # отчётом: имена датчиков несут `_` (lab_results), под Markdown это открытие
    # italic — и может сорвать доставку всего отчёта.
    _header = critical_header(_crit)
    if _header:
        await send_long(context.bot, chat_id, _header, parse_mode=None)

    # Срочные алерты — до отчёта
    if sn_result and sn_result.get("max_level") in ("urgent", "critical"):
        _urgent = sn_result["urgent_message"]
        if _crit:   # в то же утро монитор нашёл сбой данных — говорим это рядом с тревогой
            _urgent += "\n\n" + i18n.t("safety.data_doubt" + (
                "_critical" if sn_result.get("max_level") == "critical" else ""))
        await send_long(context.bot, chat_id, _urgent, parse_mode=None)

    # Сохраняем markdown-версию
    # tenant-aware (2026-07-03): раньше захардкожено в iCloud владельца — отчёт
    # партнёра писался в каталог владельца. Теперь в data-каталог тенанта.
    import os
    reports_dir = Path(os.environ.get("HEALTH_DATA_DIR")
                       or infra_config.cloud_dir()) / "data" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    # Метка едет и в ФАЙЛ: его читают дашборд и GP-циклы позже, когда сообщение в
    # Telegram уже пролистано. Документ, которому нельзя верить, обязан нести это
    # в себе, а не в соседнем канале.
    _file_header = critical_header_file(_crit)
    (reports_dir / f"{target}.md").write_text(
        f"{_file_header}\n\n---\n\n{report}" if _file_header else report)

    await send_long(context.bot, chat_id, report)
    # Квитанция доставки: FSM гейта коммитим ТОЛЬКО после успешной отправки (риск #1)
    if _gate_sink.get("specs"):
        try:
            await asyncio.to_thread(_commit_gate_state, _gate_sink["specs"])
        except Exception as e:
            log.warning(f"gate FSM commit failed: {e}")
    # Watermark локации — тоже ТОЛЬКО после успешной доставки (RYW), рядом с gate FSM.
    if _gate_sink.get("location_watermark"):
        try:
            await asyncio.to_thread(_commit_location_watermark, _gate_sink["location_watermark"])
        except Exception as e:
            log.warning(f"location watermark commit failed: {e}")
    # Квитанция «записал так, поправь» — отмечаем ПОСЛЕ доставки, там же где FSM гейта
    # и watermark. Бриф не ушёл → отметки нет → квитанция придёт завтра. Обратный порядок
    # терял бы её навсегда, а потеря дороже повтора (нить brief-repeat, риск Р3).
    if _gate_sink.get("note_receipts"):
        try:
            await asyncio.to_thread(_commit_note_receipts, _gate_sink["note_receipts"])
        except Exception as e:
            log.warning(f"note receipts commit failed: {e}")

    # Квартальный пищевой профиль (per-tenant, раз в 3 мес) — гвард сам решает, пора ли.
    try:
        import food_quarterly as _fq
        if await asyncio.to_thread(_fq.maybe_deliver):
            log.info("food_quarterly: профиль доставлен")
    except Exception as e:
        log.warning(f"food_quarterly piggyback failed: {e}")

    # После доставки перевзводим завтрашний бриф со свежим GPS-tz.
    # При смене пояса зафиксированный при регистрации tz устаревает.
    # Перевзвод после доставки ограничивает отставание следующим слотом.
    # Повторный вызов идемпотентен.
    try:
        _apply_morning_schedule(context.application)
    except Exception as e:  # noqa: BLE001 — перевзвод не критичен для доставки
        log.warning(f"morning schedule self-rearm failed: {e}")
    # Experiment-check (Day 7/14/30) снят: конвейер экспериментов ретайрится
    # (BL-EXP-1, 2026-07-10). Жизненный цикл гипотез — только через consilium.


# ── Вечерний чекин: СНЯТ 2026-08-17 (решение владельца) ────────────────────
# Автозапуск в 20:00 местного рассылал вопрос обоим тенантам без их действия.
# Функция send_evening_checkin и регистрация job'ы "evening_checkin" удалены
# целиком, а не спрятаны за флаг: выключенный флаг — это обещание вернуть
# ровно эту механику, а её решено переделать заново. Ручной /checkin
# (handlers/meta.cmd_checkin) жив и ничего не шлёт без команды человека.
# Оракул возврата: tests/integration/test_bot_sprint6c.py
#   ::test_evening_checkin_autostart_is_gone


# ── Специалисты (вс 23:00) ─────────────────────────────────────────────────

async def run_specialists_scheduled(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled вс 23:00 — запускает MDT + follow-up по незакрытым задачам."""
    log.info("Scheduled: запуск MDT специалистов (вс 23:00)...")
    chat_id = get_chat_id()
    try:
        await asyncio.to_thread(gp.run_specialists_and_save)
        log.info("Scheduled: MDT специалисты завершены")
    except Exception as e:
        log.error(f"Ошибка scheduled MDT: {e}", exc_info=True)
    # Follow-up по незакрытым задачам
    if chat_id:
        try:
            overdue = await asyncio.to_thread(db.get_overdue_tasks, days_old=7)
            if overdue:
                from handlers.tasks import task_keyboard
                await send_long(context.bot, chat_id, ta.format_open_tasks_message(overdue),
                                reply_markup=task_keyboard(overdue))
        except Exception as e:
            log.warning(f"Ошибка followup: {e}")


# ── Еженедельный GP-отчёт (вс 07:00) ───────────────────────────────────────

async def send_weekly_report(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled вс 07:00 — GP читает сохранённый MDT и генерирует отчёт.

    Расписание (PTB days convention: 0=Sun..6=Sat — НЕ Python weekday):
      сб 23:00 → specialists_run (MDT синтез)
      вс 07:00 → send_weekly_report (этот job, читает MDT)
    """
    chat_id = get_chat_id()
    if not chat_id:
        return
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    await asyncio.to_thread(refresh_data)
    target = get_today() - timedelta(days=1)
    report = None
    try:
        # GP читает MDT из agent_reports (специалисты уже отработали в сб 23:00)
        report = await asyncio.to_thread(gp.generate_weekly_report, target, False)
        header = i18n.t("jobs.weekly.heading") + report
    except Exception as e:
        log.error(f"Ошибка GP weekly report: {e}")
        header = await asyncio.to_thread(notify.fault,
            f"scheduled.send_weekly_report: {type(e).__name__}: {e}", person_key="jobs.weekly.failed")
    await send_long(context.bot, chat_id, header)
    # Извлекаем задачи из scheduled отчёта
    if report:
        asyncio.create_task(_send_tasks_from_report(
            context.bot, chat_id, report, "gp_weekly", target
        ))

    # ── Напоминания по протоколам ─────────────────────────────────────────
    try:
        protocols = await asyncio.to_thread(hai.get_active_protocols)
        if protocols:
            today = get_today()
            due_reminders = []
            for p in protocols:
                created = date.fromisoformat(p["created_at"][:10])
                days_elapsed = (today - created).days
                reminder_days = _j.loads(p.get("reminder_days") or "[]")
                # Проверяем: прошло ли ровно N дней (±3 дня окно для weekly)
                for rd in reminder_days:
                    if abs(days_elapsed - rd) <= 3:
                        due_reminders.append((p, days_elapsed, rd))
                        break

            if due_reminders:
                lines = [i18n.t("jobs.protocols.review")]
                for p, elapsed, rd in due_reminders:
                    lines.append(f"\n#{p['id']} {p['title']}")
                    lines.append(f"{p['behavior']}")
                    lines.append(i18n.t("jobs.protocols.age", elapsed=fmt_count(elapsed, "days"), day=rd))
                from handlers.hypotheses import protocol_keyboard
                lines.append(i18n.t("jobs.protocols.retire"))
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="\n".join(lines),
                    reply_markup=protocol_keyboard([p for p, _, _ in due_reminders])
                )
            elif protocols:
                # Раз в 4 недели показываем все протоколы как сводку
                week_num = today.isocalendar()[1]
                if week_num % 4 == 0:
                    lines = [i18n.t("jobs.protocols.summary")]
                    for p in protocols:
                        lines.append(f"  #{p['id']} {p['title']} — {p.get('frequency', '—')}")
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="\n".join(lines)
                    )
    except Exception as e:
        log.warning(f"Protocol reminders: {e}")


# ── Ежемесячный GP-отчёт (1-го 09:30) ──────────────────────────────────────

async def send_monthly_check(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled 1-го числа — GP ежемесячный отчёт."""
    chat_id = get_chat_id()
    if not chat_id:
        return
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    await asyncio.to_thread(refresh_data)
    target = get_today() - timedelta(days=1)
    try:
        report = await asyncio.to_thread(gp.generate_monthly_report, target)
        header = i18n.t("jobs.monthly.heading") + report
    except Exception as e:
        log.error(f"Ошибка GP monthly report: {e}")
        header = await asyncio.to_thread(notify.fault,
            f"scheduled.send_monthly_report: {type(e).__name__}: {e}", person_key="jobs.monthly.failed")
    await send_long(context.bot, chat_id, header)


# ── Рекомендации по доменам (каждые 2ч, 9:00-21:00) ────────────────────────

async def check_recommendations_scheduled(context):
    """
    Каждые 2 часа: проверяем все домены с активными протоколами.
    Отправляем максимум 1 уведомление в день (required — до 2).
    Активно только с 9:00 до 21:00 по локальному времени.
    """
    chat_id = get_chat_id()
    if not chat_id:
        return

    # Окно доставки и границы дня считаются в поясе текущего тенанта.
    # Общий TZ модуля неверен, если у тенантов разные настройки.
    tz = _tenant_tz(TZ)
    now_h = _dt.now(tz=tz).hour
    if now_h < 9 or now_h > 21:
        return

    # Получаем уникальные домены из активных протоколов
    all_protocols = db.get_active_protocols()
    domains = list({p["domain"] for p in all_protocols if p.get("domain")})

    today_str = str(_dt.now(tz=tz).date())
    sent_data = context.bot_data.get(_RECO_SENT_KEY, {})

    for domain in domains:
        result = evaluate_domain_need(domain)
        if not result["needed"]:
            continue

        domain_key = f"{domain}_{today_str}"
        sent_today = sent_data.get(domain_key, 0)
        max_sends  = 2 if result["urgency"] == "required" else 1
        if sent_today >= max_sends:
            continue

        proto_titles = ", ".join(p["title"] for p in result["protocols"])
        urgency_emoji = "🚨" if result["urgency"] == "required" else "🧠"

        main_reason = result["reasons"][0] if result["reasons"] else ""
        extra = len(result["reasons"]) - 1
        extra_text = i18n.t("jobs.recommendation.more", count=extra) if extra > 0 else ""
        text = i18n.t("jobs.recommendation.message", icon=urgency_emoji,
                      reason=main_reason, extra=extra_text, plans=proto_titles)

        await send_md(context.bot.send_message, chat_id=chat_id, text=text)
        log.info(f"Рекомендация [{domain}] отправлена ({result['urgency']}): {result['reasons']}")

        sent_data[domain_key] = sent_today + 1
        context.bot_data[_RECO_SENT_KEY] = sent_data


# ── Pending consults на VPS — ВЫПИЛЕНО (2026-07-06, TD-09) ──────────────────
# check_pending_consults опрашивал {FUNNEL_URL}/api/consult/pending каждые 2 мин.
# VPS деком. 2026-04-09; после переезда /api на соседний проект джоб 3 месяца тихо ловил
# отказ (log.DEBUG — не видно) и слал X-Sync-Token чужому приложению.
# Genome-MDT запускается штатно: /consult в боте + monthly_consilium.


# ── Document review (каждые 5 мин) ─────────────────────────────────────────

_DOC_TYPE_LABELS = {
    "lab":          "cards.document.type.lab",
    "oncology":     "cards.document.type.oncology",
    "consultation": "cards.document.type.consultation",
    "discharge":    "cards.document.type.discharge",
    "other":        "cards.document.type.other",
    "skip":         "cards.document.type.skip",
}


_DOC_ATTACH_MAX_MB = 45.0   # запас под лимит Telegram Bot API (50 МБ на sendDocument)


def _resolve_doc_path(source_file: str) -> Path | None:
    """Абсолютный путь к исходнику по `source_file` из очереди.

    В очередь кладут путь ОТНОСИТЕЛЬНО корня документов (`import_all.py`:
    `pdf.relative_to(HEALTH)`), поэтому сам по себе он не открывается. Корень берём
    у `import_all` — единственного писателя этих строк, — а не четвёртой копией
    литерала: два дома у корня уже есть (`import_all.HEALTH`, `lab_extractor.HEALTH_DIR`),
    третий сделал бы расхождение вопросом времени. Легаси-строку с абсолютным путём
    `pathlib` пропустит как есть. None = файла нет на диске.
    """
    try:
        import import_all  # lazy: бот уже держит его через handlers.callbacks
        p = Path(import_all.HEALTH) / source_file
        return p if p.is_file() else None
    except Exception as e:  # silent-ok: логируем; разрешение пути не вправе уронить алерт
        log.warning("doc_review: путь к %s не разрешён: %s", source_file, e)
        return None


async def _send_doc_review_message(bot, review: dict) -> None:
    """Карточка подтверждения типа документа — вместе с САМИМ документом.

    Вопрос «что это?» без файла заставляет владельца идти искать документ на диске по
    одному имени, то есть отвечать по памяти либо не отвечать вовсе — а через 48 часов
    `auto_confirm_stale_reviews` подтвердит предложенный тип за него. Гейт, на который
    нечем ответить, — это не гейт (§13: эскалация к человеку оправдана, только если
    человек может вынести вердикт).

    Если приложить нельзя, причина уходит В СООБЩЕНИЕ, а не только в лог: карточка без
    файла и без объяснения неотличима от карточки, где файл не нужен.
    """
    rid  = review["id"]
    fname = review["source_file"].split("/")[-1]
    proposed = review.get("proposed_type", "?")

    _main_keys = ["lab", "oncology", "consultation"]
    _aux_keys  = ["discharge", "other", "skip"]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(i18n.t(_DOC_TYPE_LABELS[k]), callback_data=f"docrev_{rid}_{k}") for k in _main_keys],
        [InlineKeyboardButton(i18n.t(_DOC_TYPE_LABELS[k]), callback_data=f"docrev_{rid}_{k}") for k in _aux_keys],
    ])
    text = i18n.t("cards.document.review", fname=fname, proposed=proposed)

    path = _resolve_doc_path(review["source_file"])
    if not is_owner():
        # FAIL-CLOSED (§13 ступень 2). Корень документов — литерал ВЛАДЕЛЬЦА
        # (`import_all.HEALTH`), тенант-осознанного дома у него нет. Для партнёра тот же
        # относительный `source_file` разрешится в папку владельца, и совпадение имени
        # отдало бы партнёру ЧУЖОЙ документ — ровно класс бага 2026-07-01 (секреты
        # владельца в процессе тенанта). Пока корень не тенант-осознан, вложение только
        # у владельца; текстовая карточка тенанту приходит как раньше.
        await asyncio.to_thread(notify.fault,
            f"scheduled.doc_review: attachment owner-only review_id={rid}", person_key=None)
    elif path is None:
        await asyncio.to_thread(notify.fault,
            f"scheduled.doc_review: file missing review_id={rid}", person_key=None)
    else:
        size_mb = path.stat().st_size / 1048576
        if size_mb > _DOC_ATTACH_MAX_MB:
            text += i18n.t("cards.document.too_large")
        else:
            try:
                await send_md(bot.send_document, chat_id=owner_chat_id(), document=path, filename=fname, caption=text, reply_markup=keyboard)
                return
            except TelegramError as e:
                # Алерт важнее вложения: файл не ушёл — карточка уходит всё равно.
                log.warning("doc_review id=%s: send_document отклонён (%s) → шлём текстом", rid, e)
                await asyncio.to_thread(notify.fault,
                    f"scheduled.doc_review: attachment rejected review_id={rid}: {type(e).__name__}: {e}", person_key=None)

    await send_md(bot.send_message, chat_id=owner_chat_id(), text=text, reply_markup=keyboard)


async def check_pending_doc_reviews(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job: проверяет pending_doc_reviews и отправляет уведомления."""
    try:
        # Авто-подтверждение старых (48ч)
        stale = db.auto_confirm_stale_reviews(hours=48)
        if stale:
            log.info(f"check_pending_doc_reviews: авто-подтверждено {stale} устаревших")

        pending = db.get_pending_doc_reviews()
        for review in pending:
            try:
                await _send_doc_review_message(context.bot, review)
                db.mark_doc_review_sent(review["id"])  # не слать повторно
                log.info(f"check_pending_doc_reviews: отправлено уведомление id={review['id']}")
            except Exception as e:
                log.warning(f"check_pending_doc_reviews: ошибка отправки id={review['id']}: {e}")
    except Exception as e:
        log.error(f"check_pending_doc_reviews: {e}")


# ── Человек-гейт: подтверждение извлечённых режимов лечения ─────────────────

_MODALITY_KEYS = {
    "chemo": "treatment.modality.chemo", "chemoradiation": "treatment.modality.chemoradiation",
    "immunotherapy": "treatment.modality.immunotherapy", "targeted": "treatment.modality.targeted",
    "radiation": "treatment.modality.radiation",
}


async def _send_treatment_review_message(bot, med: dict) -> None:
    """Карточка подтверждения извлечённого режима лечения."""
    modality = med.get("modality") or ""
    mod = i18n.t(_MODALITY_KEYS[modality]) if modality in _MODALITY_KEYS else modality or "—"
    cyc = med.get("cycles_completed")
    cyc_str = i18n.t("treatment.review.cycles", count=cyc) if cyc else ""
    dates = f"{med.get('start_date') or '?'} – {med.get('end_date') or '?'}"
    agents_raw = med.get("agents")
    agents_str = ""
    if agents_raw:
        try:
            _al = _j.loads(agents_raw) if isinstance(agents_raw, str) else agents_raw
        except Exception:
            _al = None
        if _al:
            agents_str = i18n.t("treatment.review.agents", agents=" + ".join(str(a) for a in _al))
    text = i18n.t("treatment.review.message", name=med.get("name"), modality=mod,
                  cycles=cyc_str, agents=agents_str, dates=dates)
    keyboard = [[
        InlineKeyboardButton(i18n.t("treatment.review.accept"), callback_data=f"medrev_{med['id']}_ok"),
        InlineKeyboardButton(i18n.t("treatment.review.skip"), callback_data=f"medrev_{med['id']}_skip"),
    ]]
    await send_md(bot.send_message, chat_id=owner_chat_id(), text=text, reply_markup=InlineKeyboardMarkup(keyboard))


async def check_pending_treatments(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job: карточки подтверждения для извлечённых, но не подтверждённых режимов."""
    try:
        pending = db.get_unsent_proposed_medications()
        for med in pending:
            try:
                await _send_treatment_review_message(context.bot, med)
                db.mark_medication_gate_sent(med["id"])
                log.info(f"check_pending_treatments: карточка отправлена med id={med['id']}")
            except Exception as e:
                log.warning(f"check_pending_treatments: ошибка отправки id={med['id']}: {e}")
    except Exception as e:
        log.error(f"check_pending_treatments: {e}")


# ── Регистрация всех jobs ──────────────────────────────────────────────────

async def send_genome_update_monthly(context: ContextTypes.DEFAULT_TYPE):
    """Ежемесячный синк ClinVar + авто-уведомление (audit 2026-06-17).

    Раньше run_monthly_update вызывался только вручную через /genome_update.
    Теперь идёт по расписанию; нарратив значимых изменений (риск вверх) сам
    приходит владельцу, лог помечается отправленным.

    При significant > 0 дополнительно (2026-06-27):
    - Block C: создаётся задача на пересмотр гипотез (health_db.save_task)
    - Block B: запускается перегенерация конституций (generate_constitutions.py subprocess)
    """
    chat_id = get_chat_id()
    try:
        import genome_update_agent as gua
        result = await asyncio.to_thread(gua.run_monthly_update)
    except Exception as e:
        log.error(f"send_genome_update_monthly error: {e}", exc_info=True)
        return
    changed = result.get("changed", 0)
    significant = result.get("significant", 0)
    narrative = result.get("narrative")
    log_id = result.get("log_id")
    if significant > 0 and narrative:
        if not chat_id:
            log.warning("send_genome_update_monthly: chat_id неизвестен — нарратив не отправлен")
            return
        await send_md(context.bot.send_message, chat_id=chat_id, text=i18n.t("jobs.genome.monthly_update", changed=changed, significant=significant))
        await send_long(context.bot, chat_id,
                        i18n.t("genome.reply.update_explanation", narrative=narrative),
                        parse_mode=None)
        if log_id:
            db.mark_genome_log_sent(log_id)

        # Block C (задача человеку «пересмотреть гипотезы») снят 28.09 — решение владельца:
        # работу системы в список человека не кладём; у задачи не было исполнителя.

        # Block B: перегенерация конституций (2026-06-27)
        await context.bot.send_message(
            chat_id=chat_id,
            text=i18n.t("jobs.genome.updating_rules"),
        )
        try:
            import subprocess as _subprocess
            _regen = await asyncio.to_thread(
                _subprocess.run,
                ["python3", "generate_constitutions.py"],
                cwd=str(Path.home() / "health_scripts"),
                capture_output=True,
                text=True,
                timeout=700,
            )
            if _regen.returncode != 0:
                log.error(
                    "generate_constitutions returned %d: %s",
                    _regen.returncode,
                    _regen.stderr[:500],
                )
                await asyncio.to_thread(notify.fault,
                    f"scheduled.send_genome_update_monthly: generate_constitutions failed code={_regen.returncode}",
                    person_key=None)
            else:
                log.info("send_genome_update_monthly: конституции успешно перегенерированы")
        except Exception as e:
            log.error("send_genome_update_monthly: regen failed: %s", e, exc_info=True)  # silent-ok: regen failure не роняет job
            await asyncio.to_thread(notify.fault,
                f"scheduled.send_genome_update_monthly: regeneration: {type(e).__name__}: {e}", person_key=None)
    else:
        log.info(f"send_genome_update_monthly: изменений {changed}, значимых 0 — уведомление не нужно")


async def check_prs_updates_monthly(context: ContextTypes.DEFAULT_TYPE):
    """Ежемесячная проверка обновлений весов PGS + пересчёт Phase H (Block D1, 2026-06-27).

    Алгоритм:
    1. Для каждого pgs_id из pgs_catalog — перекачать веса (genome_weights.download_and_import).
    2. Если есть новые SNP (n_inserted > 0) — пересчитать PRS через Phase H.
    3. Сравнить raw_score до/после и отправить diff в Telegram.
    4. При отсутствии изменений — тихо логируем.

    Запускается после send_genome_update_monthly (05:00) — в 06:00 1-го числа.
    """
    chat_id = get_chat_id()

    try:
        import genome_weights as gw
        import prs_pipeline as pp
    except ImportError as e:
        log.error(f"check_prs_updates_monthly: import failed: {e}")
        return

    # Получаем список PGS из каталога
    def _read_catalog():
        with db.get_conn() as conn:
            return conn.execute("SELECT pgs_id, trait_label FROM pgs_catalog").fetchall()

    try:
        pgs_rows = await asyncio.to_thread(_read_catalog)
    except Exception as e:
        log.error(f"check_prs_updates_monthly: catalog read failed: {e}", exc_info=True)  # silent-ok: таблица может отсутствовать
        return

    if not pgs_rows:
        log.info("check_prs_updates_monthly: pgs_catalog пуст — нечего обновлять")
        return

    # Перекачиваем веса для каждого PGS
    total_inserted = 0
    updated_ids: list[str] = []
    for row in pgs_rows:
        pgs_id = row[0]
        try:
            res = await asyncio.to_thread(gw.download_and_import, pgs_id)
            n = res.get("n_inserted", 0)
            if n > 0:
                updated_ids.append(pgs_id)
                total_inserted += n
                log.info(f"check_prs_updates_monthly: {pgs_id} +{n} новых SNP")
        except Exception as e:
            log.warning(f"check_prs_updates_monthly: {pgs_id} download failed: {e}")  # silent-ok: продолжаем остальные

    if total_inserted == 0:
        log.info(f"check_prs_updates_monthly: весов не изменилось ({len(pgs_rows)} PGS проверено)")
        return

    # Читаем текущие raw_scores ДО пересчёта (Phase H затрёт их)
    def _read_before():
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT pgs_id, raw_score FROM prs_scores "
                "WHERE rowid IN (SELECT MAX(rowid) FROM prs_scores GROUP BY pgs_id)"
            ).fetchall()
            return {r[0]: r[1] for r in rows}

    before_scores: dict = {}
    try:
        before_scores = await asyncio.to_thread(_read_before)
    except Exception as e:
        log.warning(f"check_prs_updates_monthly: before_scores read failed: {e}")  # silent-ok: diff будет без «до»

    if chat_id:
        await send_md(context.bot.send_message, chat_id=chat_id,
                      text=i18n.t("jobs.genome.recalculating", count=total_inserted))

    # Phase H — пересчёт PRS
    def _run_phase_h():
        with db.get_conn() as conn:
            gid = conn.execute("SELECT MAX(id) FROM genome_imports").fetchone()[0]
            if not gid:
                return None
            return pp.run(conn, gid)

    phase_h = None
    try:
        phase_h = await asyncio.to_thread(_run_phase_h)
    except Exception as e:
        log.error(f"check_prs_updates_monthly: Phase H failed: {e}", exc_info=True)
        await asyncio.to_thread(notify.fault,
            f"scheduled.check_prs_updates_monthly: Phase H: {type(e).__name__}: {e}", person_key=None)
        return

    if not phase_h:
        log.warning("check_prs_updates_monthly: genome_imports пуст — Phase H пропущена")
        return

    # Строим diff before → after
    after_scores = phase_h.get("results", {})
    diff_lines: list[str] = []
    for pgs_id in updated_ids:
        after = after_scores.get(pgs_id, {})
        b_raw = before_scores.get(pgs_id)
        a_raw = after.get("raw_score")
        label = after.get("trait_label") or pgs_id
        cov   = after.get("coverage_pct", 0)
        if b_raw is not None and a_raw is not None:
            delta = a_raw - b_raw
            sign  = "+" if delta >= 0 else ""
            diff_lines.append(
                i18n.t("jobs.genome.score_changed", label=label, before=b_raw, after=a_raw,
                       sign=sign, delta=delta, coverage=cov)
            )
        elif a_raw is not None:
            diff_lines.append(i18n.t("jobs.genome.score_new", label=label, value=a_raw, coverage=cov))

    if chat_id:
        msg = i18n.t("jobs.genome.summary_heading") + ("\n".join(diff_lines) if diff_lines else i18n.t("jobs.genome.no_score_changes"))
        await send_md(context.bot.send_message, chat_id=chat_id, text=msg)

    log.info(
        "check_prs_updates_monthly: завершено. total_inserted=%d updated=%s",
        total_inserted,
        updated_ids,
    )


async def discover_pgs_monthly(context: ContextTypes.DEFAULT_TYPE):
    """Ежемесячный полный скан PGS Catalog — авто-импорт новых scoring files + Phase H.

    Block D2 (2026-06-27): запускается 2-го числа 07:00 — после D1 (1-го 06:00).
    Фильтр качества: GRCh38 harmonized + num_variants >= 50.
    Phase H coverage — финальный фильтр релевантности.
    """
    chat_id = get_chat_id()
    log.info("discover_pgs_monthly: start")

    try:
        result = await asyncio.to_thread(pgsd.discover_and_import_new_scores)
    except Exception as exc:
        log.error("discover_pgs_monthly: failed: %s", exc, exc_info=True)
        await asyncio.to_thread(notify.fault,
            f"scheduled.discover_pgs_monthly: {type(exc).__name__}: {exc}", person_key=None)
        return

    imported        = result.get("imported",       [])
    errors          = result.get("errors",         [])
    snapshot_error  = result.get("snapshot_error", None)

    # Snapshot failure — всегда алерт, независимо от импортов
    if snapshot_error:
        await asyncio.to_thread(notify.fault,
            f"scheduled.discover_pgs_monthly: pre-op snapshot failed: {snapshot_error}", person_key=None)
        log.error("discover_pgs_monthly: snapshot failed: %s", snapshot_error)
    if errors:
        await asyncio.to_thread(notify.fault,
            f"scheduled.discover_pgs_monthly: import errors={len(errors)}", person_key=None)

    if not imported:
        log.info(
            "discover_pgs_monthly: ничего нового (%d существующих, %d не прошли качество)",
            result.get("skipped_existing", 0),
            result.get("skipped_quality",  0),
        )
        return

    # ПРИГЛУШЕНО 2026-07-02 (решение владельца): рутинный batch-импорт (до 100
    # моделей/прогон во время backfill каталога) больше НЕ спамит списком —
    # сырые PGS per-batch не actionable (нет перцентиля, европ.-калибровка,
    # дырявое покрытие SNP). Сохранены: alert об ошибке и о сбое snapshot (выше).
    # Итоговый alert — ОДИН раз, когда каталог допройден (capped=False).
    # Пока backfill идёт (capped=True) — только лог.
    if result.get("capped"):
        log.info(
            "discover_pgs_monthly: +%d моделей, backfill продолжается — TG-alert приглушён",
            len(imported),
        )
        return

    # Каталог допройден — единственный итоговый alert
    if chat_id:
        phtail = i18n.t("jobs.genome.recalculated") if result.get("phase_h_run") else ""
        await context.bot.send_message(
            chat_id=chat_id,
            text=i18n.t("jobs.genome.catalog_done", count=len(imported),
                        dashboard=i18n.t("jobs.genome.dashboard"), tail=phtail),
        )

    log.info(
        "discover_pgs_monthly: done (final). imported=%d errors=%d phase_h=%s",
        len(imported), len(errors), result.get("phase_h_run"),
    )


def _schedule_morning_catchup(app):
    """Если бот стартует ПОСЛЕ 08:30, а отчёт за сегодня ещё не сформирован —
    запланировать одноразовую досылку через 60с. Идемпотентно: маркер —
    наличие файла reports/<today>.md (send_morning_report пишет его при успехе),
    поэтому повторный рестарт в тот же день не пошлёт дубль."""
    import os
    tz, _tzn, _ok = _resolve_brief_tz()
    bt = _brief_time()
    now = _dt.now(tz)
    slot = now.replace(hour=bt.hour, minute=bt.minute, second=0, microsecond=0)
    if now <= slot:
        return  # обычное расписание сработает сегодня — досылка не нужна
    reports_dir = Path(os.environ.get("HEALTH_DATA_DIR")
                       or infra_config.cloud_dir()) / "data" / "reports"
    today_md = reports_dir / f"{get_today()}.md"
    if today_md.exists():
        return  # отчёт за сегодня уже сформирован
    log.info("morning_report catch-up: рестарт после 08:30, нет %s — досылаю через 60с",
             today_md.name)
    app.job_queue.run_once(send_morning_report, when=60, name="morning_report_catchup")


# ── Непрерывная консолидация памяти (A): nightly + человек-гейт SUPERSEDE ─────

async def _send_consolidation_card(bot, prop: dict) -> None:
    """Карточка человек-гейта: пометить устаревший факт (SUPERSEDE) или оставить.
    PLAIN TEXT (parse_mode=None): значения фактов — JSON со скобками/подчёркиваниями,
    ломают Markdown-парсер Telegram (детект-без-доставки, инцидент 2026-07-05).
    Текст карточки — в memory_consolidation (домен + под регресс-тестом копии)."""
    import memory_consolidation as _mc
    text = _mc.consolidation_card_text(prop)
    keyboard = [[
        InlineKeyboardButton(i18n.t("cards.memory.remove"), callback_data=f"memcons_{prop['id']}_ok"),
        InlineKeyboardButton(i18n.t("cards.memory.keep"), callback_data=f"memcons_{prop['id']}_skip"),
    ]]
    await bot.send_message(chat_id=owner_chat_id(), text=text,
                           parse_mode=None,
                           reply_markup=InlineKeyboardMarkup(keyboard))


async def run_nightly_consolidation(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Nightly непрерывная консолидация (A). Тяжёлый LLM propose — в потоке (не блокирует
    бота). Авто-применяет DEFER+STALE (обратимо); SUPERSEDE-противоречия шлёт на
    человек-гейт в Telegram. Изолировано: сбой не роняет джоб-очередь."""
    import memory_consolidation as mc
    from datetime import timezone as _tz_utc
    started = _dt.now(_tz_utc.utc)
    try:
        summary = await asyncio.to_thread(mc.run_nightly)
        log.info(f"run_nightly_consolidation: {summary}")
        # A1-доставка: расхождения фактов с объективной Oura → тот же SUPERSEDE-гейт
        # (было detection-without-delivery: run_shadow считал, disagree уходил в лог).
        import memory_truthcheck as _tc
        odis = await asyncio.to_thread(_tc.propose_oura_disagreements)
        log.info(f"oura-disagree→supersede: {odis}")
        for prop in await asyncio.to_thread(mc.pending_supersedes):
            try:
                await _send_consolidation_card(context.bot, prop)
                await asyncio.to_thread(mc.mark_supersede_sent, prop["id"])
            except Exception as e:
                log.warning(f"run_nightly_consolidation: отправка id={prop.get('id')}: {e}")
        # E1 liveness: heartbeat в КОНЦЕ джоба — датчик увидит, если доставка тихо умрёт
        # (иначе disagree/supersede не доедут = детект-без-доставки на уровне джоба).
        import config_db as _cfg
        # Ритм — суточный (run_daily ниже): _DAY_S, тот же смысл, что у регистрации.
        await asyncio.to_thread(_cfg.upsert_config, "_consolidation_delivery_last_run",
                                "ok", None, _rhythm(_DAY_S, started), "heartbeat",
                                "run_nightly_consolidation")
    except Exception as e:
        log.error(f"run_nightly_consolidation: {e}", exc_info=True)


async def check_visual_followups(context: ContextTypes.DEFAULT_TYPE):
    """этап2: жизненный цикл follow-up серии — три перехода за прогон (§9-каденции в config):
    (1) handed_off + фото старше visual_followup_days(7) → напоминание «пришли фото» → awaiting_followup;
    (2) awaiting_followup + напоминание старше visual_decision_after_days(1) без фото → запрос
        «закрыть/продолжить» (кнопки) → awaiting_decision;
    (3) awaiting_decision + без выбора старше visual_close_after_days(1) → авто-закрытие (дефолт «закрыть»).
    Регистрируется ТОЛЬКО при symptom_intake_enabled (см. register)."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    import config_db as _cfg
    import visual_db as _vdb
    chat_id = get_chat_id()
    if not chat_id:
        return
    days = int(_cfg.get_config("visual_followup_days", 7))
    dec_after = int(_cfg.get_config("visual_decision_after_days", 1))
    close_after = int(_cfg.get_config("visual_close_after_days", 1))

    # (1) напоминание прислать свежее фото
    try:
        due = _vdb.get_cases_awaiting_followup_reminder(days)
    except Exception as e:  # noqa: BLE001
        log.warning(f"check_visual_followups: get due: {e}")
        due = []
    for c in due:
        region = c.get("region") or i18n.t("jobs.visual.region_fallback")
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=i18n.t("jobs.visual.fresh_photo", days=fmt_count(days, "days_genitive"), region=region))
            _vdb.mark_followup_reminded(c["id"])
        except Exception as e:  # noqa: BLE001
            log.warning(f"check_visual_followups: reminder {c['id']}: {e}")

    # (2) нет фото после напоминания → спросить закрыть/продолжить
    try:
        decide = _vdb.get_cases_awaiting_decision(dec_after)
    except Exception as e:  # noqa: BLE001
        log.warning(f"check_visual_followups: get decide: {e}")
        decide = []
    for c in decide:
        region = c.get("region") or i18n.t("jobs.visual.region_fallback")
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(i18n.t("jobs.visual.continue"), callback_data=f"visfu_{c['id']}_continue"),
            InlineKeyboardButton(i18n.t("jobs.visual.close"), callback_data=f"visfu_{c['id']}_close"),
        ]])
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=i18n.t("jobs.visual.decision", region=region, days=fmt_count(close_after, "days")),
                reply_markup=kb)
            _vdb.mark_decision_requested(c["id"])
        except Exception as e:  # noqa: BLE001
            log.warning(f"check_visual_followups: decision {c['id']}: {e}")

    # (3) нет выбора → авто-закрытие (дефолт «закрыть»)
    try:
        expired = _vdb.get_cases_decision_expired(close_after)
    except Exception as e:  # noqa: BLE001
        log.warning(f"check_visual_followups: get expired: {e}")
        expired = []
    for c in expired:
        try:
            _vdb.close_visual_case(c["id"])
            region = c.get("region") or i18n.t("jobs.visual.region_fallback")
            await context.bot.send_message(
                chat_id=chat_id,
                text=i18n.t("jobs.visual.closed", region=region))
        except Exception as e:  # noqa: BLE001
            log.warning(f"check_visual_followups: close {c['id']}: {e}")


def register(app):
    """Регистрирует все scheduled JobQueue-задачи в Application."""
    # PTB days convention: 0=Sun, 1=Mon, 2=Tue, 3=Wed, 4=Thu, 5=Fri, 6=Sat
    # (НЕ Python weekday() где 0=Mon). Подтверждено логом
    # cron[day_of_week='sun'] для days=(0,). Проверь это перед правкой дней.
    #
    # misfire_grace_time позволяет догнать пропущенный слот после задержки.
    # Без grace APScheduler может молча отбросить missed occurrence.
    # Для каждого расписания ниже задано своё безопасное окно.

    # Утренняя рассылка — время из system_config, tz из GPS (этап 2 morning-brief-timing).
    # Заменяет литерал time(8,30, _tenant_tz(TZ)): параметр в ТАБЛИЦЕ + местный tz + backstop.
    _apply_morning_schedule(app)

    # Catch-up нужен и после рестарта процесса: заново созданный run_daily
    # планирует следующий слот, а пропущенный сегодня требует досылки.
    _schedule_morning_catchup(app)

    # Пересчёт местного tz (поездка) — раз в 12ч (выбор владельца). run_daily биндит tz на
    # регистрации, поэтому без этого смена пояса не подхватится до рестарта процесса.
    app.job_queue.run_repeating(
        _reschedule_local,
        interval=_RESCHED_INTERVAL_S,
        first=_RESCHED_FIRST_S,
        name="reschedule_local",
    )

    # Пульс службы в контейнере (docker-install, этап 2б): PID бота датчику в соседнем
    # контейнере не виден. Удар из очереди задач доказывает, что цикл событий крутится.
    # Натив — без метки HEALTH_SERVICE_LABEL beat() ничего не делает.
    # Корутина, не lambda: очередь PTB ждёт результат задачи через await, и lambda давала
    # «NoneType can't be used in 'await' expression» в журнал ошибок каждую минуту (244 строки
    # за первые два часа пилота, 30.09). Удар при этом шёл — шумел только журнал.
    import daemon_liveness as _dl

    async def _service_pulse(_ctx):
        _dl.beat()
    app.job_queue.run_repeating(_service_pulse, interval=_dl.PULSE_EVERY_S,
                                first=1, name="service_pulse")

    # этап2 visual follow-up напоминания — раз в день 10:00, ТОЛЬКО при флаге
    # symptom_intake_enabled (иначе не регистрируем; fail-safe try/except не роняет старт).
    try:
        import config_db as _cfg_vf
        if _cfg_vf.get_config("symptom_intake_enabled", False):
            app.job_queue.run_daily(
                check_visual_followups,
                time=time(hour=10, minute=0, tzinfo=_tenant_tz(TZ)),
                name="visual_followups",
                job_kwargs={"misfire_grace_time": 3600},
            )
            log.info("visual_followups job registered (flag ON)")
    except Exception as _vf_e:  # noqa: BLE001
        log.warning(f"visual_followups registration skipped: {_vf_e}")

    # Вечерний чекин (20:00 местного) снят 2026-08-17 — см. комментарий выше по файлу.

    # Специалисты — суббота 23:00 (days=6 = Sat в PTB)
    app.job_queue.run_daily(
        run_specialists_scheduled,
        time=time(hour=23, minute=0, tzinfo=_tenant_tz(TZ)),
        days=(6,),  # суббота (PTB 0=Sun..6=Sat)
        name="specialists_run",
        job_kwargs={"misfire_grace_time": 3600},
    )

    # GP еженедельный отчёт — воскресенье 07:00 (days=0 = Sun в PTB)
    app.job_queue.run_daily(
        send_weekly_report,
        time=WEEKLY_REPORT_TIME.replace(tzinfo=_tenant_tz(TZ)),
        days=WEEKLY_REPORT_DAYS,
        name="weekly_report",
        job_kwargs={"misfire_grace_time": 3600},
    )

    # Ежемесячная проверка — 1-го числа 09:30
    app.job_queue.run_monthly(
        send_monthly_check,
        when=MONTHLY_REPORT_TIME.replace(tzinfo=_tenant_tz(TZ)),
        day=MONTHLY_REPORT_DAY,
        name="monthly_check",
        job_kwargs={"misfire_grace_time": 86400},
    )

    # Геном: ежемесячный синк ClinVar + авто-уведомление — 1-го 05:00 (audit 2026-06-17)
    app.job_queue.run_monthly(
        send_genome_update_monthly,
        when=time(hour=5, minute=0, tzinfo=_tenant_tz(TZ)),
        day=1,
        name="genome_update_monthly",
        job_kwargs={"misfire_grace_time": 86400},
    )

    # PRS: ежемесячная проверка обновлений весов PGS Catalog — 1-го 06:00 (Block D1, 2026-06-27)
    # Запускается после genome_update_monthly (05:00) чтобы не конкурировать за БД.
    app.job_queue.run_monthly(
        check_prs_updates_monthly,
        when=time(hour=6, minute=0, tzinfo=_tenant_tz(TZ)),
        day=1,
        name="prs_updates_monthly",
        job_kwargs={"misfire_grace_time": 86400},
    )

    # PGS Discovery: ежемесячный скан PGS Catalog за новыми scores — 2-го 07:00 (Block D2, 2026-06-27)
    # Запускается на следующий день после D1 чтобы не конкурировать за сетевые ресурсы.
    app.job_queue.run_monthly(
        discover_pgs_monthly,
        when=time(hour=7, minute=0, tzinfo=_tenant_tz(TZ)),
        day=2,
        name="pgs_discovery_monthly",
        job_kwargs={"misfire_grace_time": 86400},
    )

    # Рекомендации по доменам — каждые 2ч (только 09:00–21:00)
    app.job_queue.run_repeating(
        check_recommendations_scheduled,
        interval=7200,
        first=300,
        name="recommendations_check"
    )

    # Опрос pending-консультаций (VPS-эра) выпилен 2026-07-06 — см. tombstone выше.

    # Оценка гипотез консилиумом — понедельник 10:00 (days=1 = Mon в PTB)
    app.job_queue.run_daily(
        check_hypothesis_evaluations,
        time=time(hour=10, minute=0, tzinfo=_tenant_tz(TZ)),
        days=(1,),  # понедельник (PTB 0=Sun..6=Sat)
        name='hypothesis_eval',
        job_kwargs={"misfire_grace_time": 3600},
    )

    # ⚰ 26.09: check_hae_new_metrics снят. Он сканировал iCloud-папку HAE, пустую после перехода
    # на REST (06.07), и 2,5 месяца молча отвечал «новых метрик нет». Новое ловит судья на
    # REST-приёме (hae_checker.judge_payload) + ночной check_hae_arrivals_have_owner.

    # Проверка новых документов — каждые 5 мин
    app.job_queue.run_repeating(
        check_pending_doc_reviews,
        interval=300,
        first=10,
        name="check_pending_doc_reviews"
    )

    # Проверка неизвестных маркеров анализов — каждые 5 мин
    app.job_queue.run_repeating(
        check_pending_field_reviews,
        interval=300,
        first=20,
        name="check_pending_field_reviews"
    )

    # Подтверждение извлечённых режимов лечения (человек-гейт) — каждые 5 мин
    app.job_queue.run_repeating(
        check_pending_treatments,
        interval=300,
        first=40,
        name="check_pending_treatments"
    )

    # Непрерывная консолидация памяти (A) — ежедневно 04:30 (после ночных импортов)
    app.job_queue.run_daily(
        run_nightly_consolidation,
        time=time(hour=4, minute=30, tzinfo=_tenant_tz(TZ)),
        name="memory_consolidation_nightly",
        job_kwargs={"misfire_grace_time": 3600},
    )

    # Outbox заключений консилиума опрашивается периодически:
    # outcome с sent_at=NULL должен доставляться без ожидания рестарта.
    app.job_queue.run_repeating(
        deliver_unsent_outcomes_job,
        interval=300,
        first=60,
        name="deliver_unsent_outcomes",
    )

    # Outbox задач-опросников — каждые 5 мин (2026-09-03): единственный путь начать
    # опрос из бота; без него задача планировщика висела с sent_at=NULL.
    app.job_queue.run_repeating(
        deliver_unsent_assessment_tasks_job,
        interval=300,
        first=90,
        name="deliver_unsent_assessment_tasks",
    )

    # Outbox предложений делает доставку независимой от ручной /report.
    import proposals_db as _pdb
    app.job_queue.run_repeating(
        deliver_pending_proposals_job,
        interval=_pdb.DELIVERY_EVERY_S,
        first=150,
        name="deliver_pending_proposals",
    )

    # Outbox вопросов — каждые 5 мин (2026-09-12, нить question-answer-channel):
    # у вопроса ответ это текст, и до этой нити его некуда было написать —
    # ремайндер принимает только галочку. Дроссель questions.max_per_day внутри.
    app.job_queue.run_repeating(
        deliver_unsent_question_tasks_job,
        interval=300,
        first=120,
        name="deliver_unsent_question_tasks",
    )

    # Подъём кандидатов из памяти малыми партиями; расписание не заменяет
    # ограничение по очереди недоставленных вопросов.
    app.job_queue.run_repeating(
        promote_memory_questions_job,
        interval=_PROMOTE_EVERY_S,
        first=600,
        name="promote_memory_questions",
    )

    # Еженедельный дайджест изменений системы (нить weekly-digest, 2026-09-05): outbox-
    # читатель раз в час, НЕ run_daily — слот вс 09:00 терялся бы при рестарте на коммит
    # (уроки 2026-06-21, 07-03). Решение «слать/ждать» — чистая weekly_digest.due.
    app.job_queue.run_repeating(
        weekly_digest_outbox,
        interval=3600,
        first=180,
        name="weekly_digest_outbox",
    )
    # Точный слот вс 09:00 местного (2026-09-06): часовой читатель тикает с фазой от рестарта
    # (08:41 / 08:54 в первое живое воскресенье → доставка в 09:41 / 09:54), а владелец ждал
    # 09:00. Тот же idempotent-job, тот же mark; часовой остаётся страховкой на пропуск слота.
    # tz биндится на регистрации (как у брифа) — при смене пояса точность уходит, страховка нет.
    _tz_digest, _, _ = _resolve_brief_tz()
    app.job_queue.run_daily(
        weekly_digest_outbox,
        time=time(hour=wd_send_hour(), minute=0, tzinfo=_tz_digest),
        days=(0,),  # воскресенье (PTB 0=Sun..6=Sat)
        name="weekly_digest_0900",
        job_kwargs={"misfire_grace_time": 1800},
    )


# ── Outbox еженедельного дайджеста системы ───────────────────────────────────

def wd_send_hour() -> int:
    import weekly_digest as _wd
    return _wd.SEND_HOUR


_DIGEST_SENT_KEY = "weekly_digest.last_sent_week"
_DIGEST_ALERT_KEY = "weekly_digest.alerted_week"


async def weekly_digest_outbox(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Раз в час: (1) свой per-tenant вердикт над готовым текстом недели — идемпотентно;
    (2) если вс ≥ 09:00 местного, текст есть и ВСЕ тенанты дали pass — шлём и метим
    неделю по квитанции send_long; (3) blocked — служебный алерт оператору на первом же
    тике, wait без вердикта соседа — на первом тике после 09:00 вс (notify_operator: класс
    причины, без текста; один раз в неделю). Генератор — launchd
    weekly_digest.py (сб 22:00, Studio); этот job ничего не генерирует."""
    import weekly_digest as wd
    import config_db as _cfg
    chat_id = get_chat_id()
    if not chat_id:
        return
    tz, _, _ = _resolve_brief_tz()
    now = _dt.now(tz).replace(tzinfo=None)
    week = wd.current_week(now)
    try:
        await asyncio.to_thread(wd.write_tenant_verdict, week)
        digest = wd.read_digest(week)
        verd = wd.verdicts(week, wd.expected_tags())
        last = _cfg.get_config(_DIGEST_SENT_KEY)
    except Exception as e:  # noqa: BLE001 — outbox не должен ронять job-очередь
        log.warning("weekly_digest_outbox: %s: %s", type(e).__name__, e)
        return
    decision = wd.due(now, digest, verd, last)
    if decision == "send":
        ids = await send_long(context.bot, chat_id, wd.text_for(digest, i18n.lang_of()))
        if ids and any(i is not None for i in ids):
            _cfg.upsert_config(_DIGEST_SENT_KEY, value_text=week, category="digest",
                               source="weekly_digest_outbox")
            log.info("weekly_digest_outbox: %s доставлен (%d кусков)", week, len(ids))
        else:
            log.error("weekly_digest_outbox: %s — send_long без квитанции, mark не ставлю", week)
        return
    # Алерт — сразу, не с отсрочкой (замечание владельца 05.09): blocked известен с первого
    # тика после генерации (сб 22:xx); wait без вердикта соседа — с первого тика после 09:00 вс.
    alert_now = (decision == "blocked" and digest is not None) or (
        decision == "wait" and digest is not None and now.isoweekday() == 7 and now.hour >= wd.SEND_HOUR)
    if alert_now:
        if _cfg.get_config(_DIGEST_ALERT_KEY) != week:
            from notify import fault
            missing = [t for t, v in verd.items() if v is None]
            fault(f"weekly_digest: {decision}; missing verdicts={len(missing)}", person_key=None)
            _cfg.upsert_config(_DIGEST_ALERT_KEY, value_text=week, category="digest",
                               source="weekly_digest_outbox")


# ── Outbox заключений консилиума ─────────────────────────────────────────────

async def deliver_unsent_outcomes(app) -> None:
    """
    Доставляет в Telegram заключения консилиумов с sent_at IS NULL —
    независимо от того, кто запустил консилиум (дашборд, бот, планировщик)
    и жив ли клиент, который его запускал. Outbox-замок (delivering_since,
    10 мин) — в hypotheses_db.get_unsent_hypothesis_outcomes.
    Зовётся из post_init (старт бота) и из job deliver_unsent_outcomes (5 мин).
    """
    chat_id = get_chat_id()
    if not chat_id:
        return
    try:
        rows = db.get_unsent_hypothesis_outcomes()
    except Exception as e:
        log.warning(f"deliver_unsent_outcomes: не удалось получить список: {e}")
        return

    if not rows:
        return

    log.info(f"deliver_unsent_outcomes: {len(rows)} недоставленных заключений")
    for row in rows:
        memory_id    = row["memory_id"]
        outcome_id   = row.get("id")     # метим ИМЕННО эту строку, не MAX(id)
        coord_text   = row.get("coordinator_text") or ""
        verdict      = fmt_label(row.get("verdict", ""), "hypotheses.verdict")
        confidence   = row.get("confidence") or 0.0
        evaluated_at = (row.get("evaluated_at") or "")[:10]
        try:
            db.mark_hypothesis_outcome_delivering(memory_id, chat_id, outcome_id)
            if coord_text:
                await send_long(
                    app.bot, chat_id,
                    i18n.t("jobs.outcomes.report", memory_id=memory_id, verdict=verdict, confidence=confidence,
                           evaluated_at=evaluated_at, coord_text=coord_text),
                )
            else:
                await asyncio.to_thread(notify.fault,
                    f"scheduled.deliver_unsent_outcomes: report unavailable memory_id={memory_id} outcome_id={outcome_id}",
                    person_key=None)
            db.mark_hypothesis_outcome_sent(memory_id, outcome_id)
            log.info(f"deliver_unsent_outcomes: #{memory_id} (outcome {outcome_id}) доставлено")
        except Exception as e:
            log.error(f"deliver_unsent_outcomes: #{memory_id} ошибка: {e}")


async def deliver_unsent_outcomes_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-обёртка: тот же outbox, что и post_init, по расписанию."""
    await deliver_unsent_outcomes(context.application)


# ── Outbox задач-опросников ──────────────────────────────────────────────────

async def deliver_unsent_assessment_tasks(app) -> None:
    """Шлёт владельцу открытые задачи type='assessment' с sent_at IS NULL вместе с
    клавиатурой «Заполнить / +3д / +7д / Пропустить» (assessment_bot_handlers) и метит
    sent_at по id. До 2026-09-03 клавиатуру никто не отправлял: планировщик (launchd,
    без бота) создавал задачу, а путь начать опрос из бота отсутствовал — PRO-ряд стоял
    неделями при ежедневном «assessments просрочены». Отказ отправки sent_at не
    ставит: задача остаётся в outbox, а не теряется."""
    chat_id = get_chat_id()
    if not chat_id:
        return
    try:
        db.wake_snoozed_assessment_tasks(get_today())
        rows = db.get_unsent_assessment_tasks()
    except Exception as e:
        log.warning(f"deliver_unsent_assessment_tasks: не удалось получить список: {e}")
        return
    if not rows:
        return
    import assessment_bot_handlers as abh
    log.info(f"deliver_unsent_assessment_tasks: {len(rows)} недоставленных опросников")
    for row in rows:
        task_id = row["id"]
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=i18n.t("jobs.assessment.task", task_id=task_id, content=row.get('content') or ''),
                reply_markup=abh.build_assessment_task_keyboard(task_id),
            )
            db.mark_task_sent(task_id)
            log.info(f"deliver_unsent_assessment_tasks: #{task_id} доставлено")
        except Exception as e:
            log.error(f"deliver_unsent_assessment_tasks: #{task_id} ошибка: {e}")


async def deliver_unsent_assessment_tasks_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-обёртка outbox опросников (5 мин)."""
    await deliver_unsent_assessment_tasks(context.application)


async def deliver_pending_proposals_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Outbox предложений по списку проблем: доставка тенанту своего бота + квитанция."""
    from bot.helpers import _send_problem_proposals
    chat_id = get_chat_id()
    if not chat_id:
        return
    await _send_problem_proposals(context.application.bot, chat_id)


# ── Outbox вопросов (нить question-answer-channel, 2026-09-12) ───────────────

def _questions_budget_left() -> int:
    """Сколько вопросов ещё можно задать в эти сутки.

    Неограниченный поток вопросов может сделать канал непригодным для человека.
    Лимит изменяемый, поэтому живёт в system_config (questions.max_per_day),
    а не литералом в коде. Нет ключа — считаем, что настройка не сделана,
    и молчим (fail-closed): непрошенная лавина хуже паузы.
    """
    import config_db as cfg
    limit = cfg.get_config("questions.max_per_day", default=None)
    try:
        limit = int(float(limit))
    except (TypeError, ValueError):
        log.warning("questions.max_per_day не задан в system_config — вопросы не шлём")
        return 0
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE type='question' "
            "AND sent_at >= datetime('now', '-1 day')"
        ).fetchone()
    sent = row["n"] if row else 0
    return max(0, limit - sent)


async def deliver_unsent_question_tasks(app) -> None:
    """Шлёт владельцу открытые вопросы с sent_at IS NULL и запоминает message_id.

    Вопрос — единственная задача, у которой ответ это ТЕКСТ, а не факт делания,
    поэтому она не едет в Reminders (галочка стирает содержание: замер 2026-09-12 —
    7 вопросов, 0 ответов). ForceReply делает реплай путём по умолчанию, а
    message_id, записанный по id задачи, — это обратный адрес: по нему ответ
    находит свой вопрос (tasks_db.get_task_by_tg_message).

    Отказ отправки sent_at не ставит — вопрос остаётся в outbox, а не теряется
    (тот же образец, что у опросников и заключений)."""
    chat_id = get_chat_id()
    if not chat_id:
        return
    budget = _questions_budget_left()
    if budget <= 0:
        return
    try:
        rows = db.get_questions_needing_delivery()
    except Exception as e:
        log.warning(f"deliver_unsent_question_tasks: не удалось получить список: {e}")
        return
    if not rows:
        return

    from telegram import ForceReply
    for row in rows[:budget]:
        task_id = row["id"]
        try:
            msg = await app.bot.send_message(
                chat_id=chat_id,
                text=i18n.t("jobs.question.task", task_id=task_id, content=row.get('content') or ''),
                reply_markup=ForceReply(selective=False,
                                        input_field_placeholder=i18n.t("jobs.question.placeholder")),
            )
            db.mark_task_sent(task_id, tg_message_id=msg.message_id)
            log.info(f"deliver_unsent_question_tasks: #{task_id} доставлен "
                     f"(message_id={msg.message_id})")
        except Exception as e:
            log.error(f"deliver_unsent_question_tasks: #{task_id} ошибка: {e}")


async def deliver_unsent_question_tasks_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job-обёртка outbox вопросов (5 мин)."""
    await deliver_unsent_question_tasks(context.application)


async def promote_memory_questions_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Раз в сутки поднимает часть накопленных в памяти вопросов в задачи.

    Отдельно от доставки: рождение вопроса и его отправка — разные события с
    разной ценой ошибки. Партия маленькая (questions.promote_batch), доставка
    всё равно ограничена дросселем — лавина не соберётся ни на одном из двух шагов."""
    import task_agent as ta
    from datetime import timezone as _tz_utc
    started = _dt.now(_tz_utc.utc)
    try:
        created = await asyncio.to_thread(ta.promote_memory_questions)
        if created:
            log.info(f"promote_memory_questions_job: поднято {len(created)}")
        # Квитанция ПОСЛЕ работы, а не до (нить question-promote-liveness, 2026-09-14).
        # Смерть этого джоба была невидима: единственный датчик, который её заметил бы,
        # — «кандидат умер, не побывав у судьи», а он по построению говорит только через
        # окно свежести, то есть через 45 дней. Отметка ставится ВНУТРИ try и ПОСЛЕ
        # вызова: упавший подъём квитанции не оставляет, иначе датчик зеленел бы на
        # факте входа в функцию (§14 — heartbeat доказывает «запустился», и это
        # минимум, который он обязан доказывать честно).
        #
        # Судится ФАКТ ЗАПУСКА, а не число поднятого: разговоры с ботом
        # бывают редкими и всплесками, и «сегодня поднято ноль» — норма, а не
        # поломка. Датчик, краснеющий на норме, перестаёт читаться.
        import config_db as _cfg
        await asyncio.to_thread(_cfg.upsert_config, "_promote_questions_last_run",
                                "ok", None, _rhythm(_PROMOTE_EVERY_S, started), "heartbeat",
                                "promote_memory_questions_job")
    except Exception as e:
        log.warning(f"promote_memory_questions_job: {e}")


# ── Pending field reviews (неизвестные маркеры анализов) ─────────────────────

async def _send_field_review_message(bot, review: dict) -> None:
    """
    Отправляет Telegram-карточку для маппинга неизвестного маркера анализа.

    UX:
    - Показывает топ-3 ближайших canonical из БД в виде кнопок (даже с низким score)
    - Если кандидатов нет — только Skip + инструкция ответить текстом
    - Ручной ввод: reply-to-message → handle_text → resolve_field_review
    """
    from lab_fuzzy import top_candidates
    rid      = review["id"]
    raw_name = review["raw_name"]
    value    = review["value"]
    unit     = review.get("unit") or ""
    fname    = (review.get("source_file") or "").split("/")[-1]

    candidates = top_candidates(raw_name, n=3, min_score=0.15)

    lines = [
        i18n.t("cards.marker.heading"),
        f"`{raw_name}` = {value} {unit}".strip(),
        i18n.t("cards.marker.file", fname=fname),
        "",
        i18n.t("cards.marker.reply"),
    ]
    if candidates:
        top_names = ", ".join(f"*{c}*" for c, _ in candidates)
        lines.append(i18n.t("cards.marker.choose", names=top_names))

    # Строим клавиатуру: по одной кнопке на кандидата + Skip
    # callback_data ограничен 64 символами → обрезаем canonical до 40 символов
    candidate_buttons = [
        InlineKeyboardButton(
            f"{c} ({s:.0%})",
            callback_data=f"fieldrev_{rid}_ok_{c[:40]}"
        )
        for c, s in candidates
    ]
    skip_button = InlineKeyboardButton(i18n.t("cards.document.type.skip"), callback_data=f"fieldrev_{rid}_skip")

    keyboard_rows = []
    # Кандидаты по 2 в ряд
    for i in range(0, len(candidate_buttons), 2):
        keyboard_rows.append(candidate_buttons[i:i+2])
    keyboard_rows.append([skip_button])

    msg = await send_md(bot.send_message, chat_id=owner_chat_id(), text="\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard_rows))
    try:
        db.set_field_review_tg_message(rid, msg.message_id)
    except Exception as _e:
        log.warning(f"_send_field_review_message: tg_message_id save failed: {_e}")


async def check_pending_field_reviews(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job: проверяет pending_field_reviews и отправляет уведомления (по одному за раз)."""
    try:
        pending = db.get_pending_field_reviews()
        # Отправляем только непосланные (нет аналога mark_sent для field_reviews —
        # используем created_at как сигнал: посылаем только самый свежий чтобы не спамить)
        unsent = [r for r in pending if not r.get("suggested")]
        if not unsent:
            return
        # Берём первый, посылаем, сохраняем suggested чтобы не дублировать
        review = unsent[0]
        await _send_field_review_message(context.bot, review)
        # Помечаем что уведомление отправлено (пишем suggested=SENT)
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE pending_field_reviews SET suggested='__sent__' WHERE id=?",
                (review["id"],)
            )
        log.info(f"check_pending_field_reviews: отправлено id={review['id']} raw={review['raw_name']}")
    except Exception as e:
        log.error(f"check_pending_field_reviews: {e}")


# ── HV-5: Еженедельная оценка гипотез консилиумом ──────────────────────────

async def check_hypothesis_evaluations(context: ContextTypes.DEFAULT_TYPE):
    """
    Еженедельно (пн 10:00, см. register): сканирует гипотезы в status='testing' без вердикта.
    Запускает полный консилиум для каждой. Результат отправляет в Telegram.
    """
    chat_id = get_chat_id()
    if not chat_id:
        return

    try:
        pending = db.get_hypotheses_awaiting_evaluation()
    except Exception as e:
        log.warning(f"check_hypothesis_evaluations: get_hypotheses_awaiting_evaluation: {e}")
        return

    if not pending:
        log.info("check_hypothesis_evaluations: нет гипотез к оценке")
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text=i18n.t("jobs.hypotheses.checking", count=len(pending)),
    )

    from hypothesis_consilium_eval import evaluate_hypothesis_via_consilium
    from hypothesis_resolution import resolve_hypothesis

    for hyp in pending:
        memory_id = hyp["memory_id"]
        obs = hyp.get("observation", "")[:80]

        await context.bot.send_message(
            chat_id=chat_id,
            text=i18n.t("jobs.hypotheses.checking_one", memory_id=memory_id, observation=obs),
        )
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            verdict_dict = await evaluate_hypothesis_via_consilium(memory_id)
            resolution   = await asyncio.to_thread(resolve_hypothesis, memory_id, verdict_dict, person_key=None)

            coord_text = verdict_dict.get("coordinator_text", "")
            if coord_text:
                from bot.utils import send_long as _send_long
                # Outbox: помечаем намерение отправить — до send_long
                await asyncio.to_thread(
                    db.mark_hypothesis_outcome_delivering, memory_id, chat_id
                )
                await _send_long(
                    context.bot, chat_id,
                    i18n.t("jobs.hypotheses.report", memory_id=memory_id, coord_text=coord_text)
                )
                await asyncio.to_thread(db.mark_hypothesis_outcome_sent, memory_id)

            if resolution["message"]:
                await context.bot.send_message(chat_id=chat_id, text=resolution["message"])
        except Exception as e:
            log.error(f"check_hypothesis_evaluations #{memory_id}: {e}", exc_info=True)
            await asyncio.to_thread(notify.fault,
                f"scheduled.check_hypothesis_evaluations memory_id={memory_id}: {type(e).__name__}: {e}",
                person_key=None)
