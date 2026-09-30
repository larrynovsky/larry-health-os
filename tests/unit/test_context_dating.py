"""tests/unit/test_context_dating.py — M4 (_chat_context_line) + M5 (pending_chat) staleness.

Два неохваченных канала память→промпт: открытые вопросы (45д, помечались «свежие» без
дат) и сырой мост pending_chat. Фикс — датировать [ГГГГ-ММ-ДД], убрать ложное «свежие».
"""
from __future__ import annotations
from datetime import date, timedelta
import pytest

pytestmark = pytest.mark.unit


def test_chat_context_line_dates_questions_no_fresh_lie(db):
    """M4: вопрос 40-дневной давности датирован своей датой; ложное «свежие» убрано."""
    import patient_context as pc
    import health_db
    old = (date.today() - timedelta(days=40)).isoformat()
    with health_db.get_conn() as c:
        c.execute("INSERT INTO memory_facts (mem_class, value, valid_from, active) "
                  "VALUES ('question', ?, ?, 1)", ("нужен ли дополнительный анализ?", old))
    line = pc._chat_context_line()
    assert "свежие" not in line, "ложное «свежие» убрано"
    assert f"[{old}]" in line, "вопрос несёт свою дату"
    assert "относятся к своей дате" in line


def test_pending_chat_dates_raw_lines(db):
    """M5: сырые строки моста «ТОЛЬКО ЧТО В ЧАТЕ» датированы."""
    import patient_context as pc
    import health_db, hai_core
    hai_core._ensure_history_table()
    ts = date.today().isoformat() + " 11:00:00"
    with health_db.get_conn() as c:
        c.execute("INSERT INTO conversation_history (role, content, created_at) "
                  "VALUES ('user', ?, ?)", ("тест-сообщение-моста", ts))
    out = pc.pending_chat(max_hours=48)
    today = date.today().isoformat()
    assert "тест-сообщение-моста" in out
    assert f"[{today}]" in out, "строка датирована"
