"""Оракул: предложение GP по списку проблем доходит до человека само, один раз и с квитанцией.

Плановое создание предложений требует плановой доставки. Название карточки
берётся из new_value.title, если модель положила его туда.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit


def _propose(title: str) -> int:
    import problems_db
    return problems_db.save_problem_proposal("gp_weekly", [{
        "action": "add", "problem_id": "P001", "field": None, "old_value": None,
        "new_value": {"id": "P001", "title": title, "status": "active"},
        "reason": "по данным недели"}])


def _bot(fail: bool = False):
    bot = SimpleNamespace()
    if fail:
        bot.send_message = AsyncMock(side_effect=RuntimeError("telegram down"))
    else:
        bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=777))
    return bot


def test_предложение_доставляется_один_раз_с_квитанцией(db):
    import health_db as hdb
    from bot.helpers import _send_problem_proposals
    pid = _propose("Проблема А — показатель выше целевого")
    bot = _bot()
    asyncio.run(_send_problem_proposals(bot, 42))
    assert bot.send_message.await_count == 1
    text = bot.send_message.await_args.kwargs["text"]
    assert "Проблема А" in text, f"название не попало в карточку:\n{text}"
    assert "/approve" not in text
    buttons = bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard[0]
    assert [b.callback_data for b in buttons] == [f"act:pa:{pid}", f"act:pr:{pid}"]
    row = db.execute("SELECT delivered_at, tg_message_id FROM problem_list_proposals WHERE id=?",
                     (pid,)).fetchone()
    assert row[0] and row[1] == 777
    asyncio.run(_send_problem_proposals(bot, 42))
    assert bot.send_message.await_count == 1, "доставленное прислано повторно"
    assert hdb.get_undelivered_proposals() == []


def test_карточка_без_разметки_текст_модели_не_ломает_отправку(db):
    """Произвольные символы модели отправляются простым текстом без Markdown."""
    from bot.helpers import _send_problem_proposals
    import problems_db
    problems_db.save_problem_proposal("gp_weekly", [{
        "action": "add", "problem_id": "P002", "new_value": {"title": "Marker_high *123*"},
        "reason": "см. `lab_results` и Marker_low"}])
    bot = _bot()
    asyncio.run(_send_problem_proposals(bot, 42))
    kw = bot.send_message.await_args.kwargs
    assert "parse_mode" not in kw, "карточка снова уходит с разметкой"
    assert "Marker_high *123*" in kw["text"]


def test_правка_действующей_проблемы_названа_по_имени_и_сейчас_из_базы(db):
    """Карточка 23.09 «P007 поле: …показыва… → …» читалась как дубль принятой проблемы:
    не было ни названия проблемы, ни поля, а «было» обрезала модель."""
    import proposals_db
    db.execute("INSERT INTO problem_list (problem_id, title, status, notes, first_seen, last_updated) VALUES "
               "('P007', 'Состояние Б', 'watchful_waiting', 'маркер 40.0→25.0', '2021-01-10', '2021-03-01')")
    card = proposals_db.format_proposal_card({"id": 9, "source": "gp_weekly", "repeats": 1,
        "proposed": '[{"action": "update_field", "problem_id": "P007", "field": "notes", '
                    '"old_value": "марк...", "new_value": "маркер 40.0→25.0→31.0"}]'})
    assert "«Состояние Б»" in card
    assert "Что меняется: заметки" in card
    assert "Сейчас: маркер 40.0→25.0" in card, "«сейчас» взято не из базы"
    assert "марк..." not in card
    assert "заменит текущий текст целиком" in card, "не сказано, что одобрение перезапишет заметки"


def test_отказ_отправки_оставляет_в_outbox(db):
    import health_db as hdb
    from bot.helpers import _send_problem_proposals
    pid = _propose("Проблема В")
    asyncio.run(_send_problem_proposals(_bot(fail=True), 42))
    assert [p["id"] for p in hdb.get_undelivered_proposals()] == [pid]


def test_датчик_видит_застрявшее_и_молчит_о_свежем(db):
    import proposals_db
    pid = _propose("Проблема Г")
    total, stuck = proposals_db.stuck_undelivered_proposals()
    assert total == 1 and stuck == [], "свежее предложение названо застрявшим"
    db.execute("UPDATE problem_list_proposals SET created_at=datetime('now','-1 hour') WHERE id=?",
               (pid,))
    total, stuck = proposals_db.stuck_undelivered_proposals()
    assert stuck == [pid]
    proposals_db.mark_proposal_delivered(pid, 1)
    assert proposals_db.stuck_undelivered_proposals() == (0, [])


def test_outbox_зарегистрирован_в_расписании_бота():
    import jobs.scheduled as js
    import proposals_db
    names = {}

    class _JQ:
        def __getattr__(self, _name):
            def _reg(cb, *a, **kw):
                names[kw.get("name") or getattr(cb, "__name__", "?")] = kw
                return []
            return _reg

    app = SimpleNamespace(job_queue=_JQ(), bot_data={})
    err = None
    try:
        js.register(app)
    except Exception as e:  # noqa: BLE001 — регистрация соседних job'ов может требовать конфиг
        err = e
    assert "deliver_pending_proposals" in names, (sorted(names), repr(err))
    assert names["deliver_pending_proposals"]["interval"] == proposals_db.DELIVERY_EVERY_S
