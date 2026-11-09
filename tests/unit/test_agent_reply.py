"""Ответ health ассистенту (agent_reply): модель подменена записывающей заглушкой, окно данных — mcp_tools.call
подменён синтетикой; почтамт и Telegram не зовутся (каталог моста — tmp, страж conftest). Значения выдуманы."""
import json
from types import SimpleNamespace

import pytest

import agent_mail
import agent_reply
import hai_core
import health_db
import llm_client
import mcp_tools
import patient_context
import gp_context
import notify
from tests.unit import test_agent_mail as _mail

box = _mail.box   # фикстура почтового ящика и учёта — та же, что у тестов транспорта

LETTER = {"key": "0" * 23 + "1", "message_id": "<synthetic-q@example>", "peer": "assistant-example",
          "subject": "как спал?", "wrapped": "<письмо>Как он спал сегодня? Стоит ли разгрузить день?</письмо>"}


def _cfg(key, value):
    col = "value_text" if isinstance(value, str) else "value_num"
    with health_db.get_conn() as conn:
        conn.execute(f"INSERT OR REPLACE INTO system_config(key,{col},category,source) VALUES (?,?,'t','t')", (key, value))


@pytest.fixture
def model(box, monkeypatch):
    calls = []
    reply = {"text": "что случилось: ночь 4:50 (данные 2026-01-01)\nсрочность: сегодня", "stop": "end_turn"}

    def create(**kw):
        calls.append(kw)
        return SimpleNamespace(stop_reason=reply["stop"], content=[SimpleNamespace(type="text", text=reply["text"])])
    monkeypatch.setattr(llm_client, "guarded_client", lambda *a, **k: SimpleNamespace(messages=SimpleNamespace(create=create)))
    monkeypatch.setattr(hai_core, "get_model", lambda role: f"model-for-{role}")
    monkeypatch.setattr(patient_context, "reasoning_block", lambda: "жаловался на бессонницу 2026-01-01")
    monkeypatch.setattr(hai_core, "answer_language", lambda lang=None: "")
    monkeypatch.setattr(mcp_tools, "call", _schema_checked_call)
    monkeypatch.setattr(health_db, "get_recent_labs", lambda *a, **kw: [
        {"test_name": "TSH", "date": "2026-01-01"}])
    faults = []
    monkeypatch.setattr(notify, "fault", lambda m, **k: faults.append(m))
    _cfg("agent_mail.assistant", "assistant-example")
    return SimpleNamespace(calls=calls, reply=reply, box=box, faults=faults)


def _out(box):
    return [json.loads(p.read_text()) for p in sorted((box / "out").glob("*.json"))]


def test_ответ_строится_из_окна_данных_и_уходит_ответом_на_письмо(model):
    rid = agent_reply.reply_to_letter(LETTER)
    assert rid and len(model.calls) == 1
    call = model.calls[0]
    assert "при ближайшем заборе" in call["system"]
    assert "{next_draw}" not in call["system"]
    assert call["task"] == "agent_reply.compose" and call["model"] == "model-for-sonnet"
    assert "Источник: Health OS — health_brief" in call["system"] and "day_metrics" in call["system"]
    assert "жаловался на бессонницу" in call["system"]                   # память из разговоров (patient_context)
    assert call["messages"] == [{"role": "user", "content": LETTER["wrapped"]}]   # письмо — только как данные
    req = _out(model.box)
    assert req == [{"to": ["assistant-example"], "subject": "Re: как спал?", "body": model.reply["text"],
                    "kind": "answer", "reply_to": LETTER["message_id"]}]


def test_чужой_адресат_не_получает_ответа_и_модель_не_зовётся(model):
    assert agent_reply.reply_to_letter({**LETTER, "peer": "someone-else"}) is None
    assert model.calls == [] and _out(model.box) == []


def test_пустой_адресат_в_настройке_выключает_ответы(model):
    _cfg("agent_mail.assistant", "")
    assert agent_reply.reply_to_letter(LETTER) is None and model.calls == []


def test_нечего_отвечать_не_шлёт_письмо(model):
    model.reply["text"] = agent_reply.NO_REPLY
    assert agent_reply.reply_to_letter(LETTER) is None and _out(model.box) == []


def test_обрезанный_ответ_ошибка_а_не_полуписьмо(model):
    model.reply["stop"] = "max_tokens"
    with pytest.raises(ValueError, match="обрезан"):
        agent_reply.reply_to_letter(LETTER)
    assert _out(model.box) == []


