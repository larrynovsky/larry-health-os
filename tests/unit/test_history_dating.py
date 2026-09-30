"""tests/unit/test_history_dating.py — M2 F-A: датирование сырой истории чата.

Недатированный транскрипт позволяет принять старую реплику за текущую.
Вымышленное сообщение о керамике должно сохранить собственную дату из
created_at; собранный контекст несёт правило времени. Два acceptance:
clean → каждое сообщение датировано; injected → старое несёт СВОЮ дату, не сегодня.
"""
from __future__ import annotations
from datetime import date, timedelta
import re
import pytest

pytestmark = pytest.mark.unit


def _seed_msg(role: str, content: str, days_ago: int):
    import hai_core
    import health_db
    hai_core._ensure_history_table()
    ts = (date.today() - timedelta(days=days_ago)).isoformat() + " 12:00:00"
    with health_db.get_conn() as c:
        c.execute("INSERT INTO conversation_history (role, content, created_at) VALUES (?,?,?)",
                  (role, content, ts))


def test_clean_all_history_dated(db):
    """clean → 0 недатированного: каждое сообщение истории несёт префикс [ГГГГ-ММ-ДД]."""
    import hai_core
    _seed_msg("user", "привет", 0)
    _seed_msg("assistant", "здравствуй", 0)
    hist = hai_core.get_history(12)
    assert hist, "история не пуста"
    for m in hist:
        assert re.match(r"^\[\d{4}-\d{2}-\d{2}\] ", m["content"]), f"не датировано: {m['content']!r}"


def test_stale_message_carries_own_date(db):
    """injected → 1: 5-дневная реплика датирована СВОЕЙ датой, не сегодня."""
    import hai_core
    _seed_msg("assistant", "занятие по керамике перенесено на четверг", 5)
    old_day = (date.today() - timedelta(days=5)).isoformat()
    hist = hai_core.get_history(12)
    stale = next(m for m in hist if "по керамике" in m["content"])
    assert stale["content"].startswith(f"[{old_day}] "), stale["content"]


def test_stale_not_presented_as_today_in_assembled_context(db):
    """injected → 1 на ПОЛНОМ собранном контексте (точка потребления): старая реплика
    несёт свою дату, НЕ сегодняшнюю."""
    import hai_chat
    _seed_msg("assistant", "занятие по керамике перенесено на четверг", 5)
    txt = hai_chat.assembled_context_text("что в плане?", include_data=False)
    old_day = (date.today() - timedelta(days=5)).isoformat()
    today = date.today().isoformat()
    assert f"[{old_day}] занятие по керамике перенесено на четверг" in txt, "старое несёт свою дату"
    assert f"[{today}] занятие по керамике перенесено на четверг" not in txt, "старое НЕ датировано сегодня"


def test_time_discipline_line_in_assembled_context(db):
    """Строка-дисциплина времени доходит до модели (не только штамп, но и инструкция)."""
    import hai_chat
    txt = hai_chat.assembled_context_text("тест", include_data=False)
    assert "не переноси прошлые факты" in txt.lower() or "относится к своей дате" in txt.lower()
