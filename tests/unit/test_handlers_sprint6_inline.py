"""
tests/unit/test_handlers_sprint6_inline.py — закрытие долга по inline 6b.

Покрывает модули Sprint 6 C3-C12, для которых tests не были добавлены
в момент extract. Structural тесты (register signature, функции на месте)
+ behavioral где легко мокается.

См. audit/sprint_6_plan_2026-05-23.md и CHANGELOG 2026-05-23.
"""
from __future__ import annotations

import json

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ── Helpers ──────────────────────────────────────────────────────────────────

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


def _make_context(args=None, user_data=None):
    ctx = SimpleNamespace(args=args or [], user_data=user_data or {}, bot=MagicMock())
    return ctx


# ── Structural тесты для всех модулей ────────────────────────────────────────

def test_all_handler_modules_export_register():
    """Sprint 6 contract: каждый handlers/* и jobs/* экспортирует register()."""
    import handlers.callbacks
    import handlers.consult
    import handlers.genome
    import handlers.hypotheses
    import handlers.messages
    import handlers.problems
    import handlers.reports
    import jobs.scheduled

    # 2-параметровая сигнатура (app, owner_filter)
    for m in [handlers.consult, handlers.genome, handlers.hypotheses,
              handlers.messages, handlers.problems, handlers.reports]:
        assert hasattr(m, "register"), f"{m.__name__} missing register"
        sig = inspect.signature(m.register)
        assert len(sig.parameters) == 2, f"{m.__name__}.register sig={sig}"

    # handlers.callbacks — register(app, owner_filter=None) → 1 обязательный
    assert hasattr(handlers.callbacks, "register")
    cb_sig = inspect.signature(handlers.callbacks.register)
    assert len(cb_sig.parameters) >= 1

    # jobs.scheduled.register(app) — 1 параметр
    assert hasattr(jobs.scheduled, "register")
    js_sig = inspect.signature(jobs.scheduled.register)
    assert len(js_sig.parameters) == 1


def test_bot_main_exports_callable_main():
    """bot/main.py: main() callable, no required args."""
    import bot.main
    assert callable(bot.main.main)
    sig = inspect.signature(bot.main.main)
    assert all(p.default is not inspect.Parameter.empty
               for p in sig.parameters.values()) or len(sig.parameters) == 0


def test_bot_helpers_exports():
    """bot/helpers.py — все 5 shared helpers на месте."""
    import bot.helpers as h
    for fn in ("refresh_data", "_send_problem_proposals",
               "_send_tasks_from_report", "_run_arbiter_background",
               "_finalize_checkin_background"):
        assert hasattr(h, fn), f"bot.helpers missing {fn}"


def test_services_recommendations_exports():
    """services/recommendations.py — публичный API."""
    import services.recommendations as r
    assert callable(r.evaluate_domain_need)
    assert callable(r._signal_reason)


# ── handlers.reports ─────────────────────────────────────────────────────────

def test_reports_labs_no_data(tg):
    """/labs без данных → 'Нет данных анализов'."""
    from handlers.reports import cmd_labs
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/labs", chat_id=owner_chat_id()), tg)
    ctx = _make_context()

    with patch("handlers.reports.db.init_db", return_value=None), \
         patch("handlers.reports.db.get_recent_labs", return_value=[]):
        asyncio.run(cmd_labs(upd, ctx))

    assert any("Результатов анализов пока нет" in m["text"] for m in tg.outgoing)


# ── handlers.problems ────────────────────────────────────────────────────────

def test_problems_approve_no_args(tg):
    from handlers.problems import cmd_approve
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/approve", chat_id=owner_chat_id()), tg)
    ctx = _make_context(args=[])

    with patch("handlers.problems.db.get_pending_proposals", return_value=[]):
        asyncio.run(cmd_approve(upd, ctx))

    # Партия 5 (28.09): без номера — список ожидающих с кнопками, а не «Укажи ID».
    import i18n
    assert any(i18n.t("problems.reply.no_pending") in m["text"] for m in tg.outgoing)


def test_problems_reject_invalid_id(tg):
    from handlers.problems import cmd_reject
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/reject abc", chat_id=owner_chat_id()), tg)
    ctx = _make_context(args=["abc"])

    asyncio.run(cmd_reject(upd, ctx))

    assert any("Номер должен быть числом" in m["text"] for m in tg.outgoing)


def test_problems_approve_calls_apply_proposal(tg):
    from handlers.problems import cmd_approve
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/approve 7", chat_id=owner_chat_id()), tg)
    ctx = _make_context(args=["7"])

    with patch("handlers.problems.db.apply_proposal", return_value=3) as mock_apply:
        asyncio.run(cmd_approve(upd, ctx))

    mock_apply.assert_called_once_with(7)
    assert any("Изменений в списке проблем: 3" in m["text"] for m in tg.outgoing)


# ── handlers.hypotheses ──────────────────────────────────────────────────────

