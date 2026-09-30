"""
tests/integration/test_bot_sprint6c.py — Sprint 6c integration тесты.

Закрывает behavioral gap из ответа "хочу 6c" (2026-05-23):
- 8 jobs из jobs/scheduled.py (mock JobQueue context)
- handle_text/handle_photo/handle_location (mock ai.chat / urllib)
- cmd_report/weekly/monthly (mock gp.generate_*)
- bot.helpers.refresh_data + 4 async helpers (mock subprocess/db)
- handlers.consult полный flow (save → restore через БД)

Все тесты — unit-level с mock, без реальных API/subprocess/network.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.integration


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_bot_mock():
    """Mock telegram.Bot с async-методами."""
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.send_chat_action = AsyncMock()
    bot.send_photo = AsyncMock()
    return bot


def _make_job_context(bot_data=None):
    """ContextTypes.DEFAULT_TYPE-подобный объект для jobs."""
    ctx = SimpleNamespace(
        bot=_make_bot_mock(),
        bot_data=bot_data or {},
        error=None,
    )
    return ctx


def _attach_reply(upd, tg):
    chat_id = upd.message.chat.id

    async def reply_text(text, **kwargs):
        return await tg.send_message(chat_id=chat_id, text=text, **kwargs)

    async def send_action(action):
        return None

    upd.message.reply_text = reply_text
    upd.message.chat.send_action = send_action
    upd.message.get_bot = lambda: tg
    return upd


# ═════════════════════════════════════════════════════════════════════════════
# jobs/scheduled — все 8 jobs
# ═════════════════════════════════════════════════════════════════════════════

def test_job_send_morning_report_no_chat_id_skips():
    """send_morning_report: если chat_id не известен → silent return."""
    from jobs.scheduled import send_morning_report

    ctx = _make_job_context()
    with patch("jobs.scheduled.get_chat_id", return_value=None):
        asyncio.run(send_morning_report(ctx))

    ctx.bot.send_message.assert_not_called()


def test_job_send_morning_report_happy_path(tmp_path, monkeypatch):
    """send_morning_report: успешный flow → send_long с отчётом."""
    from jobs.scheduled import send_morning_report

    # reports_dir мокаем на tmp чтобы не писать в реальный каталог тенанта.
    # C2 (2026-07-03): reports_dir теперь из HEALTH_DATA_DIR (не HOME/iCloud) —
    # изоляция обязана переопределять именно его, иначе тест пишет в ~/health.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))

    ctx = _make_job_context()
    with patch("jobs.scheduled.get_chat_id", return_value=42), \
         patch("jobs.scheduled.refresh_data", return_value=None), \
         patch("jobs.scheduled.gp.generate_daily_report",
               return_value=("Тестовый отчёт", {"max_level": None, "urgent_message": ""})), \
         patch("jobs.scheduled.gp.run_experiment_checks", return_value=[]):
        asyncio.run(send_morning_report(ctx))

    # send_long был вызван — bot.send_message хотя бы 1 раз с отчётом
    assert ctx.bot.send_message.called
    last_call = ctx.bot.send_message.call_args
    assert "Тестовый отчёт" in last_call.kwargs.get("text", "")


def test_job_send_morning_report_urgent_safety_before_report(tmp_path, monkeypatch):
    """send_morning_report: urgent safety alert приходит ДО основного отчёта."""
    from jobs.scheduled import send_morning_report

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))

    ctx = _make_job_context()
    with patch("jobs.scheduled.get_chat_id", return_value=42), \
         patch("jobs.scheduled.refresh_data", return_value=None), \
         patch("jobs.scheduled.gp.generate_daily_report",
               return_value=("отчёт", {"max_level": "urgent",
                                          "urgent_message": "СРОЧНО: проверь анализы"})), \
         patch("jobs.scheduled.gp.run_experiment_checks", return_value=[]):
        asyncio.run(send_morning_report(ctx))

    # Первый send_message — safety alert
    first_call = ctx.bot.send_message.call_args_list[0]
    assert "СРОЧНО" in first_call.kwargs["text"]


def test_evening_checkin_autostart_is_gone():
    """Автозапуск вечернего чекина снят 2026-08-17 (решение владельца).

    Оракул ВОЗВРАТА, не отсутствия сообщений: он краснеет, если носитель
    автозапуска появится снова. Граница названа вслух — тест не доказывает,
    что боту нечем разбудить тенанта вечером; он доказывает, что ровно этот
    носитель мёртв, и заставляет принять решение заново, а не вернуть тихо.
    """
    import jobs.scheduled as sched

    assert not hasattr(sched, "send_evening_checkin"), (
        "send_evening_checkin вернулся в jobs/scheduled.py — "
        "автозапуск чекина снят решением владельца 2026-08-17, "
        "возврат требует нового решения, а не правки теста"
    )
    src = Path(sched.__file__).read_text(encoding="utf-8")
    assert 'name="evening_checkin"' not in src, (
        "job 'evening_checkin' снова регистрируется в register()"
    )


def test_job_run_specialists_scheduled_calls_gp():
    from jobs.scheduled import run_specialists_scheduled

    ctx = _make_job_context()
    with patch("jobs.scheduled.get_chat_id", return_value=42), \
         patch("jobs.scheduled.gp.run_specialists_and_save", return_value=None) as mock_run, \
         patch("jobs.scheduled.db.get_overdue_tasks", return_value=[]):
        asyncio.run(run_specialists_scheduled(ctx))

    mock_run.assert_called_once()


def test_job_run_specialists_with_followup_sends_message():
    from jobs.scheduled import run_specialists_scheduled

    ctx = _make_job_context()
    with patch("jobs.scheduled.get_chat_id", return_value=42), \
         patch("jobs.scheduled.gp.run_specialists_and_save", return_value=None), \
         patch("jobs.scheduled.db.get_overdue_tasks",
               return_value=[{"id": 5, "type": "action", "content": "Invented task",
                              "created_at": "2000-01-01"}]), \
         patch("jobs.scheduled.ta.format_open_tasks_message", return_value="Invented task"):
        asyncio.run(run_specialists_scheduled(ctx))

    ctx.bot.send_message.assert_called_once()
    assert "Invented task" in ctx.bot.send_message.call_args.kwargs["text"]
    assert ctx.bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "act:td:5"


def test_job_send_weekly_report_happy_path():
    from jobs.scheduled import send_weekly_report

    ctx = _make_job_context()
    with patch("jobs.scheduled.get_chat_id", return_value=42), \
         patch("jobs.scheduled.refresh_data", return_value=None), \
         patch("jobs.scheduled.gp.generate_weekly_report",
               return_value="Недельный отчёт текст"), \
         patch("jobs.scheduled._send_tasks_from_report", new_callable=AsyncMock), \
         patch("jobs.scheduled.hai.get_active_protocols", return_value=[]):
        asyncio.run(send_weekly_report(ctx))

    assert ctx.bot.send_message.called
    assert "Недельный отчёт" in ctx.bot.send_message.call_args_list[0].kwargs["text"]


def test_job_send_monthly_check_happy_path():
    from jobs.scheduled import send_monthly_check

    ctx = _make_job_context()
    with patch("jobs.scheduled.get_chat_id", return_value=42), \
         patch("jobs.scheduled.refresh_data", return_value=None), \
         patch("jobs.scheduled.gp.generate_monthly_report",
               return_value="Ежемесячный отчёт"):
        asyncio.run(send_monthly_check(ctx))

    assert ctx.bot.send_message.called
    assert "Ежемесячный отчёт" in ctx.bot.send_message.call_args_list[0].kwargs["text"]


def test_job_check_recommendations_outside_hours_skips():
    """check_recommendations_scheduled: вне 9-21 → skip."""
    from jobs.scheduled import check_recommendations_scheduled

    ctx = _make_job_context()
    fake_now = SimpleNamespace(hour=23, date=lambda: date.today())
    with patch("jobs.scheduled.get_chat_id", return_value=42), \
         patch("jobs.scheduled._dt") as dt_mock:
        dt_mock.now.return_value = fake_now
        asyncio.run(check_recommendations_scheduled(ctx))

    ctx.bot.send_message.assert_not_called()


def test_job_check_recommendations_sends_when_domain_needed():
    """check_recommendations_scheduled: domain needed → send_message."""
    from jobs.scheduled import check_recommendations_scheduled

    ctx = _make_job_context(bot_data={})
    fake_now = SimpleNamespace(hour=14, date=lambda: date(2026, 5, 23))
    with patch("jobs.scheduled.get_chat_id", return_value=42), \
         patch("jobs.scheduled._dt") as dt_mock, \
         patch("jobs.scheduled.db.get_active_protocols",
               return_value=[{"id": 1, "title": "Дыхание", "domain": "vagal_activation"}]), \
         patch("jobs.scheduled.evaluate_domain_need",
               return_value={"needed": True, "urgency": "required",
                             "reasons": ["ВСР низкая"],
                             "protocols": [{"title": "Дыхание"}]}):
        dt_mock.now.return_value = fake_now
        asyncio.run(check_recommendations_scheduled(ctx))

    ctx.bot.send_message.assert_called_once()
    text = ctx.bot.send_message.call_args.kwargs["text"]
    assert "ВСР низкая" in text
    assert "Дыхание" in text


def test_job_check_recommendations_respects_daily_cap():
    """check_recommendations_scheduled: если уже отправлено max — skip."""
    from jobs.scheduled import check_recommendations_scheduled, _RECO_SENT_KEY

    today_str = str(date(2026, 5, 23))
    ctx = _make_job_context(bot_data={_RECO_SENT_KEY: {f"sleep_{today_str}": 1}})
    fake_now = SimpleNamespace(hour=14, date=lambda: date(2026, 5, 23))
    with patch("jobs.scheduled.get_chat_id", return_value=42), \
         patch("jobs.scheduled._dt") as dt_mock, \
         patch("jobs.scheduled.db.get_active_protocols",
               return_value=[{"id": 1, "title": "Сон", "domain": "sleep"}]), \
         patch("jobs.scheduled.evaluate_domain_need",
               return_value={"needed": True, "urgency": "recommended",
                             "reasons": ["плохой сон"], "protocols": [{"title": "Сон"}]}):
        dt_mock.now.return_value = fake_now
        asyncio.run(check_recommendations_scheduled(ctx))

    # recommended max=1, sent_today=1 → не отправляем
    ctx.bot.send_message.assert_not_called()


def test_job_check_pending_doc_reviews_no_pending():
    """check_pending_doc_reviews без pending → нет send_message."""
    from jobs.scheduled import check_pending_doc_reviews

    ctx = _make_job_context()
    with patch("jobs.scheduled.db.auto_confirm_stale_reviews", return_value=0), \
         patch("jobs.scheduled.db.get_pending_doc_reviews", return_value=[]):
        asyncio.run(check_pending_doc_reviews(ctx))

    ctx.bot.send_message.assert_not_called()


def test_job_check_pending_doc_reviews_sends_each():
    """check_pending_doc_reviews: каждый pending → 1 send_message + mark_sent."""
    from jobs.scheduled import check_pending_doc_reviews

    reviews = [
        {"id": 1, "source_file": "/path/file1.pdf", "proposed_type": "oncology"},
        {"id": 2, "source_file": "/path/file2.pdf", "proposed_type": "consultation"},
    ]
    ctx = _make_job_context()
    with patch("jobs.scheduled.db.auto_confirm_stale_reviews", return_value=0), \
         patch("jobs.scheduled.db.get_pending_doc_reviews", return_value=reviews), \
         patch("jobs.scheduled.db.mark_doc_review_sent") as mock_mark:
        asyncio.run(check_pending_doc_reviews(ctx))

    assert ctx.bot.send_message.call_count == 2
    assert mock_mark.call_count == 2


# ═════════════════════════════════════════════════════════════════════════════
# handlers.reports — cmd_report, cmd_weekly, cmd_monthly
# ═════════════════════════════════════════════════════════════════════════════

def test_cmd_report_happy_path(tg):
    """cmd_report: gp возвращает отчёт → send_long."""
    from handlers.reports import cmd_report
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/report", chat_id=owner_chat_id()), tg)
    ctx = SimpleNamespace(args=[])

    with patch("bot.helpers.refresh_data", return_value=None), \
         patch("handlers.reports.gp.generate_daily_report",
               return_value=("Отчёт OK", {"max_level": None, "urgent_message": ""})):
        asyncio.run(cmd_report(upd, ctx))

    assert any("Отчёт OK" in m["text"] for m in tg.outgoing)


def test_cmd_report_urgent_safety_first(tg):
    """cmd_report: urgent safety alert → отправляется перед отчётом."""
    from handlers.reports import cmd_report
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/report", chat_id=owner_chat_id()), tg)
    ctx = SimpleNamespace(args=[])

    with patch("bot.helpers.refresh_data", return_value=None), \
         patch("handlers.reports.gp.generate_daily_report",
               return_value=("отчёт", {"max_level": "critical",
                                          "urgent_message": "АЛЕРТ!"})):
        asyncio.run(cmd_report(upd, ctx))

    # Первый outgoing — критический алерт
    assert "АЛЕРТ!" in tg.outgoing[0]["text"]


def test_cmd_weekly_calls_gp_with_force_mdt(tg):
    """cmd_weekly: вызывает gp.generate_weekly_report с force_mdt=True."""
    from handlers.reports import cmd_weekly
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/weekly", chat_id=owner_chat_id()), tg)
    ctx = SimpleNamespace(args=[])

    with patch("bot.helpers.refresh_data", return_value=None), \
         patch("handlers.reports.gp.generate_weekly_report",
               return_value="Weekly отчёт") as mock_gen, \
         patch("bot.helpers._send_tasks_from_report", new_callable=AsyncMock), \
         patch("bot.helpers._send_problem_proposals", new_callable=AsyncMock):
        asyncio.run(cmd_weekly(upd, ctx))

    assert mock_gen.called
    # Второй позиционный аргумент = force_mdt
    assert mock_gen.call_args.args[1] is True
    assert any("Weekly" in m["text"] for m in tg.outgoing)


def test_cmd_monthly_calls_gp_monthly(tg):
    from handlers.reports import cmd_monthly
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/monthly", chat_id=owner_chat_id()), tg)
    ctx = SimpleNamespace(args=[])

    with patch("bot.helpers.refresh_data", return_value=None), \
         patch("handlers.reports.gp.generate_monthly_report",
               return_value="Monthly отчёт"), \
         patch("bot.helpers._send_problem_proposals", new_callable=AsyncMock):
        asyncio.run(cmd_monthly(upd, ctx))

    assert any("Monthly" in m["text"] for m in tg.outgoing)


# ═════════════════════════════════════════════════════════════════════════════
# handlers.messages — handle_text, handle_photo, handle_location
# ═════════════════════════════════════════════════════════════════════════════

def test_handle_text_normal_chat(tg):
    """handle_text вне checkin/assessment → ai.chat() + send_long."""
    from handlers.messages import handle_text
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="как дела?", chat_id=owner_chat_id()), tg)
    ctx = SimpleNamespace()

    with patch("handlers.messages.ck.checkin_state") as state_mock, \
         patch("handlers.messages.abh.get_active_assessment", return_value=None), \
         patch("handlers.messages.ai.chat", return_value="всё ок"), \
         patch("bot.helpers._run_arbiter_background", new_callable=AsyncMock):
        state_mock.active = False
        asyncio.run(handle_text(upd, ctx))

    # ai.chat() результат попал в outgoing
    assert any("всё ок" in m.get("text", "") for m in tg.outgoing)


def test_orphan_skip_button_drops_stuck_keyboard_and_keeps_route(tg):
    """«Пропустить» без активного опроса — кнопка зависшей reply-клавиатуры геопозиции (у владельца
    висела с 23.09). Клавиатура снимается, а сообщение идёт своим обычным маршрутом (25.09)."""
    from handlers.messages import handle_text
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="Пропустить", chat_id=owner_chat_id()), tg)
    with patch("handlers.messages.ck.checkin_state") as state_mock, \
         patch("handlers.messages.abh.get_active_assessment", return_value=None), \
         patch("handlers.messages.abh._drop_reply_keyboard", new_callable=AsyncMock) as drop, \
         patch("handlers.messages.ai.chat", return_value="ок"), \
         patch("bot.helpers._run_arbiter_background", new_callable=AsyncMock):
        state_mock.active = False
        asyncio.run(handle_text(upd, SimpleNamespace()))
    drop.assert_awaited_once()
    assert any("ок" in m.get("text", "") for m in tg.outgoing), "маршрут сообщения сломан"


def test_handle_text_in_checkin_continues_conversation(tg):
    """handle_text при активном checkin → ck.continue_checkin."""
    from handlers.messages import handle_text
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="спал 6 часов", chat_id=owner_chat_id()), tg)
    ctx = SimpleNamespace()

    with patch("handlers.messages.ck.checkin_state") as state_mock, \
         patch("handlers.messages.ck.continue_checkin",
               return_value=("Спасибо. Что ещё?", False)), \
         patch("bot.helpers._finalize_checkin_background", new_callable=AsyncMock):
        state_mock.active = True
        # active ⇒ started_at (CheckinState.start ставит оба): кнопка «Хватит на сегодня»
        # привязана к started_at (партия 7)
        state_mock.started_at = datetime(2030, 1, 15, 20, 0)
        state_mock.should_force_end.return_value = False
        state_mock.conversation = []
        asyncio.run(handle_text(upd, ctx))

    assert any("Спасибо" in m.get("text", "") for m in tg.outgoing)


def test_handle_text_in_checkin_force_end(tg):
    """handle_text при checkin should_force_end → finalize."""
    from handlers.messages import handle_text
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="дай уже спать", chat_id=owner_chat_id()), tg)
    ctx = SimpleNamespace()

    with patch("handlers.messages.ck.checkin_state") as state_mock, \
         patch("bot.helpers._finalize_checkin_background", new_callable=AsyncMock) as mock_fin:
        state_mock.active = True
        state_mock.should_force_end.return_value = True
        state_mock.conversation = []
        asyncio.run(handle_text(upd, ctx))

    assert any("Хорошего вечера" in m.get("text", "") for m in tg.outgoing)
    # _finalize_checkin_background создаётся как task (create_task) — мог не успеть выполниться


def test_handle_photo_calls_ai_chat_with_image(tg):
    from handlers.messages import handle_photo
    from bot.filters import owner_chat_id

    photo_obj = SimpleNamespace(file_id="abc")
    upd = SimpleNamespace(
        message=SimpleNamespace(
            caption="Что это?",
            photo=[photo_obj],
            chat=SimpleNamespace(
                id=owner_chat_id(),
                send_action=AsyncMock(),
            ),
            reply_text=AsyncMock(),
        ),
        effective_chat=SimpleNamespace(id=owner_chat_id()),
    )

    bot_mock = MagicMock()
    bot_mock.get_file = AsyncMock(return_value=SimpleNamespace(
        download_as_bytearray=AsyncMock(return_value=b"fake-bytes")
    ))
    ctx = SimpleNamespace(bot=bot_mock)

    with patch("handlers.messages.ai.chat_with_image", return_value="это таблетка") as mock_ai:
        asyncio.run(handle_photo(upd, ctx))

    mock_ai.assert_called_once()
    # reply_text был вызван с ответом
    upd.message.reply_text.assert_awaited_with("это таблетка")


def test_handle_location_saves_to_profile(tg, tmp_path, monkeypatch):
    """handle_location: гео → save_memory + reply."""
    from handlers.messages import handle_location
    from bot.filters import owner_chat_id

    monkeypatch.setenv("HOME", str(tmp_path))
    # profile_context.json под iCloud-путь
    icloud = tmp_path / "Library/Mobile Documents/com~apple~CloudDocs/health/data"
    icloud.mkdir(parents=True, exist_ok=True)

    upd = SimpleNamespace(
        message=SimpleNamespace(
            location=SimpleNamespace(latitude=64.1466, longitude=-21.9426),
            chat=SimpleNamespace(
                id=owner_chat_id(),
                send_action=AsyncMock(),
            ),
            reply_text=AsyncMock(),
        ),
        effective_chat=SimpleNamespace(id=owner_chat_id()),
    )
    ctx = SimpleNamespace()

    geo_response = MagicMock()
    geo_response.read.return_value = b'{"address": {"city": "Reykjavik", "country": "Iceland", "country_code": "is"}}'
    geo_response.__enter__ = lambda self: self
    geo_response.__exit__ = lambda *args: None

    with patch("handlers.messages.db.get_profile_context", return_value={}), \
         patch("handlers.messages.db.save_memory") as mock_save_mem, \
         patch("urllib.request.urlopen", return_value=geo_response):
        asyncio.run(handle_location(upd, ctx))

    mock_save_mem.assert_called_once()
    saved_kwargs = mock_save_mem.call_args.kwargs
    assert "Reykjavik" in saved_kwargs["value"]
    upd.message.reply_text.assert_awaited()
    _aw = upd.message.reply_text.await_args  # через send_md текст идёт text=
    reply_text = _aw.kwargs.get("text") or _aw.args[0]
    assert "Reykjavik" in reply_text


# ═════════════════════════════════════════════════════════════════════════════
# bot.helpers — refresh_data + 4 async helpers
# ═════════════════════════════════════════════════════════════════════════════

def test_refresh_data_calls_subprocess_for_oura_only():
    """С 26.09 Apple Health приходит сам через REST /hae/ingest; iCloud-путь
    «import_apple_health.py daily» снят (громкий отказ) — refresh_data его не зовёт."""
    from bot.helpers import refresh_data

    with patch("bot.helpers.subprocess.run") as mock_run, \
         patch("bot.helpers.db.init_db"), \
         patch("bot.helpers.db.migrate_all_json"), \
         patch("bot.helpers.db.import_all_biochemical"):
        refresh_data()

    call_args_strs = [str(c.args[0]) for c in mock_run.call_args_list]
    assert mock_run.call_count == 1
    assert any("import_oura.py" in s for s in call_args_strs)
    assert not any("import_apple_health.py" in s for s in call_args_strs)


def test_refresh_data_subprocess_timeout_does_not_raise():
    """refresh_data: subprocess timeout → log warning, не raise."""
    from bot.helpers import refresh_data
    import subprocess as _sp

    def raise_timeout(*args, **kwargs):
        raise _sp.TimeoutExpired(cmd="x", timeout=30)

    with patch("bot.helpers.subprocess.run", side_effect=raise_timeout), \
         patch("bot.helpers.db.init_db"), \
         patch("bot.helpers.db.migrate_all_json"), \
         patch("bot.helpers.db.import_all_biochemical"):
        # Не должно бросить
        refresh_data()


def test_send_problem_proposals_no_proposals_returns_silently():
    from bot.helpers import _send_problem_proposals

    bot = _make_bot_mock()
    with patch("bot.helpers.db.get_undelivered_proposals", return_value=[]):
        asyncio.run(_send_problem_proposals(bot, chat_id=42))

    bot.send_message.assert_not_called()


def test_send_problem_proposals_with_data_formats_correctly():
    from bot.helpers import _send_problem_proposals

    bot = _make_bot_mock()
    proposal = {
        "id": 5,
        "source": "gp_weekly",
        "created_at": "2026-05-20T10:00:00",
        "proposed": '[{"action": "update_status", "problem_id": "P-001", "old_value": "active", "new_value": "resolved", "reason": "стабилизировано"}]'
    }
    # 2026-09-23: доставляются НЕДОСТАВЛЕННЫЕ, с квитанцией (нить proposal-delivery)
    with patch("bot.helpers.db.get_undelivered_proposals", return_value=[proposal]), \
         patch("bot.helpers.db.mark_proposal_delivered") as mark:
        asyncio.run(_send_problem_proposals(bot, chat_id=42))
    mark.assert_called_once()

    bot.send_message.assert_called_once()
    text = bot.send_message.call_args.kwargs["text"]
    # Партия 7: проблемы нет в (пустой) базе — человеку «ничего не изменит» и причина,
    # без внутренних кодов (P-001, active → resolved), которые он прочесть не может.
    assert "ничего не изменит" in text and "стабилизировано" in text
    assert "P-001" not in text and "active" not in text and "resolved" not in text


def test_send_tasks_from_report_no_tasks_no_message():
    from bot.helpers import _send_tasks_from_report

    bot = _make_bot_mock()
    with patch("bot.helpers.ta.process_gp_report", return_value=([], None)):
        asyncio.run(_send_tasks_from_report(bot, 42, "отчёт", "gp_daily", date.today()))

    bot.send_message.assert_not_called()


def test_send_tasks_from_report_marks_sent():
    from bot.helpers import _send_tasks_from_report

    bot = _make_bot_mock()
    tasks = [{"id": 1, "title": "t1"}, {"id": 2, "title": "t2"}]
    with patch("bot.helpers.ta.process_gp_report",
               return_value=(tasks, "msg")), \
         patch("bot.helpers.db.mark_task_sent") as mock_mark:
        asyncio.run(_send_tasks_from_report(bot, 42, "отчёт", "gp_daily", date.today()))

    bot.send_message.assert_called_once()
    assert mock_mark.call_count == 2


# ═════════════════════════════════════════════════════════════════════════════
# handlers.consult — полный flow (save → restore через БД)
# ═════════════════════════════════════════════════════════════════════════════

def test_consult_continue_restores_from_db_when_no_user_data(tg):
    """cmd_consult_continue: user_data пустой, но в БД есть session → восстановить."""
    from handlers.consult import cmd_consult_continue, CONSULTING
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="продолжи", chat_id=owner_chat_id()), tg)
    ctx = SimpleNamespace(user_data={})

    fake_session_dict = {"rounds": [{"q": "a"}]}
    fake_session = SimpleNamespace(
        rounds=[{"q": "a"}],
        to_dict=lambda: fake_session_dict,
    )

    with patch("handlers.consult.db.load_consultation_session",
               return_value=fake_session_dict), \
         patch("wellally_consult.ConsultationSession.from_dict",
               return_value=fake_session), \
         patch("handlers.consult.refresh_data", return_value=None), \
         patch("wellally_consult.run_consultation_cycle_async",
               new_callable=AsyncMock,
               return_value=("новый раунд", fake_session)), \
         patch("handlers.consult.db.save_consultation_session"):
        result = asyncio.run(cmd_consult_continue(upd, ctx))

    assert result == CONSULTING
    # Session был восстановлен и сохранён в user_data
    assert ctx.user_data.get("consult_session") is fake_session


def test_consult_end_clears_user_data_and_db():
    """cmd_consult_end: чистит user_data + БД."""
    from handlers.consult import cmd_consult_end
    from bot.filters import owner_chat_id

    fake_session = SimpleNamespace(rounds=[1, 2, 3])
    ctx = SimpleNamespace(user_data={"consult_session": fake_session})
    upd = SimpleNamespace(
        message=SimpleNamespace(reply_text=AsyncMock(), chat_id=owner_chat_id()),
        effective_chat=SimpleNamespace(id=owner_chat_id()),
    )

    with patch("handlers.consult.db.delete_consultation_session") as mock_delete:
        asyncio.run(cmd_consult_end(upd, ctx))

    mock_delete.assert_called_once_with(owner_chat_id())
    assert "consult_session" not in ctx.user_data
    upd.message.reply_text.assert_awaited()
    assert "Раундов: 3" in upd.message.reply_text.await_args.args[0]


def test_consult_timeout_cleans_state():
    """cmd_consult_timeout: чистит user_data + БД + send_message."""
    from handlers.consult import cmd_consult_timeout

    fake_session = SimpleNamespace(rounds=[1])
    ctx = SimpleNamespace(user_data={"consult_session": fake_session}, bot=_make_bot_mock())
    upd = SimpleNamespace()

    with patch("handlers.consult.db.delete_consultation_session"):
        asyncio.run(cmd_consult_timeout(upd, ctx))

    assert "consult_session" not in ctx.user_data
    ctx.bot.send_message.assert_called_once()
    assert "30 минут" in ctx.bot.send_message.call_args.kwargs["text"]


# ═════════════════════════════════════════════════════════════════════════════
# Sprint 6 — invariant: bot/main.py загружается и регистрирует все модули
# ═════════════════════════════════════════════════════════════════════════════

def test_bot_main_register_loop_uses_all_handler_modules():
    """bot/main.py содержит вызовы register() для всех handlers/* и jobs/*."""
    from pathlib import Path
    src = (Path(__file__).parents[2] / "bot" / "main.py").read_text(encoding="utf-8")

    for mod_call in [
        "handlers.meta", "handlers.reports", "handlers.consult",
        "handlers.tasks", "handlers.problems", "handlers.genome",
        "handlers.hypotheses", "handlers.messages", "handlers.callbacks",
        "jobs.scheduled",
    ]:
        assert mod_call in src, f"bot/main.py не импортирует {mod_call}"
    assert "register(app, owner)" in src
    assert "register(app)" in src  # jobs.scheduled и handlers.callbacks
