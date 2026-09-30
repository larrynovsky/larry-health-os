"""Гейт «вопрос не закрывается пустотой» — §20-контроль на рукодельной базе.

Судится МЕХАНИЗМ: тот самый DDL, который миграция ставит в боевую базу
(health_db.question_answer_gate_ddl), применяется к временной sqlite и ломается
руками. Боевая база здесь не при чём — тест обязан краснеть на снятом триггере,
а не зеленеть оттого, что на диске случайно лежит правильный health.db.

Замер, ради которого гейт существует (2026-09-12): 7 задач type='question' за
историю системы, содержательных ответов 0. Закрывали галочкой в Reminders
(resolved_text='completed via Reminders.app') и кнопкой дашборда (NULL) —
и закрытый пустотой вопрос был неотличим от отвеченного.
"""
from __future__ import annotations

import sqlite3

import pytest

import health_db as hdb

pytestmark = pytest.mark.unit


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            content TEXT NOT NULL,
            status TEXT DEFAULT 'open',
            resolved_at TEXT,
            resolved_text TEXT
        )
    """)
    for stmt in hdb.question_answer_gate_ddl():
        conn.execute(stmt)
    return conn


def _open_question(conn) -> int:
    cur = conn.execute(
        "INSERT INTO tasks (type, content, status) VALUES ('question', 'вопрос?', 'open')")
    return cur.lastrowid


@pytest.mark.parametrize("text", [None, "", "   ", "completed via Reminders.app"])
def test_close_without_answer_is_rejected(text):
    """Три пути закрытия без ответа (дашборд → NULL, пустая строка, галочка)."""
    conn = _db()
    tid = _open_question(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE tasks SET status='completed', resolved_text=? WHERE id=?",
            (text, tid))
    assert conn.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0] == "open"


def test_close_with_answer_passes():
    """Позитивный контроль: гейт не запрещает закрытие ОТВЕТОМ."""
    conn = _db()
    tid = _open_question(conn)
    conn.execute("UPDATE tasks SET status='completed', resolved_text=? WHERE id=?",
                 ("Обследование X перенесли на октябрь", tid))
    row = conn.execute("SELECT status, resolved_text FROM tasks WHERE id=?", (tid,)).fetchone()
    assert row[0] == "completed" and "октябр" in row[1]


def test_other_types_are_not_touched():
    """Негативный контроль: у lab_test ответ — факт делания, галочка законна.

    Без этой проверки гейт мог бы тихо расползтись на все типы и сломать
    Reminders-путь, ради которого ремайндеры и существуют."""
    conn = _db()
    cur = conn.execute(
        "INSERT INTO tasks (type, content, status) VALUES ('lab_test', 'сдать липазу', 'open')")
    conn.execute("UPDATE tasks SET status='completed', resolved_text=? WHERE id=?",
                 ("completed via Reminders.app", cur.lastrowid))
    assert conn.execute("SELECT status FROM tasks WHERE id=?",
                        (cur.lastrowid,)).fetchone()[0] == "completed"


@pytest.mark.parametrize("tclass,age_days,expect_ask", [
    ("durable",   400, False),   # генетика/анатомия: правда не меняется
    ("standing",  400, False),   # держится до явной смены, не по возрасту
    ("transient",   2, False),   # свежий разовый ответ
    ("transient",  30, True),    # разовый ответ протух (ttl 7 дн.)
])
def test_should_ask_again_follows_temporal_class(monkeypatch, tclass, age_days, expect_ask):
    """Перезадавание решает ось времени памяти, а не календарь GP-отчётов.

    Вторую ось для этого не заводили: класс уже стоит на записи ответа
    (memory_temporal_axis), а инвариант read_side_enforcement_open говорит, что
    чтение его до сих пор не спрашивало. Этот читатель — первый, кто спрашивает."""
    import datetime as _dt

    import task_agent as ta

    stamp = (ta.get_today() - _dt.timedelta(days=age_days)).isoformat()
    fake_facts = [{"key": "lab:ALT", "value": "ответ", "temporal_class": tclass,
                   "valid_from": stamp}]

    import memory_facts_db as mf
    import config_db as cfg
    monkeypatch.setattr(mf, "get_facts", lambda *a, **k: fake_facts)
    monkeypatch.setattr(cfg, "get_config", lambda key, default=None: 7.0)

    ask, why = ta.should_ask_again("lab:ALT")
    assert ask is expect_ask, why


def test_should_ask_again_without_answer():
    """Ответа нет — спрашиваем. Иначе вопрос молча исчезнет, не быв заданным."""
    import task_agent as ta
    import memory_facts_db as mf
    _orig = mf.get_facts
    mf.get_facts = lambda *a, **k: []
    try:
        ask, _ = ta.should_ask_again("lab:NEVER_ASKED")
        assert ask is True
    finally:
        mf.get_facts = _orig


def test_judge_failure_means_not_a_question(monkeypatch):
    """Fail-closed: судья не ответил — кандидат НЕ становится вопросом.

    Цена ошибки несимметрична: лишний вопрос человеку, на который он не может
    ответить, выключает канал целиком (прецедент 19278 сообщений 01.08), а
    пропущенный вопрос вернётся со следующим отчётом. Поэтому молчание судьи
    читается как «не пациенту», а не как «пропустить всё»."""
    import task_agent as ta

    monkeypatch.setattr(ta, "addressed_to_patient", lambda items: {})
    raw = [{"type": "question", "content": "Ответить: что с обследованием X?"},
           {"type": "lab_test", "content": "Сдать липазу"}]

    # воспроизводим ровно тот блок, что стоит в extract_tasks_from_report
    cands = [{"id": str(i), "text": t["content"]} for i, t in enumerate(raw)
             if t["type"] == "question"]
    verdicts = ta.addressed_to_patient(cands)
    for i, t in enumerate(raw):
        if t["type"] == "question" and verdicts.get(str(i), {}).get("verdict") not in ta.ASKING_VERDICTS:
            t["type"] = "action"

    assert [t["type"] for t in raw] == ["action", "lab_test"]


def test_doubt_becomes_a_question_not_an_action(monkeypatch):
    """Сомнение судьи → содержательный ВОПРОС, а не действие и не молчание.

    Правило владельца 2026-09-12: «начинать надо с вопроса — актуально ли, есть
    ли симптомы; если есть неуверенность, сначала содержательный вопрос».
    До него unsure вело бы к понижению в action, и человека не спросили бы
    о том, чего система не знает."""
    import task_agent as ta

    monkeypatch.setattr(ta, "addressed_to_patient", lambda items: {
        "0": {"verdict": "unsure", "reason": "неизвестно, появились ли симптомы",
              "ru": "Появились ли симптомы после контакта?"}})
    raw = [{"type": "question",
            "content": "Наблюдать за симптомами после контакта с инфекцией X"}]

    verdicts = ta.addressed_to_patient([{"id": "0", "text": raw[0]["content"]}])
    v = verdicts.get("0")
    if not v or v["verdict"] not in ta.ASKING_VERDICTS:
        raw[0]["type"] = "action"
    elif v["verdict"] == "unsure" and v.get("ru"):
        raw[0]["content"] = v["ru"]

    assert raw[0]["type"] == "question"
    assert raw[0]["content"] == "Появились ли симптомы после контакта?"
    assert "unsure" in ta.ASKING_VERDICTS and "patient" in ta.ASKING_VERDICTS
    assert "not_patient" not in ta.ASKING_VERDICTS


def test_judge_is_one_home_for_both_callers():
    """Судья один на оба места суждения — экстрактор и подъём из памяти.

    Два промпта уже разошлись: экстрактор пропускал «согласовать с врачом»
    как вопрос, гейт памяти такое отсекал. Проверяем структурно: старого второго
    промпта в модуле нет, оба пути зовут addressed_to_patient."""
    import inspect

    import task_agent as ta

    assert not hasattr(ta, "MEMORY_QUESTION_GATE_PROMPT"), "второй дом суждения вернулся"
    for fn in (ta.promote_memory_questions, ta.extract_tasks_from_report):
        assert "addressed_to_patient" in inspect.getsource(fn), fn.__name__


def test_delivery_predicate_is_the_reply_address_not_sent_at():
    """Доставлять надо тому вопросу, на который НЕКУДА ответить.

    Признак — отсутствие tg_message_id, не sent_at: вопросы старше канала
    (2026-09-12) помечены отправленными ремайндером и по критерию sent_at
    остались бы немыми навсегда — задача открыта, человек её видит в списке,
    а ответить не может. Так висел #192 у владельца и все шесть у партнёра.

    Запрос читается из живого модуля — фикстура повторила бы его текст и
    разъехалась бы при первой правке (§20)."""
    import inspect

    import tasks_db

    # Докстринг отрезаем: он объясняет, почему НЕ sent_at, и содержит обе фразы —
    # проверять надо исполняемый запрос, а не текст объяснения.
    body = inspect.getsource(tasks_db.get_questions_needing_delivery).split('"""')[-1]
    assert "tg_message_id IS NULL" in body
    assert "sent_at IS NULL" not in body

    conn = _db()
    conn.execute("ALTER TABLE tasks ADD COLUMN sent_at TEXT")
    conn.execute("ALTER TABLE tasks ADD COLUMN tg_message_id INTEGER")
    conn.execute("INSERT INTO tasks (type, content, status, sent_at, tg_message_id) "
                 "VALUES ('question', 'старый вопрос', 'open', '2026-09-06', NULL)")
    conn.execute("INSERT INTO tasks (type, content, status, sent_at, tg_message_id) "
                 "VALUES ('question', 'уже доставлен', 'open', '2026-09-12', 3786)")
    need = [r[0] for r in conn.execute(
        "SELECT content FROM tasks WHERE status='open' AND type='question' "
        "AND tg_message_id IS NULL")]
    assert need == ["старый вопрос"]


