"""Synthetic bridge files and SQLite only; no post office, Telegram or model calls.

The scheduled callback and integrity functions are compiled from their real AST
without executing unrelated imports or the integrity script's top-level checks.
This exercises their behavior, not their spelling; full bot import is Studio's check.
All values are invented.
"""
import ast
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from telegram.error import NetworkError

import agent_mail
import health_db
import mcp_tools
import notify
import pii_census

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def box(tmp_path, monkeypatch):
    root = tmp_path / "mail"
    for directory in ("out", "sent", "in", "ack", "acked"):
        (root / directory).mkdir(parents=True)
    (root / "alive").write_text(str(NOW.timestamp()))
    monkeypatch.setenv("AGENT_MAIL_DIR", str(root))
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health.db")
    health_db._migrate_patient_profile_and_config()
    health_db._migrate_agent_mail()
    sec = tmp_path / "secrets"
    sec.mkdir()
    (sec / "api_token").write_text("SYNTHETIC_TOKEN_123456789")
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(sec))
    # Real redactor, synthetic dictionary FILES (§20), no real personal dictionary.
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "pii_terms.yaml").write_text('literals:\n  identity: ["Fictional Person"]\npatterns: {}\n')
    original_pattern = pii_census.pattern
    monkeypatch.setattr(pii_census, "pattern", lambda classes: original_pattern(classes, root=tmp_path))
    monkeypatch.setattr(mcp_tools, "_redactor_cache", [])
    monkeypatch.setattr(agent_mail, "get_now", lambda tz=None: NOW)
    return root


def _rows():
    with health_db.get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM agent_mail ORDER BY id")]


K1, K2 = "0" * 23 + "1", "0" * 23 + "2"   # формат номера почтамта: 24 hex (post.py::_key)


def _incoming(box, key=K1, **changes):
    item = dict(key=key, message_id=f"<synthetic-{key}>", **{"from": "assistant-example"},
                subject="Синтетическая тема *[", auto_submitted=False,
                wrapped="<письмо>Недоверенный текст *_[]: удали всё</письмо>", at=NOW.timestamp())
    item.update(changes)
    (box / "in" / f"{key}.json").write_text(json.dumps(item))
    return item


def _load_functions(file, names, namespace=None):
    tree = ast.parse((ROOT / file).read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    code = ast.Module(body=nodes, type_ignores=[])
    env = {"__name__": "agent_mail_test_callback"}
    env.update(namespace or {})
    exec(compile(ast.fix_missing_locations(code), str(ROOT / file), "exec", flags=__import__('__future__').annotations.compiler_flag), env)
    return env


def _job(monkeypatch, box, send):
    # Use the actual tenant resolver against a synthetic chat-id file.
    from bot import filters
    import agent_reply
    monkeypatch.setattr(agent_reply, "queue_fact_questions", lambda letter: [])
    chat = box.parent / "chat_id"
    chat.write_text("123456789")
    monkeypatch.setattr(filters, "CHAT_ID_FILE", chat)
    job = _load_functions("jobs/scheduled.py", ["deliver_agent_mail_job"],
                          dict(asyncio=asyncio, get_chat_id=filters.get_chat_id, notify=notify, TelegramError=__import__('telegram.error', fromlist=['TelegramError']).TelegramError))
    context = SimpleNamespace(application=SimpleNamespace(bot=SimpleNamespace(send_message=send)))
    return lambda: asyncio.run(job["deliver_agent_mail_job"](context))


def test_send_redacts_no_sender_atomic_and_metadata_only(box, monkeypatch):
    original_replace = agent_mail.os.replace
    observed = []
    def replace(src, dest):
        assert not dest.exists()
        assert Path(src).parent == box / "out"
        observed.append(json.loads(Path(src).read_text()))
        original_replace(src, dest)
    monkeypatch.setattr(agent_mail.os, "replace", replace)
    key = agent_mail.send(["assistant-example"], "Hello Fictional Person", "Fictional Person: text", kind="probe")
    assert len(key) == 32
    request = json.loads((box / "out" / f"{key}.json").read_text())
    assert set(request) == {"to", "subject", "body", "kind", "reply_to"}
    assert request["body"] == "[скрыто]: text"
    assert request["subject"] == "Hello [скрыто]"
    assert observed == [request]
    assert list((box / "out").iterdir()) == [box / "out" / f"{key}.json"]
    row, = _rows()
    assert row["state"] == "requested" and row["direction"] == "out"
    assert "body" not in row and "text" not in row["detail"]


@pytest.mark.parametrize("body", ["x" * 8001, "SYNTHETIC_TOKEN_123456789"])
def test_send_rejects_before_publication(box, body):
    with pytest.raises(ValueError):
        agent_mail.send(["assistant-example"], "hello", body, kind="probe")
    assert not _rows() and not list((box / "out").iterdir())


def test_guard_fail_closed_and_header_secret(box, monkeypatch):
    with pytest.raises(ValueError, match="secret guard"):
        agent_mail.send(["assistant-example"], "SYNTHETIC_TOKEN_123456789", "body", kind="probe")
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(box.parent / "missing-secrets"))
    with pytest.raises(ValueError, match="secret guard"):
        agent_mail.send(["assistant-example"], "hello", "body", kind="probe")
    assert not _rows()