def test_hypotheses_list_empty(tg):
    from handlers.hypotheses import cmd_hypotheses
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/hypotheses", chat_id=owner_chat_id()), tg)
    ctx = _make_context()

    with patch("handlers.hypotheses.hai.get_open_hypotheses", return_value=[]):
        asyncio.run(cmd_hypotheses(upd, ctx))

    assert any("Открытых гипотез нет" in m["text"] for m in tg.outgoing)


def test_hypotheses_confirm_not_found(tg):
    from handlers.hypotheses import cmd_confirm
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/confirm 99", chat_id=owner_chat_id()), tg)
    ctx = _make_context(args=["99"])

    with patch("handlers.hypotheses.hai.confirm_hypothesis", return_value=None):
        asyncio.run(cmd_confirm(upd, ctx))

    assert any("не найдена" in m["text"] for m in tg.outgoing)


def test_hypotheses_hreject_invalid(tg):
    from handlers.hypotheses import cmd_hreject
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/hreject xyz", chat_id=owner_chat_id()), tg)
    ctx = _make_context(args=["xyz"])

    asyncio.run(cmd_hreject(upd, ctx))
    assert any("Номер должен быть числом" in m["text"] for m in tg.outgoing)


def test_hypotheses_protocols_empty(tg):
    from handlers.hypotheses import cmd_protocols
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/protocols", chat_id=owner_chat_id()), tg)
    ctx = _make_context()

    with patch("handlers.hypotheses.hai.get_active_protocols", return_value=[]):
        asyncio.run(cmd_protocols(upd, ctx))

    assert any("Действующих планов нет" in m["text"] for m in tg.outgoing)


# ── handlers.genome ──────────────────────────────────────────────────────────

def test_genome_empty_db(tg, monkeypatch, fault_journal):
    """/genome без данных → человеку понятный ответ, подробности в журнал."""
    from handlers.genome import cmd_genome
    from bot.filters import owner_chat_id
    import i18n
    import notify
    operator = []
    monkeypatch.setattr(notify, "notify_operator", lambda msg: operator.append(msg) or "telegram")

    upd = _attach_reply(tg.make_update(text="/genome", chat_id=owner_chat_id()), tg)
    ctx = _make_context(args=[])

    with patch("handlers.genome.db.init_db", return_value=None), \
         patch("handlers.genome.db.get_significant_variants", return_value=[]):
        asyncio.run(cmd_genome(upd, ctx))

    assert len(tg.outgoing) == 1
    assert tg.outgoing[0]["text"] == i18n.t("genome.reply.empty")
    assert "genome_parser" not in tg.outgoing[0]["text"]
    assert operator == []
    records = [json.loads(line) for line in fault_journal.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["where"] == 'handlers/genome.py:cmd_genome'
    assert "cmd_genome: no significant variants; check genome intake/pipeline" in records[0]["text"]


# ── handlers.tasks (visit) ───────────────────────────────────────────────────

def test_tasks_visit_invalid_date(tg):
    from handlers.tasks import cmd_visit
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/visit bad-date oncologist", chat_id=owner_chat_id()), tg)
    ctx = _make_context()

    asyncio.run(cmd_visit(upd, ctx))
    assert any("Нужна дата в порядке год-месяц-день" in m["text"] for m in tg.outgoing)


# ── handlers.consult ─────────────────────────────────────────────────────────

def test_consult_end_no_session(tg):
    """/end без активной сессии → 'Раундов: 0' + delete session."""
    from handlers.consult import cmd_consult_end
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="/end", chat_id=owner_chat_id()), tg)
    ctx = _make_context(user_data={})

    with patch("handlers.consult.db.delete_consultation_session", return_value=None):
        asyncio.run(cmd_consult_end(upd, ctx))

    assert any("Раундов: 0" in m["text"] for m in tg.outgoing)


def test_consult_continue_no_session_no_saved(tg):
    """cmd_consult_continue без user_data и без saved → 'Сессия не найдена'."""
    from handlers.consult import cmd_consult_continue
    from bot.filters import owner_chat_id

    upd = _attach_reply(tg.make_update(text="продолжи", chat_id=owner_chat_id()), tg)
    ctx = _make_context(user_data={})

    with patch("handlers.consult.db.load_consultation_session", return_value=None):
        asyncio.run(cmd_consult_continue(upd, ctx))

    assert any("Сессия не найдена" in m["text"] for m in tg.outgoing)


# ── handlers.callbacks ───────────────────────────────────────────────────────

def test_callback_doc_review_blocks_stranger():
    """Fail-closed: посторонний chat_id → silent return, нет вызовов DB."""
    from handlers.callbacks import callback_doc_review
    from bot.filters import owner_chat_id

    stranger_chat = SimpleNamespace(id=owner_chat_id() + 9999)
    upd = SimpleNamespace(
        effective_chat=stranger_chat,
        callback_query=SimpleNamespace(
            data=f"docrev_1_oncology",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
            message=SimpleNamespace(text="header\n/path/file.pdf"),
        ),
    )
    ctx = _make_context()

    with patch("handlers.callbacks.db.confirm_doc_review") as mock_confirm:
        asyncio.run(callback_doc_review(upd, ctx))

    mock_confirm.assert_not_called()