def test_temporal_class_is_derived_from_the_answer_not_the_question():
    """Класс ответа считается по ОТВЕТУ, а не по склейке «вопрос → ответ».

    Первые ответы (2026-09-12) показали дефект глазами: вопрос о заборе
    крови с датой (синтетика той же формы ниже) содержит дату, и склейка получала
    transient, хотя ответ «да, состоялся» ничем не transient. Детектор тот же —
    другой вход (class_from_structure не нарушен)."""
    import memory_facts_db as mf

    question = "Ответить: состоялся ли плановый забор крови 3 марта 2021?"
    answer = "Да"
    assert mf.derive_temporal_class(f"{question} → {answer}") == "transient"
    assert mf.derive_temporal_class(answer) == "standing"


def test_critical_flag_still_wins_over_passed_class():
    """never-decay сильнее переданного класса — иначе флаг и ось разъедутся."""
    import memory_facts_db as mf
    assert mf.derive_temporal_class("что угодно", critical_flag=1) == "durable"


def test_insert_of_closed_empty_question_is_rejected():
    """Закрытый вопрос нельзя и ВСТАВИТЬ пустым — иначе гейт обходится импортом."""
    conn = _db()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO tasks (type, content, status, resolved_text) "
            "VALUES ('question', 'вопрос?', 'completed', NULL)")