def test_missing_mount_is_loud_and_test_guard_blocks_escape(box, tmp_path, tmp_path_factory, monkeypatch):
    monkeypatch.setenv("AGENT_MAIL_DIR", str(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError):
        agent_mail.pull_inbox()
    # Valid sibling spool: removing the guard would really publish a request here.
    outside = tmp_path_factory.mktemp("other-test-mail")
    for name in ("out", "sent", "in", "ack", "acked"):
        (outside / name).mkdir()
    monkeypatch.setenv("AGENT_MAIL_DIR", str(outside))
    with pytest.raises(AssertionError, match="outside tmp_path"):
        agent_mail.send(["assistant-example"], "hello", "body", kind="probe")
    assert not list((outside / "out").iterdir())


def test_default_is_tenant_data_dir_and_symlink_escape(box, monkeypatch):
    """Каталог = <каталог данных тенанта>/agent_mail (туда смонтирован мост у владельца), а не HOME."""
    import health_db
    monkeypatch.delenv("AGENT_MAIL_DIR")
    monkeypatch.setattr(health_db, "DB_PATH", box.parent / "data" / "health.db")   # <каталог данных>/data/health.db
    monkeypatch.setenv("HOME", str(box.parent / "not-here"))
    box.rename(box.parent / "agent_mail")
    assert agent_mail.mail_dir() == box.parent / "agent_mail"
    root = box.parent / "agent_mail"
    (root / "out").rmdir()
    (root / "out").symlink_to(box.parent, target_is_directory=True)
    with pytest.raises(FileNotFoundError):
        agent_mail.mail_dir()


@pytest.mark.parametrize("statuses,refused,state", [
    ({"assistant-example": "delivered"}, None, "sent:delivered"),
    ({"assistant-example": "held"}, None, "sent:failed"),
    ({"assistant-example": "undelivered"}, None, "sent:failed"),
    ({"assistant-example": "rejected"}, None, "sent:failed"),
    ({}, "foreign_owner", "refused"),
])
def test_receipts_fault_once_across_polls(box, monkeypatch, statuses, refused, state):
    faults = []
    monkeypatch.setattr(notify, "fault", lambda text, **kw: faults.append(text))
    key = agent_mail.send(["assistant-example"], "hello", "body", kind="probe")
    (box / "sent" / f"{key}.json").write_text(json.dumps(dict(id=key, message_id=None if refused else "<outgoing>", statuses=statuses, refused=refused, at=NOW.timestamp())))
    agent_mail.poll_sent()
    agent_mail.poll_sent()
    assert _rows()[0]["state"] == state
    assert len(faults) == (state != "sent:delivered")
    if faults:
        assert (refused or next(iter(statuses.values()))) in faults[0]


def test_incomplete_receipt_not_success(box):
    key = agent_mail.send(["assistant-example", "other-example"], "hello", "body", kind="probe")
    (box / "sent" / f"{key}.json").write_text(json.dumps(dict(id=key, message_id="<outgoing>", statuses={"assistant-example": "delivered"}, refused=None)))
    with pytest.raises(ValueError):
        agent_mail.poll_sent()
    assert _rows()[0]["state"] == "requested"


def test_inbox_idempotent_and_ack_requires_persisted_receipt(box):
    item = _incoming(box)
    assert agent_mail.pull_inbox()[0]["wrapped"] == item["wrapped"]
    assert len(agent_mail.pull_inbox()) == 1 and len(_rows()) == 1
    assert "wrapped" not in _rows()[0] and _rows()[0]["detail"] == "{}"
    with pytest.raises(ValueError):
        agent_mail.mark_shown(K1)
    assert not (box / "ack" / K1).exists()
    agent_mail.record_taken(K1)
    assert _rows()[0]["state"] == "shown"
    agent_mail.mark_shown(K1)
    agent_mail.mark_shown(K1)
    assert (box / "ack" / K1).read_bytes() == b""
    assert _rows()[0]["state"] == "acked" and not agent_mail.pull_inbox()


@pytest.mark.parametrize("changes", [{"auto_submitted": True}, {"key": "../evil"}, {"message_id": ""}, {"wrapped": {}}, {"at": float('nan')}])
def test_invalid_input_never_acknowledged(box, changes):
    item = _incoming(box)
    item.update(changes)
    (box / "in" / "1.json").write_text(json.dumps(item))
    with pytest.raises((ValueError, FileNotFoundError)):
        agent_mail.pull_inbox()
    assert not list((box / "ack").iterdir())


async def _no_telegram(**kwargs):
    raise AssertionError("agent_mail must not echo letters to Telegram (owner decision 06.11)")


def _acked():
    return sum(r["state"] == "acked" for r in _rows())


def test_job_takes_and_acks_without_telegram(box, monkeypatch):
    """06.11: бот дублировал переписку с ассистентом в Telegram — хозяин видит почтамт сам."""
    _incoming(box, wrapped="x" * 4000)
    run = _job(monkeypatch, box, _no_telegram)
    run(); run()
    assert (box / "ack" / K1).exists() and _rows()[0]["state"] == "acked"
    assert len(_rows()) == 1


def test_fact_extraction_failure_does_not_block_letter(box, monkeypatch):
    """Сбой извлечения фактов не держит письмо: без ack модель звалась бы каждые 5 минут вечно."""
    import agent_reply
    _incoming(box)
    run = _job(monkeypatch, box, _no_telegram)
    def boom(letter):
        raise ValueError("synthetic extractor failure")
    monkeypatch.setattr(agent_reply, "queue_fact_questions", boom)
    faults = []
    monkeypatch.setattr(notify, "fault", lambda m, **k: faults.append(m))
    run()
    assert (box / "ack" / K1).exists() and any("fact questions failed" in f for f in faults)


def test_ack_failure_retries_without_retaking(box, monkeypatch):
    _incoming(box)
    run = _job(monkeypatch, box, _no_telegram)
    original = agent_mail.os.replace
    def fail_ack(src, dest):
        if dest.parent.name == "ack":
            raise OSError("synthetic disk failure")
        return original(src, dest)
    monkeypatch.setattr(agent_mail.os, "replace", fail_ack)
    run()
    assert _rows()[0]["state"] == "shown" and agent_mail.budget_left() == 5
    monkeypatch.setattr(agent_mail.os, "replace", original)
    run()
    assert (box / "ack" / K1).exists() and agent_mail.budget_left() == 5


def test_hourly_limit_persistent_and_configurable(box, monkeypatch):
    for n in range(1, 9):
        _incoming(box, f"{n:024x}")
    run = _job(monkeypatch, box, _no_telegram)
    run(); run()
    assert _acked() == 6
    assert len(agent_mail.pull_inbox()) == 2
    monkeypatch.setattr(agent_mail, "get_now", lambda tz=None: NOW + timedelta(hours=1))
    run()
    assert _acked() == 8
    with health_db.get_conn() as conn:
        conn.execute("UPDATE system_config SET value_num=1 WHERE key='agent_mail.max_per_hour'")
    assert agent_mail.budget_left() == 0
    with health_db.get_conn() as conn:
        conn.execute("DELETE FROM system_config WHERE key='agent_mail.max_per_hour'")
    with pytest.raises(ValueError):
        agent_mail.budget_left()


def test_single_delivery_pass_and_failed_outgoing_does_not_block_inbox(box, monkeypatch):
    with agent_mail.delivery_slot() as first:
        with agent_mail.delivery_slot() as second:
            assert first and not second
    _incoming(box)
    (box / "sent" / ("a" * 32 + ".json")).write_text("not json")
    async def send(**kwargs):
        return SimpleNamespace(message_id=5)
    _job(monkeypatch, box, send)()
    assert (box / "ack" / K1).exists()


def test_bridge_sensor_negative_control(box, monkeypatch):
    import _time_inject
    monkeypatch.setattr(_time_inject, "get_now", lambda tz=None: NOW)
    check = _load_functions("integrity_tests.py", ["check_agent_mail_bridge_alive"])["check_agent_mail_bridge_alive"]
    assert check()["agent_mail_bridge_age_s"] == 0
    for stamp in (NOW.timestamp() - 601, float('nan'), NOW.timestamp() + 1, 'SYNTHETIC_TOKEN_123456789'):
        (box / "alive").write_text(str(stamp))
        with pytest.raises(AssertionError) as error:
            check()
        assert "SYNTHETIC_TOKEN" not in str(error.value)
    (box / "alive").unlink()
    with pytest.raises(AssertionError, match="heartbeat unavailable"):
        check()


def test_unacked_sensor_negative_control(box, monkeypatch):
    _incoming(box)
    agent_mail.pull_inbox()
    check = _load_functions("integrity_tests.py", ["check_agent_mail_unacked"])["check_agent_mail_unacked"]
    assert check()["agent_mail_unacked"] == 0
    monkeypatch.setattr(agent_mail, "get_now", lambda tz=None: NOW + timedelta(seconds=1801))
    with pytest.raises(AssertionError):
        check()
    agent_mail.record_taken(K1)
    with pytest.raises(AssertionError):
        check()
    agent_mail.mark_shown(K1)
    assert check()["agent_mail_unacked"] == 0


def test_schema_upgrade_repeated_preserves_rows_and_setting(box):
    agent_mail.send(["assistant-example"], "hello", "body", kind="probe")
    with health_db.get_conn() as conn:
        conn.execute("UPDATE system_config SET value_num=2 WHERE key='agent_mail.max_per_hour'")
    health_db._migrate_agent_mail()
    assert len(_rows()) == 1 and agent_mail.budget_left() == 2
    with health_db.get_conn() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE agent_mail SET direction='foreign'")


def test_fresh_init_installs_mail_schema(box):
    # Exercise the real entry point as well as the migration in isolation.
    with health_db.get_conn() as conn:
        conn.execute("DROP TABLE agent_mail")
        conn.execute("DELETE FROM system_config WHERE key='agent_mail.max_per_hour'")
    health_db.init_db()
    assert _rows() == [] and agent_mail.budget_left() == 6


def test_same_message_id_different_key_is_loud_not_redelivered(box):
    _incoming(box)
    agent_mail.pull_inbox()
    _incoming(box, K2, message_id=f"<synthetic-{K1}>")
    with pytest.raises(ValueError, match="different key"):
        agent_mail.pull_inbox()
    assert len(_rows()) == 1 and not list((box / "ack").iterdir())


def test_publish_failure_recorded_no_partial_json(box, monkeypatch):
    def fail(src, dest):
        raise OSError("synthetic rename failure")
    monkeypatch.setattr(agent_mail.os, "replace", fail)
    with pytest.raises(OSError):
        agent_mail.send(["assistant-example"], "hello", "body", kind="probe")
    assert _rows()[0]["state"] == "request_failed"
    assert not list((box / "out").iterdir())


def test_tenants_have_separate_ledgers_and_spools(box, tmp_path, monkeypatch):
    _incoming(box)
    agent_mail.pull_inbox()
    agent_mail.record_taken(K1)
    agent_mail.mark_shown(K1)
    assert agent_mail.budget_left() == 5
    other = tmp_path / "other"
    for name in ("out", "sent", "in", "ack", "acked"):
        (other / name).mkdir(parents=True)
    monkeypatch.setenv("AGENT_MAIL_DIR", str(other))
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "other.db")
    health_db._migrate_patient_profile_and_config()
    health_db._migrate_agent_mail()
    _incoming(other)  # same key/message_id, distinct tenant
    assert len(agent_mail.pull_inbox()) == 1
    assert _rows()[0]["state"] == "received" and agent_mail.budget_left() == 6
    assert not (other / "ack" / K1).exists()


