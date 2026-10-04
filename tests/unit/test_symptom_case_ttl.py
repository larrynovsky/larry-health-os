"""У незаконченного разбора симптома есть срок жизни и исход (нить symptom-ttl, решение владельца 03.10 — спрашивать).

Замер 03.10: два кейса тенанта висели open больше двух месяцев — человек прислал фото
и не ответил на уточнение, разговор бот потерял на перезапуске, а база помнила кейс открытым. Датчик
напоминал об этом каждую ночь, а исхода у кейса не было.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((text, reply_markup))


def _case(db, status="open", days_idle=10, hyp=None, decided_days=None):
    db.execute("INSERT INTO visual_case (chat_id, tenant, domain, region, status, hypothesis_memory_id, "
               "opened_at, updated_at, decision_requested_at) VALUES (1, 't', NULL, NULL, ?, ?, "
               "datetime('now', ?), datetime('now', ?), "
               "CASE WHEN ? IS NULL THEN NULL ELSE datetime('now', ?) END)",
               (status, hyp, f"-{days_idle} days", f"-{days_idle} days",
                decided_days, f"-{decided_days or 0} days"))
    return db.fetchone("SELECT max(id) AS m FROM visual_case")["m"]


def _run(monkeypatch):
    import jobs.scheduled as js
    monkeypatch.setattr(js, "get_chat_id", lambda: 1)
    bot = _Bot()
    asyncio.run(js.check_visual_followups(SimpleNamespace(bot=bot)))
    return bot


def test_idle_open_case_gets_one_question_with_restart_and_close(db, monkeypatch):
    cid = _case(db, days_idle=10)
    bot = _run(monkeypatch)
    texts = [t for t, _ in bot.sent]
    assert any("остался незаконченным" in t or "unfinished" in t for t in texts), texts
    kb = [m for _, m in bot.sent if m][0]
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert datas == [f"visfu_{cid}_restart", f"visfu_{cid}_close"]
    assert db.fetchone("SELECT status FROM visual_case WHERE id=?", (cid,))["status"] == "awaiting_decision"
    bot2 = _run(monkeypatch)
    assert not any("незаконченным" in t for t, _ in bot2.sent), "вопрос задаётся один раз"


def test_fresh_open_case_is_left_alone(db, monkeypatch):
    cid = _case(db, days_idle=0)
    _run(monkeypatch)
    assert db.fetchone("SELECT status FROM visual_case WHERE id=?", (cid,))["status"] == "open"


def test_no_answer_closes_with_unfinished_wording(db, monkeypatch):
    cid = _case(db, status="awaiting_decision", days_idle=10, decided_days=5)
    bot = _run(monkeypatch)
    assert db.fetchone("SELECT status FROM visual_case WHERE id=?", (cid,))["status"] == "closed"
    assert any("Незаконченный разбор" in t or "unfinished review" in t for t, _ in bot.sent)


def test_sensor_sees_only_a_stuck_mechanism(db):
    import visual_db
    _case(db, status="open", days_idle=2)                       # ещё в сроке
    stuck = _case(db, status="open", days_idle=10)              # вопрос не задан — механизм молчит
    late = _case(db, status="awaiting_decision", days_idle=10, decided_days=5)  # не закрыт
    ids = {r["id"] for r in visual_db.get_cases_stuck(3, 1)}
    assert ids == {stuck, late}


def test_restart_button_closes_and_asks_for_a_photo(db, monkeypatch):
    import health_db
    health_db.init_db()          # импорт обработчиков читает сиды doc_patterns
    import handlers.callbacks as cb
    cid = _case(db, status="awaiting_decision", days_idle=10, decided_days=0)
    monkeypatch.setattr(cb, "owner_chat_id", lambda: 1)
    edited = []

    class _Q:
        data = f"visfu_{cid}_restart"

        async def answer(self):
            return None

        async def edit_message_text(self, text):
            edited.append(text)
    upd = SimpleNamespace(effective_chat=SimpleNamespace(id=1), callback_query=_Q())
    asyncio.run(cb.callback_visual_followup(upd, None))
    assert db.fetchone("SELECT status FROM visual_case WHERE id=?", (cid,))["status"] == "closed"
    assert edited and ("Пришли фото" in edited[0] or "Send a photo" in edited[0])
