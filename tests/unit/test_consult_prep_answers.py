"""Ответы пациента доезжают в отчёт к визиту — на рукодельной базе.

Замер 2026-09-12 (исполнением, не по памяти): ответ доезжает в GP-контекст и в
бриф консилиума (через память), а в `consult_prep` — нет. Врач на приёме видел
открытые задачи и не видел СЛОВ пациента: состоялось ли назначенное, о чём
договорились в прошлый раз.

Тест держит источник (таблица tasks) и окно, не рендер: формулировку заголовка
можно менять, поведение — нет.
"""
from __future__ import annotations

import sqlite3

import pytest

pytestmark = pytest.mark.unit

ANSWER = "Контрольное УЗИ перенесли на следующий месяц"


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT, content TEXT, status TEXT,
            resolved_at TEXT, resolved_text TEXT
        )
    """)
    return conn


def _lines(conn, days: int = 90):
    """Тот же запрос, что в consult_prep._build_patient_answers_lines."""
    import inspect

    import consult_prep as cp

    src = inspect.getsource(cp._build_patient_answers_lines)
    assert "type = 'question'" in src and "status = 'completed'" in src
    assert "TRIM(resolved_text) != ''" in src
    return conn.execute(
        """SELECT content, resolved_text FROM tasks
            WHERE type='question' AND status='completed'
              AND resolved_text IS NOT NULL AND TRIM(resolved_text) != ''
              AND julianday('now') - julianday(resolved_at) <= ?""",
        (days,),
    ).fetchall()


def test_answered_question_reaches_the_visit_report():
    conn = _db()
    conn.execute(
        "INSERT INTO tasks (type, content, status, resolved_at, resolved_text) "
        "VALUES ('question', 'Каков статус контрольного УЗИ?', 'completed', datetime('now'), ?)",
        (ANSWER,))
    rows = _lines(conn)
    assert len(rows) == 1 and rows[0]["resolved_text"] == ANSWER


def test_unanswered_and_stale_are_not_shown():
    """Негативный контроль: пустой ответ и ответ вне окна не попадают.

    Без него тест зеленел бы на любом запросе, который что-нибудь возвращает."""
    conn = _db()
    conn.execute(
        "INSERT INTO tasks (type, content, status, resolved_at, resolved_text) "
        "VALUES ('question', 'пустой', 'completed', datetime('now'), '  ')")
    conn.execute(
        "INSERT INTO tasks (type, content, status, resolved_at, resolved_text) "
        "VALUES ('question', 'древний', 'completed', datetime('now','-400 days'), 'Да')")
    conn.execute(
        "INSERT INTO tasks (type, content, status, resolved_at, resolved_text) "
        "VALUES ('lab_test', 'не вопрос', 'completed', datetime('now'), 'сдал')")
    assert _lines(conn) == []
