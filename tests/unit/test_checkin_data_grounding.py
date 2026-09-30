"""
Чекин не должен утверждать, что сенсорных данных нет.

Вымышленный сценарий: строки сенсорных данных есть, а модель утверждает обратное.
Контекст должен явно сообщать о наличии строк и не считать неотвеченные
сообщения самого бота ответами пользователя.
"""
from __future__ import annotations

import pytest

import checkin_agent as ca

pytestmark = pytest.mark.unit


def test_last_user_checkin_skips_assistant_nags():
    rows = [
        {"date": "2032-03-19", "answer": "ASSISTANT: Эй, ты не записывал данные"},
        {"date": "2032-03-16", "answer": "ASSISTANT: заполни заметку"},
        {"date": "2032-03-11", "answer": "планирую выходной"},
    ]
    lc = ca._last_user_checkin(rows)
    assert lc is not None and lc["date"] == "2032-03-11", \
        "должен вернуть реальный ответ пользователя, а не нэг бота"


def test_last_user_checkin_none_when_all_assistant():
    rows = [{"date": "2032-03-19", "answer": "ASSISTANT: nag"},
            {"date": "2032-03-16", "answer": "assistant: nag2"}]
    assert ca._last_user_checkin(rows) is None


def test_data_truth_note_asserts_presence(db):
    db.execute(
        "INSERT INTO daily_metrics (date, sleep_total, hrv) VALUES (?,?,?)",
        ("2032-04-12", 7.4, 53.0))
    note = ca._data_truth_note()
    assert note, "при наличии данных должен быть заземляющий факт"
    assert "2032-04-12" in note
    assert "не" in note.lower() and ("данных нет" in note or "данных" in note)


def test_data_truth_note_empty_when_no_data(db):
    # пустая daily_metrics → не утверждаем наличие данных
    assert ca._data_truth_note() == ""


def test_checkin_system_carries_guard_when_data_present(db):
    db.execute(
        "INSERT INTO daily_metrics (date, sleep_total, hrv) VALUES (?,?,?)",
        ("2032-04-12", 7.4, 53.0))
    sys_prompt = ca._build_checkin_system()
    assert "ФАКТ О ДАННЫХ" in sys_prompt, "системный промпт беседы должен нести заземление"
