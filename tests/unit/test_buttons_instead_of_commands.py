"""Batch 5 oracles: delivered keyboards reach real domain operations on fixture data.

Telegram and macOS are replaced; database assertions use only the existing test fixture.
The consultation check exercises ConversationHandler state transitions, not just callbacks.
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot import actions

pytestmark = pytest.mark.unit


def _message(bot=None, **kw):
    bot = bot or SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=100)))
    values = dict(chat_id=7, chat=SimpleNamespace(id=7, send_action=AsyncMock()),
                  message_id=50, text="Invented message", reply_to_message=None,
                  reply_markup=None, reply_text=AsyncMock(), get_bot=lambda: bot)
    values.update(kw)
    return SimpleNamespace(**values)


def _press(monkeypatch, data, message, context=None, chat_id=7):
    import bot.filters
    monkeypatch.setattr(bot.filters, "owner_chat_id", lambda: 7)
    query = SimpleNamespace(data=data, message=message, answer=AsyncMock(),
                            edit_message_reply_markup=AsyncMock())
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), callback_query=query)
    context = context or SimpleNamespace(bot=message.get_bot(), user_data={})
    asyncio.run(actions.handle_callback(update, context))
    return query


@pytest.mark.parametrize("verb,status", [("pa", "approved"), ("pr", "rejected")])
def test_delivered_proposal_buttons_apply_or_reject(db, monkeypatch, verb, status):
    import problems_db
    from bot.helpers import _send_problem_proposals

    db.add_problem("EXAMPLE", "Invented example", notes="before")
    pid = problems_db.save_problem_proposal("test", [{
        "action": "update_field", "problem_id": "EXAMPLE", "field": "notes", "new_value": "after"}])
    message = _message()
    bot = message.get_bot()
    asyncio.run(_send_problem_proposals(bot, 7))
    sent = bot.send_message.await_args.kwargs
    message.reply_markup = sent["reply_markup"]
    assert [b.callback_data for b in message.reply_markup.inline_keyboard[0]] == [
        f"act:pa:{pid}", f"act:pr:{pid}"]
    assert "/approve" not in sent["text"] and "/reject" not in sent["text"]

    # The very same delivered button must not act for another chat.
    _press(monkeypatch, f"act:{verb}:{pid}", message, chat_id=8)
    assert db.execute("SELECT status FROM problem_list_proposals WHERE id=?", (pid,)).fetchone()[0] == "pending"
    query = _press(monkeypatch, f"act:{verb}:{pid}", message)
    assert db.execute("SELECT status FROM problem_list_proposals WHERE id=?", (pid,)).fetchone()[0] == status
    assert db.execute("SELECT notes FROM problem_list WHERE problem_id='EXAMPLE'").fetchone()[0] == (
        "after" if verb == "pa" else "before")
    query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)


def test_open_tasks_buttons_complete_task_and_reminder(db, monkeypatch):
    import tasks_db
    from handlers import tasks

    tid = tasks_db.save_task("test", "action", "Invented action", priority="high")
    qid = tasks_db.save_task("test", "question", "Invented question?", priority="low")
    reminders = []
    monkeypatch.setattr(tasks.ta, "complete_macos_reminder", reminders.append)
    message = _message()
    asyncio.run(tasks.cmd_tasks(SimpleNamespace(message=message), None))
    sent = message.get_bot().send_message.await_args.kwargs
    message.reply_markup = sent["reply_markup"]
    rows = message.reply_markup.inline_keyboard
    assert [[b.callback_data for b in row] for row in rows] == [
        [f"act:td:{tid}", f"act:tx:{tid}"], [f"act:ta:{qid}"]]
    assert rows[0][0].text.startswith("1 ") and rows[1][0].text.startswith("2 ")
    assert "/done" not in sent["text"] and "/dismiss" not in sent["text"]
    query = _press(monkeypatch, rows[0][0].callback_data, message)
    assert db.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0] == "completed"
    assert reminders == [tid]
    remaining = query.edit_message_reply_markup.await_args.kwargs["reply_markup"]
    assert remaining.inline_keyboard == (rows[1],), "Other tasks must stay actionable"


@pytest.mark.parametrize("typed", [False, True])
def test_question_ask_reply_records_answer(db, monkeypatch, typed):
    from telegram import ForceReply
    import tasks_db
    from handlers import tasks

    tid = tasks_db.save_task("test", "question", "Invented question?")
    message = _message()
    context = SimpleNamespace(bot=message.get_bot(), args=[str(tid)], user_data={})
    if typed:
        asyncio.run(tasks.cmd_done(SimpleNamespace(message=message), context))
    else:
        _press(monkeypatch, tasks.task_keyboard([{"id": tid, "type": "question"}])
               .inline_keyboard[0][0].callback_data, message, context)
    assert isinstance(context.bot.send_message.await_args.kwargs["reply_markup"], ForceReply)
    answer = "Invented answer with enough detail to distinguish it from the prompt."
    reply = _message(text=answer, reply_to_message=SimpleNamespace(message_id=100))
    assert asyncio.run(actions.handle_reply(reply, context)) is True
    row = db.execute("SELECT status, resolved_text FROM tasks WHERE id=?", (tid,)).fetchone()
    assert tuple(row) == ("completed", answer)
    assert asyncio.run(actions.handle_reply(reply, context)) is False, "Receipt must be consumed"


def test_confirmation_replies_to_person_and_saves_full_text(db, monkeypatch):
    import tasks_db
    import link_fetch
    from handlers import messages, tasks

    import health_db
    health_db.init_db()   # миграция tasks.tg_message_id — как в проде; в схеме фикстуры её нет
    tid = tasks_db.save_task("test", "question", "Invented question?")
    tasks_db.mark_task_sent(tid, 31)
    monkeypatch.setattr(link_fetch, "parse", lambda text: None)
    monkeypatch.setattr(messages.ck, "checkin_state", SimpleNamespace(active=False))
    monkeypatch.setattr(messages.abh, "get_active_assessment", lambda chat: {"id": 1})
    monkeypatch.setattr(messages.abh, "handle_text_in_assessment", AsyncMock())
    full_text = "Invented answer. " * 40 + "The final sentence must survive."
    original = _message(text=full_text)
    context = SimpleNamespace(bot=original.get_bot(), user_data={})
    asyncio.run(messages.handle_text(SimpleNamespace(message=original, effective_chat=original.chat), context))
    hint = original.reply_text.await_args.kwargs
    assert hint["reply_to_message_id"] == original.message_id
    confirmation = _message(reply_to_message=original, reply_markup=hint["reply_markup"])
    yes, no = hint["reply_markup"].inline_keyboard[0]
    _press(monkeypatch, no.callback_data, confirmation, context)
    assert db.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0] == "open"
    _press(monkeypatch, yes.callback_data, confirmation, context)
    assert db.execute("SELECT resolved_text FROM tasks WHERE id=?", (tid,)).fetchone()[0] == full_text


def test_task_row_limit_and_summary_order(db):
    import task_agent
    from handlers.tasks import task_keyboard

    tasks = [{"id": n, "type": "action", "content": f"Invented task {n}",
              "priority": "high" if n == 25 else "low"} for n in range(26)]
    shown = task_agent.summary_tasks(tasks)
    keyboard = task_keyboard(shown, open_list=True)
    text = task_agent._format_tasks_summary(tasks, 0)
    assert len(task_keyboard(tasks).inline_keyboard) == 20
    assert len(keyboard.inline_keyboard) <= 20
    assert shown[0]["id"] == 25 and "1. 📋 [25]" in text
    assert keyboard.inline_keyboard[0][0].callback_data == "act:td:25"
    assert keyboard.inline_keyboard[-1][0].callback_data == "act:tasks:"


def test_consult_buttons_change_conversation_state_and_resume_receipt(db, monkeypatch):
    from telegram import CallbackQuery, Chat, Message, Update, User
    from telegram.ext import filters
    import bot.filters
    from handlers import consult

    monkeypatch.setattr(bot.filters, "owner_chat_id", lambda: 7)
    removed = []
    monkeypatch.setattr(consult.db, "delete_consultation_session", removed.append)
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=100)),
                          answer_callback_query=AsyncMock(), edit_message_reply_markup=AsyncMock())
    context = SimpleNamespace(bot=bot, user_data={"consult_session": SimpleNamespace(rounds=[1, 2])})
    handlers = []
    app = SimpleNamespace(add_handler=handlers.append, job_queue=None, bot=bot)
    consult.register(app, filters.Chat(7))
    conv = handlers[0]
    user, chat = User(7, "Fictional", False), Chat(7, "private")

    def message(mid, text, reply_to=None):
        msg = Message(mid, datetime(2000, 1, 1, tzinfo=timezone.utc), chat,
                      from_user=user, text=text, reply_to_message=reply_to)
        msg.set_bot(bot)
        return msg

    def callback(verb):
        query = CallbackQuery("example", user, "example", message=message(50, "Invented report"),
                              data=f"act:{verb}:")
        query.set_bot(bot)
        return Update(1, callback_query=query)

    async def dispatch(handler, update):
        check = handler.check_update(update)
        assert check is not None and check is not False, "ConversationHandler did not accept the update"
        await handler.handle_update(update, app, check, context)

    async def scenario():
        conv._conversations[(7, 7)] = consult.CONSULTING
        await dispatch(conv, callback("cs_end"))
        assert (7, 7) not in conv._conversations
        assert "consult_session" not in context.user_data and removed == [7]
        assert conv.check_update(Update(2, message=message(51, "Ordinary chat"))) is None
        await dispatch(conv, callback("cs_new"))
        assert conv._conversations[(7, 7)] == consult.ASKING
        question = "Invented consultation question"
        started = []

        async def start(msg, ctx, text):
            started.append(text)
            ctx.user_data["consult_session"] = SimpleNamespace(rounds=[])
            return consult.CONSULTING

        monkeypatch.setattr(consult, "_start_consult", start)
        # A fresh ConversationHandler has no in-memory ASKING state: the durable receipt routes it.
        consult.register(app, filters.Chat(7))
        resumed = handlers[-1]
        await dispatch(resumed, Update(3, message=message(52, question, message(100, "Prompt"))))
        assert started == [question]
        assert resumed._conversations[(7, 7)] == consult.CONSULTING

    asyncio.run(scenario())


def test_caption_action_keeps_caption_and_long_message_keeps_keyboard(db, monkeypatch):
    from telegram.error import BadRequest
    from bot.utils import send_long
    from handlers import hypotheses

    retired = []
    monkeypatch.setattr(hypotheses.hai, "retire_protocol", lambda tid, note: retired.append((tid, note)) or True)
    message = _message(text=None, caption="Invented document caption")
    _press(monkeypatch, "act:pret:15", message)
    assert retired == [(15, None)] and message.caption == "Invented document caption"
    bot = message.get_bot()
    bot.send_message.side_effect = [SimpleNamespace(message_id=1),
                                    BadRequest("Can't parse entities"), SimpleNamespace(message_id=2)]
    keyboard = hypotheses.protocol_keyboard([{"id": 15}])
    asyncio.run(send_long(bot, 7, "x" * 3000 + "\n" + "y" * 3000, reply_markup=keyboard))
    calls = bot.send_message.await_args_list
    assert "reply_markup" not in calls[0].kwargs
    assert calls[1].kwargs["reply_markup"] == calls[2].kwargs["reply_markup"] == keyboard


def test_genome_prompt_answer_reuses_trait_handler(db, monkeypatch):
    from handlers import genome

    questions = []
    monkeypatch.setattr(genome.gc, "answer_trait_question", lambda text: questions.append(text) or "Invented response")
    message = _message()
    context = SimpleNamespace(bot=message.get_bot(), user_data={})
    _press(monkeypatch, "act:gq:", message, context)
    reply = _message(text="Invented genome question", reply_to_message=SimpleNamespace(message_id=100))
    assert asyncio.run(actions.handle_reply(reply, context))
    assert questions == [reply.text]
    assert "Invented response" in reply.reply_text.await_args.kwargs["text"]


@pytest.mark.parametrize("n", [0, 1, 4, 7])
def test_sleep_heading_counts_displayed_data_and_keeps_unknown_distinct_from_zero(db, monkeypatch, n):
    from datetime import date, timedelta
    from handlers import meta
    import i18n
    monkeypatch.setattr(i18n, "lang_of", lambda profile=None: "ru")
    today = date(2030, 1, 20)
    rows = {str(today - timedelta(days=i)): {"sleep": {"totalSleep": 7.0, "deep": None, "sleep_score": 0}}
            for i in range(1, n + 1)}
    monkeypatch.setattr(meta, "get_today", lambda: today)
    monkeypatch.setattr(meta.db, "init_db", lambda: None)
    monkeypatch.setattr(meta.db, "get_day", lambda d: rows.get(d, {}))
    monkeypatch.setattr(meta.db, "get_stats", lambda days: {"avg_sleep": 7.0, "avg_deep": None, "avg_sleep_score": 0})
    message = _message()
    asyncio.run(meta.cmd_sleep(SimpleNamespace(message=message), None))
    call = message.reply_text.await_args
    text = call.kwargs.get("text") or call.args[0]
    if n:
        assert f"{n} " in text.splitlines()[0]
        assert sum("оценка сна — 0/100" in line for line in text.splitlines()) == n
        assert "глубокий сон — — мин" in text and "None" not in text
    else:
        assert "данных о сне нет" in text and "Среднее" not in text


@pytest.mark.parametrize("medical,visible", [({}, []),
    ({"treatment_status": None, "chemo_ended": "—", "next_appointment": "  "}, []),
    ({"treatment_status": "Выдуманный статус", "chemo_ended": "Выдуманная запись", "next_appointment": "Выдуманный визит"},
     ["Статус лечения", "Химиотерапия завершена", "Следующее обследование"])])
def test_memory_omits_unset_treatment_fields(db, monkeypatch, medical, visible):
    from handlers import meta
    import memory_facts_db
    import i18n
    monkeypatch.setattr(i18n, "lang_of", lambda profile=None: "ru")
    monkeypatch.setattr(meta.db, "init_db", lambda: None)
    monkeypatch.setattr(memory_facts_db, "get_facts", lambda **kw: [])
    monkeypatch.setattr(meta.db, "get_profile_context", lambda: {"medical": medical})
    message = _message()
    asyncio.run(meta.cmd_memory(SimpleNamespace(message=message, effective_chat=message.chat), None))
    text = message.get_bot().send_message.await_args.kwargs["text"]
    for label in ("Статус лечения", "Химиотерапия завершена", "Следующее обследование"):
        assert (label in text) == (label in visible)
    assert "я начну запоминать важное" in text and "арбитр" not in text


# Три значка: 🔴 проверенный носитель патогенного; 🟡 патогенный, но носительство
# НЕ определено (NULL/непроверенный аллель — «неизвестно», не «чисто»:
# genome_effect_allele.null_is_unknown_not_clean) — к врачу тоже; ⚪ — не носитель
# или метка не патогенная.
@pytest.mark.parametrize("genotype,allele,status,significance,icon", [
    ("AA", "A", "resolved", "Pathogenic", "🔴"),
    ("AA", "A", "resolved", "Likely pathogenic", "🔴"),
    ("AC", "C", "palindromic_het_resolved", "Pathogenic", "🔴"),
    ("AA", "A", "resolved", "Pathogenic; risk factor", "🔴"),
    ("GG", "A", "resolved", "Pathogenic", "⚪"),
    ("AA", "", "unresolved", "Pathogenic", "🟡"),
    ("AG", None, "palindromic", "Pathogenic", "🟡"),
    ("AA", "A", "unresolved", "Pathogenic", "🟡"),
    ("AA", "A", "resolved", "Conflicting classifications of pathogenicity", "⚪"),
    ("AA", "A", "resolved", "Uncertain significance", "⚪"),
])
def test_genome_attention_needs_verified_carrier_and_pathogenicity(db, monkeypatch,
        genotype, allele, status, significance, icon):
    attention = icon != "⚪"
    from handlers import genome
    import i18n
    monkeypatch.setattr(i18n, "lang_of", lambda profile=None: "ru")
    # Entire record is synthetic; disease name is the translation example from the task.
    variant = {"gene": "EXAMPLE", "rsid": "rsExample", "genotype": genotype,
               "effect_allele": allele, "effect_allele_status": status,
               "significance": significance, "conditions": '["Hereditary hemochromatosis"]'}
    text = genome._variant_entry(variant)
    assert text.startswith(icon)
    assert ("Запишись к врачу в ближайшие недели" in text) == attention
    assert ("определить не удалось" in text) == (icon == "🟡")
    assert "наследственный гемохроматоз — накопление железа" in text
    assert "Pathogenic" not in text and "Hereditary" not in text


def test_checkin_exit_uses_delivered_button_saves_once_and_rejects_old_button(db, monkeypatch):
    from handlers import meta
    import bot.helpers
    import bot.filters
    import i18n
    monkeypatch.setattr(i18n, "lang_of", lambda profile=None: "ru")
    monkeypatch.setattr(bot.filters, "owner_chat_id", lambda: 7)
    state = meta.ck.CheckinState()
    monkeypatch.setattr(meta.ck, "checkin_state", state)
    monkeypatch.setattr(meta.ck, "generate_opening_question", lambda: "Выдуманный вопрос?")
    saved = AsyncMock()
    monkeypatch.setattr(bot.helpers, "_finalize_checkin_background", saved)
    message = _message()
    context = SimpleNamespace(bot=message.get_bot(), user_data={})

    async def scenario():
        await meta.cmd_checkin(SimpleNamespace(message=message), context)
        sent = message.reply_text.await_args
        button = sent.kwargs["reply_markup"].inline_keyboard[0][0]
        assert button.text == "Хватит на сегодня"
        assert "Можно ответить позже — разговор подождёт." in sent.args[0]
        query = SimpleNamespace(data=button.callback_data, message=message, answer=AsyncMock(),
                                edit_message_reply_markup=AsyncMock())
        update = SimpleNamespace(effective_chat=message.chat, callback_query=query)
        state.add_user("Выдуманный ответ о дне.")
        snapshot = list(state.conversation)
        # The same button must not operate for a different chat.
        await actions.handle_callback(SimpleNamespace(effective_chat=SimpleNamespace(id=8), callback_query=query), context)
        assert state.active
        await actions.handle_callback(update, context)
        await asyncio.sleep(0)
        assert not state.active and state.conversation == []
        saved.assert_awaited_once_with(snapshot)
        await actions.handle_callback(update, context)
        saved.assert_awaited_once()
        state.start("Новый выдуманный вопрос?")
        state.started_at = datetime(2035, 1, 1, tzinfo=timezone.utc)
        await actions.handle_callback(update, context)
        assert state.active, "An old button must not stop a new conversation"
        saved.assert_awaited_once()

    asyncio.run(scenario())


def test_hypothesis_review_is_reachable_from_list_button(db, monkeypatch):
    from handlers import hypotheses
    import i18n
    monkeypatch.setattr(i18n, "lang_of", lambda profile=None: "ru")
    monkeypatch.setattr(hypotheses.hai, "get_open_hypotheses", lambda **kw: [
        {"memory_id": 42, "status": "open", "observation": "Выдуманное наблюдение"}])
    monkeypatch.setattr(hypotheses.db, "get_hypothesis_outcome", lambda mid: {
        "verdict": "partial", "confidence": .62, "evaluated_at": "2030-01-01", "sent_at": "2030-01-01"})
    evaluate = AsyncMock()
    monkeypatch.setattr(hypotheses, "_evaluate_hypothesis", evaluate)
    message = _message()
    asyncio.run(hypotheses._send_hypotheses(message))
    sent = message.reply_text.await_args
    assert "частично подтвердилась, уверенность 62%" in sent.args[0]
    button = next(b for row in sent.kwargs["reply_markup"].inline_keyboard for b in row
                  if b.callback_data == "act:he:42")
    _press(monkeypatch, button.callback_data, message)
    evaluate.assert_awaited_once_with(message, 42)


def test_onboarding_controls_are_attached_to_intro_stop_and_summary(db, monkeypatch):
    from handlers import meta
    import assessment_bot_handlers as abh

    message = _message()
    message.chat.send_message = AsyncMock()
    started, stopped = [], []
    monkeypatch.setattr(abh, "start_onboarding", lambda cid: (
        started.append(cid) or "Invented intro", [[("English", "cb_aa:language:1")]], 12))
    monkeypatch.setattr(meta.db, "get_active_assessment_session", lambda *a, **k: {"id": 12})
    monkeypatch.setattr(abh.ad, "abandon", lambda sid, reason: stopped.append(sid) or True)
    asyncio.run(meta._begin_onboarding(message.chat))
    message.reply_markup = message.chat.send_message.await_args.kwargs["reply_markup"]
    stop = message.reply_markup.inline_keyboard[-1][0]
    assert stop.callback_data == "act:ob_stop:"
    _press(monkeypatch, stop.callback_data, message)
    assert stopped == [12]
    resume = message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
    _press(monkeypatch, resume.callback_data, message)
    assert started == [7, 7]

    monkeypatch.setattr(abh.ad, "current_item", lambda session: None)
    asyncio.run(abh._send_answer(SimpleNamespace(effective_chat=message.chat), None,
                                {"instrument_id": "onboarding"}, "Invented summary", None, True))
    redo = message.chat.send_message.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert redo.callback_data == "act:ob_redo:"


def test_active_assessment_snoozes_its_own_task(db, monkeypatch):
    from datetime import timedelta
    from _time_inject import get_today
    import assessment_bot_handlers as abh
    import bot.filters
    import tasks_db

    monkeypatch.setattr(bot.filters, "owner_chat_id", lambda: 7)
    tid = tasks_db.save_task("test", "assessment", "Invented questionnaire")
    sid = abh.db.save_assessment_session("invented", "test", chat_id=7, task_id=tid)
    session = abh.db.get_assessment_session(sid)
    monkeypatch.setattr(abh.ad, "current_item", lambda session: None)
    message = _message()
    message.chat.send_message = AsyncMock()
    update = SimpleNamespace(message=message, effective_chat=message.chat)
    asyncio.run(abh.handle_text_in_assessment(update, None, session))
    button = message.chat.send_message.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.callback_data == f"cb_as:s3:{tid}"
    query = SimpleNamespace(data=button.callback_data, message=message,
                            answer=AsyncMock(), edit_message_reply_markup=AsyncMock())
    update.callback_query = query
    asyncio.run(abh.cb_router(update, None))
    row = db.execute("SELECT status, deadline FROM tasks WHERE id=?", (tid,)).fetchone()
    assert tuple(row) == ("snoozed", (get_today() + timedelta(days=3)).isoformat())
    assert abh.ad.get_active(7) is None, "A postponed questionnaire must release free text routing"