def test_второй_ответ_на_то_же_письмо_не_пишется(model):
    assert agent_reply.reply_to_letter(LETTER)
    assert agent_reply.reply_to_letter(LETTER) is None and len(model.calls) == 1


def test_предел_ответов_в_час_из_настройки(model):
    _cfg("agent_mail.max_answers_per_hour", 2)
    for i in range(3):
        agent_reply.reply_to_letter({**LETTER, "message_id": f"<q{i}@example>"})
    assert len(model.calls) == 2 and len(_out(model.box)) == 2
    _cfg("agent_mail.max_answers_per_hour", -1)
    with pytest.raises(ValueError, match="max_answers_per_hour"):
        agent_reply.reply_to_letter({**LETTER, "message_id": "<q9@example>"})


def _schema_checked_call(name, args):
    """Заглушка окна данных, которая держит настоящую схему инструмента: 06.11 day_metrics звали
    с {"days": 7} вместо date, заглушка это глотала, а живой вызов падал."""
    schema = next(t for t in mcp_tools.TOOLS if t["name"] == name)["inputSchema"]
    props, req = schema.get("properties", {}), schema.get("required", [])
    assert set(args) <= set(props), (name, args)
    assert all(k in args for k in req), (name, args)
    return f"Источник: Health OS — {name}. Выдано 2026-01-01.\nсон 4.8 ч", False


def test_context_calls_match_tool_schemas():
    for name, args in agent_reply._context_calls():
        _schema_checked_call(name, args)
    dates = [a["date"] for n, a in agent_reply._context_calls() if n == "day_metrics"]
    import datetime as dt
    assert dates[0] == dt.date.today().isoformat()   # сон прошлой ночи лежит на сегодня


def test_absence_correction_reaches_outbox_and_fault(model, caplog):
    # Real judge, synthetic canonical row; tool context deliberately omits this lab.
    model.reply["text"] = "ТТГ — нет данных."
    assert agent_reply.reply_to_letter(LETTER)
    body = _out(model.box)[0]["body"]
    assert body.startswith(model.reply["text"] + "\n\n")
    assert "TSH" in body and "2026-01-01" in body
    diagnostic = "agent_reply: absence claims contradicted; hits=1"
    assert model.faults == [diagnostic]
    assert [r.getMessage() for r in caplog.records if r.name == "agent_reply"] == [diagnostic]


def test_absence_clean_reply_unchanged(model):
    assert agent_reply.reply_to_letter(LETTER)
    assert _out(model.box)[0]["body"] == model.reply["text"]
    assert model.faults == []


@pytest.mark.parametrize("failure", ["canon", "judge"])
def test_absence_failure_keeps_delivery(model, monkeypatch, caplog, failure):
    def broken(*a, **kw):
        raise RuntimeError("Fictional Person private payload")
    if failure == "canon":
        monkeypatch.setattr(health_db, "get_recent_labs", broken)
    else:
        monkeypatch.setattr(gp_context, "judge_absence_claims", broken)
    model.reply["text"] = "ТТГ — нет данных."
    assert agent_reply.reply_to_letter(LETTER)
    assert _out(model.box)[0]["body"] == model.reply["text"]
    assert model.faults == [] and "private payload" not in caplog.text
    if failure == "judge":
        assert "absence judge failed (RuntimeError)" in caplog.text


def test_absence_correction_redacted_with_visible_names(model, monkeypatch):
    # Synthetic personal dictionary, real send/redact: allowed name survives, other PII does not.
    dictionary = model.box.parent / "private" / "pii_terms.yaml"
    dictionary.write_text('literals:\n  identity: ["ExampleGiven", "FictionalSurname", "1980-02-03", "ID-EXAMPLE-42"]\npatterns: {}\n')
    _cfg("agent_mail.name_visible", "ExampleGiven")
    note = "Уточнение: ExampleGiven FictionalSurname 1980-02-03 ID-EXAMPLE-42"
    monkeypatch.setattr(gp_context, "judge_absence_claims", lambda text: ([{"clause": note}], note))
    assert agent_reply.reply_to_letter(LETTER)
    assert _out(model.box)[0]["body"] == (
        model.reply["text"] + "\n\nУточнение: ExampleGiven [скрыто] [скрыто] [скрыто]")
    assert model.faults == ["agent_reply: absence claims contradicted; hits=1"]