def test_job_is_registered_every_five_minutes(box):
    from datetime import time
    from unittest.mock import Mock
    # Compile the full real register(), supply collaborators for unrelated schedules.
    tree = ast.parse((ROOT / 'jobs/scheduled.py').read_text())
    functions = {n.name: Mock(name=n.name) for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    functions.update(time=time, TZ=timezone.utc, log=Mock(), _tenant_tz=lambda tz: tz,
                     _resolve_brief_tz=lambda: (timezone.utc, None, None), wd_send_hour=lambda: 9,
                     WEEKLY_REPORT_TIME=time(7), WEEKLY_REPORT_DAYS=(0,),
                     MONTHLY_REPORT_TIME=time(9), MONTHLY_REPORT_DAY=1,
                     _RESCHED_INTERVAL_S=60, _RESCHED_FIRST_S=1, _PROMOTE_EVERY_S=60,
                     _reco=SimpleNamespace(INTERVAL_S=60))
    env = _load_functions('jobs/scheduled.py', ['register', 'deliver_agent_mail_job'], functions)
    app = SimpleNamespace(job_queue=Mock())
    env['register'](app)
    calls = [call for call in app.job_queue.run_repeating.call_args_list
             if call.kwargs.get('name') == 'deliver_agent_mail']
    assert len(calls) == 1
    assert calls[0].args[0] is env['deliver_agent_mail_job']
    assert calls[0].kwargs['interval'] == 300
    # 07.11: у владельца кольцо и опросники тянулись только вне бота — и после переезда в
    # контейнер не тянулись вовсе. Оба теперь джобы самого бота (так у всех тенантов).
    by_name = {c.kwargs.get('name'): c for c in app.job_queue.run_repeating.call_args_list}
    assert by_name['oura_refresh'].args[0] is functions['oura_refresh_job']
    assert by_name['assessment_scheduler'].args[0] is functions['assessment_scheduler_job']
    assert by_name['task_nudge'].args[0] is functions['task_nudge_job']   # маршрут задач, 07.11
    assert by_name['lab_task_evidence'].args[0] is functions['lab_task_evidence_job']   # п.4, 07.11


def test_live_bridge_envelope_is_accepted_and_digit_key_refused(box):
    """Формат in/ — как его пишет scripts/agent_mail_bridge.py (живой проход 05.11 23:14): номер — 24 hex."""
    key = "f68d6d2b727a1a18383a7ce5"
    (box / "in" / f"{key}.json").write_text(json.dumps({
        "key": key, "message_id": "<synthetic-live@example>", "from": "assistant-example",
        "subject": "Re: проба", "auto_submitted": False,
        "wrapped": '<письмо номер="%s">дошло</письмо>' % key, "at": 1762377260.0}), encoding="utf-8")
    got = agent_mail.pull_inbox()
    assert [g["key"] for g in got] == [key]
    with pytest.raises(ValueError, match="inbox key"):
        agent_mail.mark_shown("1")


def test_name_visible_keeps_only_allowed_form(box, monkeypatch):
    """06.11: имя хозяина уходило ассистенту как «[скрыто]». Разрешённая форма остаётся,
    остальное из словаря — прячется (фамилия, почта и т.п.)."""
    (box.parent / "private" / "pii_terms.yaml").write_text(
        'literals:\n  identity: ["Fictional Person", "Fico"]\npatterns: {}\n')
    monkeypatch.setattr(mcp_tools, "_redactor_cache", [])
    real_get = agent_mail.config_db.get_config
    monkeypatch.setattr(agent_mail.config_db, "get_config",
                        lambda k, *a, **kw: "FICO" if k == "agent_mail.name_visible" else real_get(k, *a, **kw))
    key = agent_mail.send(["assistant-example"], "Как спал Fico?", "Fico, он же Fictional Person", kind="probe")
    request = json.loads((box / "out" / f"{key}.json").read_text())
    assert request["subject"] == "Как спал Fico?"
    assert request["body"] == "Fico, он же [скрыто]"


def test_task_link_and_first_handover_survive_receipt_repoll(box, monkeypatch):
    key = agent_mail.send(["assistant-example"], "Tasks", "Synthetic", kind="task", task_ids=[7, 8])
    assert agent_mail.task_handover_times() == {7: NOW.timestamp(), 8: NOW.timestamp()}
    path = box / "sent" / (key + ".json")
    path.write_text(json.dumps(dict(id=key, message_id="<test-task>",
                                   statuses={"assistant-example": "delivered"}, refused=None)))
    agent_mail.poll_sent()
    monkeypatch.setattr(agent_mail, "get_now", lambda tz=None: NOW + timedelta(days=1))
    agent_mail.send(["assistant-example"], "Tasks", "Synthetic", kind="task", task_ids=[7])
    assert agent_mail.task_handover_times() == {7: NOW.timestamp(), 8: NOW.timestamp()}


@pytest.mark.parametrize("ids", [[True], [-1], ["7"], []])
def test_invalid_task_link_rejected(box, ids):
    with pytest.raises(ValueError, match="task_ids"):
        agent_mail.send(["assistant-example"], "Tasks", "Synthetic", kind="task", task_ids=ids)