def test_cb_router_with_owner_check_blocks_stranger():
    from handlers.callbacks import cb_router_with_owner_check
    from bot.filters import owner_chat_id

    stranger_chat = SimpleNamespace(id=owner_chat_id() + 1)
    upd = SimpleNamespace(effective_chat=stranger_chat)
    ctx = _make_context()

    with patch("handlers.callbacks.abh.cb_router", new_callable=AsyncMock) as mock_router:
        asyncio.run(cb_router_with_owner_check(upd, ctx))

    mock_router.assert_not_called()


# ── services.recommendations ─────────────────────────────────────────────────

def test_recommendations_no_signals_no_protocols():
    """Domain без сигналов и без протоколов → needed=False."""
    from services.recommendations import evaluate_domain_need

    with patch("services.recommendations.db.get_domain_signals", return_value=[]), \
         patch("services.recommendations.db.get_metric_percentiles", return_value={}), \
         patch("services.recommendations.db.get_absolute_thresholds", return_value=[]), \
         patch("services.recommendations.db.get_active_protocols", return_value=[]):
        result = evaluate_domain_need("unknown_domain")

    assert result["needed"] is False
    assert result["urgency"] == "none"


def test_recommendations_floor_fired():
    """ABSOLUTE_FLOOR triggers даже без сигналов (Q-2 BUG-FLOORS-ORDER fix)."""
    from services.recommendations import evaluate_domain_need

    pcts = {"hrv": {"percentile": 50, "value": 12, "mean": 25}}
    floors = [{"metric": "hrv", "value": 15, "reason_template": "HRV {val:.0f} критично"}]
    protocols = [{"id": 1, "title": "Дыхание 4-7-8", "domain": "vagal_activation"}]

    with patch("services.recommendations.db.get_domain_signals", return_value=[]), \
         patch("services.recommendations.db.get_metric_percentiles", return_value=pcts), \
         patch("services.recommendations.db.get_absolute_thresholds",
               side_effect=lambda kind: floors if kind == "floor" else []), \
         patch("services.recommendations.db.get_active_protocols", return_value=protocols), \
         patch("services.recommendations.db.get_active_constraints", return_value=[]):
        result = evaluate_domain_need("vagal_activation")

    assert result["needed"] is True
    assert "критично" in result["reasons"][0]


def test_signal_reason_known_metrics():
    """_signal_reason возвращает читаемый текст для известных метрик."""
    from services.recommendations import _signal_reason
    assert "ВСР" in _signal_reason("hrv", 12, 30, 5)
    assert "стресс" in _signal_reason("stress_high_min", 120, 30, 5).lower()
    assert "REM" in _signal_reason("sleep_rem", 0.5, 1.5, 10)


# ── bot.utils ────────────────────────────────────────────────────────────────

def test_send_long_short_message():
    """send_long с коротким сообщением → один send_message вызов."""
    from bot.utils import send_long

    bot_mock = MagicMock()
    bot_mock.send_message = AsyncMock()

    asyncio.run(send_long(bot_mock, chat_id=42, text="короткое"))

    assert bot_mock.send_message.call_count == 1
    args = bot_mock.send_message.call_args
    assert args.kwargs["chat_id"] == 42
    assert args.kwargs["text"] == "короткое"


def test_send_long_splits_long_message():
    """send_long с длинным сообщением (>4000) → несколько send_message."""
    from bot.utils import send_long

    bot_mock = MagicMock()
    bot_mock.send_message = AsyncMock()

    text = "\n".join(["строка %d" % i for i in range(800)])  # > 4000 chars
    assert len(text) > 4000

    asyncio.run(send_long(bot_mock, chat_id=1, text=text))

    assert bot_mock.send_message.call_count >= 2


def test_callback_doc_review_with_attached_file_confirms_and_edits_caption():
    """28.09.2026: карточка с файлом — документ с подписью, message.text = None. Обработчик
    падал на разборе текста и тип не подтверждал. Теперь: подтверждает, правит подпись."""
    from contextlib import contextmanager
    from handlers.callbacks import callback_doc_review
    from bot.filters import owner_chat_id

    class _Conn:
        def execute(self, *a):
            return SimpleNamespace(fetchone=lambda: ("inbox/2026-01-10_lab.pdf",))

    @contextmanager
    def _get_conn():
        yield _Conn()

    q = SimpleNamespace(data="docrev_7_lab", answer=AsyncMock(),
                        edit_message_text=AsyncMock(), edit_message_caption=AsyncMock(),
                        message=SimpleNamespace(text=None, caption="📄 Новый документ"))
    upd = SimpleNamespace(effective_chat=SimpleNamespace(id=owner_chat_id()), callback_query=q)
    with patch("handlers.callbacks.db.get_conn", _get_conn), \
         patch("handlers.callbacks.db.confirm_doc_review") as confirm, \
         patch("handlers.callbacks.doc_import.apply_confirmed_type"):
        asyncio.run(callback_doc_review(upd, _make_context()))

    confirm.assert_called_once_with(7, "lab")
    q.edit_message_text.assert_not_called()
    assert "2026-01-10_lab.pdf" in q.edit_message_caption.call_args.kwargs["caption"]