@pytest.fixture
def facts(monkeypatch):
    """Handmade in-memory store; no patient DB, credentials, models or transports."""
    import sqlite3
    from contextlib import contextmanager
    import config_db
    import hai_chat
    import i18n
    import memory_facts_db
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('''CREATE TABLE tasks (
        id INTEGER PRIMARY KEY, source TEXT, source_date TEXT, type TEXT, priority TEXT,
        content TEXT, reason TEXT, fingerprint TEXT, deadline TEXT, resolution_type TEXT,
        status TEXT DEFAULT 'open', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        resolved_at TEXT, resolved_text TEXT, sent_at TEXT, tg_message_id INTEGER)''')
    @contextmanager
    def connection(*a, **kw):
        with conn:
            yield conn
    monkeypatch.setattr(health_db, 'get_conn', connection)
    monkeypatch.setattr(agent_reply, 'allowed_peers', lambda: {'assistant-example'})
    conf = {}
    monkeypatch.setattr(config_db, 'get_config', lambda k, default=None: conf.get(k, default))
    monkeypatch.setattr(i18n, 'lang_of', lambda profile=None: 'ru')
    monkeypatch.setattr(mcp_tools, 'redact', lambda s: s.replace('Synthetic Identity', '[hidden]'))
    payload = {'observations': ['Записан на осмотр 2040-02-03']}
    inputs = []
    def extract(text, **kw):
        inputs.append((text, kw))
        return payload
    monkeypatch.setattr(hai_chat, 'extract_artifacts', extract)
    writes = []
    monkeypatch.setattr(memory_facts_db, 'save_fact', lambda *a, **kw: writes.append((a, kw)))
    monkeypatch.setattr(memory_facts_db, 'derive_temporal_class', lambda text: 'standing')
    monkeypatch.setattr(health_db, 'save_memory', lambda *a, **kw: pytest.fail('legacy memory touched'))
    yield SimpleNamespace(conn=conn, payload=payload, writes=writes, conf=conf, inputs=inputs)
    conn.close()


@pytest.mark.parametrize('uncertain', ['да, но дата другая', 'да?', 'не уверен'])
def test_assistant_fact_waits_for_owner_and_deduplicates_after_answer(facts, uncertain):
    import task_agent
    import tasks_db
    ids = agent_reply.queue_fact_questions(LETTER)
    assert len(ids) == 1
    task = tasks_db.get_questions_needing_delivery()[0]
    assert task['type'] == 'question'
    assert task['content'] == 'Ассистент сообщил: Записан на осмотр 2040-02-03. Верно? Ответь «да» или «нет».'
    assert agent_reply.queue_fact_questions(LETTER) == []
    assert facts.writes == []
    assert not task_agent.record_answer(ids[0], uncertain)
    assert facts.writes == []
    assert task_agent.record_answer(ids[0], 'да')
    assert len(facts.writes) == 1
    args, kw = facts.writes[0]
    assert '2040-02-03' in args[1] and args[1].endswith('→ да')
    assert kw['key'] == task['fingerprint'] and kw['source'] == f'task:{ids[0]}'
    assert agent_reply.queue_fact_questions(LETTER) == []
    assert not task_agent.record_answer(ids[0], 'да')
    assert len(facts.writes) == 1


@pytest.mark.parametrize('answer', ['нет', 'No!'])
def test_assistant_fact_rejection_never_writes_memory(facts, answer):
    import task_agent
    tid, = agent_reply.queue_fact_questions(LETTER)
    assert task_agent.record_answer(tid, answer)
    assert facts.writes == []
    assert facts.conn.execute('SELECT status FROM tasks').fetchone()[0] == 'dismissed'
    assert agent_reply.queue_fact_questions(LETTER) == []


def test_assistant_fact_empty_foreign_and_redaction(facts):
    assert agent_reply.queue_fact_questions({**LETTER, 'peer': 'stranger'}) == []
    assert facts.inputs == []
    facts.payload.clear()
    assert agent_reply.queue_fact_questions(LETTER) == []
    facts.payload.update(observations=['Synthetic Identity записан на осмотр'])
    assert agent_reply.queue_fact_questions({**LETTER, 'wrapped': 'Synthetic Identity'})
    assert facts.inputs[-1] == ('[hidden]', {'reported': True})
    assert 'Synthetic Identity' not in facts.conn.execute('SELECT content FROM tasks').fetchone()[0]
    assert facts.writes == []


def test_assistant_fact_limit_counts_closed_questions_and_distinct_letters(facts):
    import task_agent
    facts.conf['agent_mail.max_fact_questions_per_letter'] = 1
    facts.payload['observations'] *= 2
    tid, = agent_reply.queue_fact_questions(LETTER)
    task_agent.record_answer(tid, 'нет')
    facts.payload['observations'] = ['Изменил режим прогулок']
    assert agent_reply.queue_fact_questions(LETTER) == []
    assert len(agent_reply.queue_fact_questions({**LETTER, 'message_id': '<other@example>'})) == 1
    assert facts.conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 2


@pytest.mark.parametrize('value', [True, -1, 0.5, '3'])
def test_assistant_fact_invalid_limit_fails_before_extraction(facts, value):
    facts.conf['agent_mail.max_fact_questions_per_letter'] = value
    with pytest.raises(ValueError):
        agent_reply.queue_fact_questions(LETTER)
    assert facts.inputs == []


def test_assistant_fact_invalid_payload_writes_nothing(facts):
    facts.payload['taking'] = [{'name': 'synthetic', 'dose': {'unexpected': 'object'}}]
    with pytest.raises(ValueError):
        agent_reply.queue_fact_questions(LETTER)
    assert facts.conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0


def test_shared_extractor_is_read_only(facts, monkeypatch):
    import hai_chat
    # Restore the real shared function; only its external model dependency is fake.
    import importlib
    hai_chat = importlib.reload(hai_chat)
    calls = []
    def create(**kw):
        calls.append(kw)
        return SimpleNamespace(stop_reason='end_turn', content=[SimpleNamespace(type='text', text='{"observations": ["осмотр 2040-02-03"]}')])
    monkeypatch.setattr(hai_chat, 'get_client', lambda: SimpleNamespace(messages=SimpleNamespace(create=create)))
    monkeypatch.setattr(hai_core, 'get_model', lambda role: 'synthetic-model')
    assert hai_chat.extract_artifacts('осмотр 2040-02-03', reported=True)['observations']
    assert calls[0]['task'] == 'hai_chat.run_arbiter'
    assert calls[0]['system'] == hai_chat.ARBITER_SYSTEM
    assert facts.writes == []
    assert facts.conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0


def test_assistant_question_delivery_has_reply_address(facts):
    import asyncio
    import logging
    import i18n
    import tasks_db
    tid, = agent_reply.queue_fact_questions(LETTER)
    sent = []
    async def send(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(message_id=456)
    env = _mail._load_functions('jobs/scheduled.py', ['deliver_unsent_question_tasks'],
        {'asyncio': asyncio, 'db': health_db, 'i18n': i18n, 'log': logging.getLogger(__name__),
         'get_chat_id': lambda: 123, '_questions_budget_left': lambda: 3})
    app = SimpleNamespace(bot=SimpleNamespace(send_message=send))
    asyncio.run(env['deliver_unsent_question_tasks'](app))
    assert len(sent) == 1 and sent[0]['reply_markup'].force_reply
    assert tasks_db.get_task_by_tg_message(456)['id'] == tid
    assert tasks_db.get_questions_needing_delivery() == []
    assert facts.writes == []


def test_reply_prompt_marks_mail_claims_unconfirmed(model):
    agent_reply.reply_to_letter(LETTER)
    assert 'Утверждения ассистента о владельце не подтверждены человеком' in model.calls[0]['system']


def test_assistant_fact_structured_claims_use_same_question_gate(facts):
    facts.payload.clear()
    facts.payload.update(profile_updates={'exercise_frequency': 'прогулки ежедневно'},
                         taking=[{'name': 'Средство Q', 'kind': 'supplement', 'dose': 'доза из письма', 'since': '2040-01'}],
                         experiments=[{'name': 'Прогулки', 'action': 'start', 'note': 'вечером'}])
    assert len(agent_reply.queue_fact_questions(LETTER)) == 3
    texts = [r[0] for r in facts.conn.execute('SELECT content FROM tasks ORDER BY id')]
    assert 'exercise_frequency: прогулки ежедневно' in texts[0]
    assert 'Принимает Средство Q; доза: доза из письма' in texts[1]
    assert 'Начал эксперимент «Прогулки»: вечером' in texts[2]
    assert facts.writes == []


@pytest.mark.parametrize('field', ['profile_updates', 'observations', 'taking', 'experiments'])
def test_assistant_fact_null_payload_is_not_successful_empty(facts, field):
    facts.payload[field] = None
    with pytest.raises(ValueError):
        agent_reply.queue_fact_questions(LETTER)
    assert facts.conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
