"""Месячный genome-job (audit 2026-06-17, C5).

Покрываем механику (Check vs Test): отправку уведомления, регистрацию job,
single-primary guard записи. Правильность переклассификаций ClinVar и качество
нарратива — НЕ тестируем (нет оракула, клиническое суждение).

2026-06-27: Block B+C (constitution regen + hypothesis task) + Block D1 (PRS check).
"""
from __future__ import annotations

import json

import asyncio
import socket
import sqlite3
import subprocess
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import genome_update_agent as gua
import genome_weights as gw
import jobs.scheduled as sched
import prs_pipeline as pp

import infra_config as _ic
_PRIMARY = _ic.PRIMARY_HOST  # основная машина установки, не литерал владельца


def _run(coro):
    return asyncio.run(coro)


def _ctx_with_bot():
    bot = SimpleNamespace(send_message=AsyncMock())
    return SimpleNamespace(bot=bot)


def _mock_subprocess_ok(monkeypatch):
    """Мок subprocess.run → returncode=0 (блокирует реальный запуск скрипта)."""
    monkeypatch.setattr(
        subprocess, "run",
        MagicMock(return_value=SimpleNamespace(returncode=0, stdout="ok", stderr="")),
    )


def _make_conn_ctx(execute_side_effects):
    """Conn-mock с последовательным side_effect для execute()."""
    fake_conn = MagicMock()
    fake_conn.execute.side_effect = execute_side_effects
    fake_cm = MagicMock()
    fake_cm.__enter__ = MagicMock(return_value=fake_conn)
    fake_cm.__exit__ = MagicMock(return_value=False)
    return fake_cm


@pytest.mark.unit
def test_significant_changes_send_notification(monkeypatch):
    """significant>0 + narrative → ≥2 send_message + send_long + mark_sent (задачи человеку нет, 28.09).

    Изменено 2026-06-27: Block B+C добавляют 2-е send_message ("запускаю рег."),
    поэтому assert_awaited_once() → await_count >= 2. Subprocess мокируем.
    """
    monkeypatch.setattr(sched, "get_chat_id", lambda: 42)
    monkeypatch.setattr(gua, "run_monthly_update", lambda: {
        "changed": 3, "significant": 2, "narrative": "GP: текст", "log_id": 7,
    })
    send_long = AsyncMock()
    monkeypatch.setattr(sched, "send_long", send_long)
    fake_db = MagicMock()
    monkeypatch.setattr(sched, "db", fake_db)
    _mock_subprocess_ok(monkeypatch)
    ctx = _ctx_with_bot()

    _run(sched.send_genome_update_monthly(ctx))

    assert ctx.bot.send_message.await_count >= 2, "Ожидалось ≥2 send_message (ClinVar + рег.)"
    send_long.assert_awaited_once()
    fake_db.mark_genome_log_sent.assert_called_once_with(7)
    fake_db.save_task.assert_not_called()  # Block C снят 28.09: работа системы — не в список человека


@pytest.mark.unit
def test_no_significant_changes_sends_nothing(monkeypatch):
    """significant==0 → ничего не отправлено, лог info, mark_* не вызван."""
    monkeypatch.setattr(sched, "get_chat_id", lambda: 42)
    monkeypatch.setattr(gua, "run_monthly_update", lambda: {
        "changed": 1, "significant": 0, "narrative": None, "log_id": 5,
    })
    send_long = AsyncMock()
    monkeypatch.setattr(sched, "send_long", send_long)
    fake_db = MagicMock()
    monkeypatch.setattr(sched, "db", fake_db)
    ctx = _ctx_with_bot()

    _run(sched.send_genome_update_monthly(ctx))

    ctx.bot.send_message.assert_not_awaited()
    send_long.assert_not_awaited()
    fake_db.mark_genome_log_sent.assert_not_called()


@pytest.mark.unit
def test_chat_id_none_warns_no_crash(monkeypatch, caplog):
    """significant>0, но chat_id None → warning, без отправки и без падения."""
    monkeypatch.setattr(sched, "get_chat_id", lambda: None)
    monkeypatch.setattr(gua, "run_monthly_update", lambda: {
        "changed": 2, "significant": 2, "narrative": "txt", "log_id": 9,
    })
    send_long = AsyncMock()
    monkeypatch.setattr(sched, "send_long", send_long)
    fake_db = MagicMock()
    monkeypatch.setattr(sched, "db", fake_db)
    ctx = _ctx_with_bot()

    import logging
    with caplog.at_level(logging.WARNING):
        _run(sched.send_genome_update_monthly(ctx))

    ctx.bot.send_message.assert_not_awaited()
    fake_db.mark_genome_log_sent.assert_not_called()
    assert "chat_id" in caplog.text


@pytest.mark.unit
def test_run_monthly_update_error_is_swallowed(monkeypatch):
    """Ошибка прогона → return без падения и без отправки (лог error)."""
    monkeypatch.setattr(sched, "get_chat_id", lambda: 42)
    def _boom():
        raise RuntimeError("clinvar down")
    monkeypatch.setattr(gua, "run_monthly_update", _boom)
    monkeypatch.setattr(sched, "send_long", AsyncMock())
    monkeypatch.setattr(sched, "db", MagicMock())
    ctx = _ctx_with_bot()

    _run(sched.send_genome_update_monthly(ctx))  # не должно бросить
    ctx.bot.send_message.assert_not_awaited()


@pytest.mark.unit
def test_register_adds_monthly_genome_job():
    """register() регистрирует genome_update_monthly как run_monthly, day=1.
    Ловит: пропажу задачи при будущих правках register()."""
    jq = MagicMock()
    app = SimpleNamespace(job_queue=jq)
    sched.register(app)

    monthly_calls = jq.run_monthly.call_args_list
    matched = [
        c for c in monthly_calls
        if c.kwargs.get("name") == "genome_update_monthly"
        and c.kwargs.get("day") == 1
        and c.args and c.args[0] is sched.send_genome_update_monthly
    ]
    assert matched, f"genome_update_monthly не зарегистрирована корректно: {monthly_calls}"


# ── Block B: constitution regen ─────────────────────────────────────────────

@pytest.mark.unit
def test_block_b_triggers_constitution_regen(monkeypatch):
    """significant>0 → subprocess.run(['python3', 'generate_constitutions.py']) вызван."""
    monkeypatch.setattr(sched, "get_chat_id", lambda: 42)
    monkeypatch.setattr(gua, "run_monthly_update", lambda: {
        "changed": 2, "significant": 1, "narrative": "txt", "log_id": 3,
    })
    monkeypatch.setattr(sched, "send_long", AsyncMock())
    monkeypatch.setattr(sched, "db", MagicMock())
    run_mock = MagicMock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(subprocess, "run", run_mock)

    _run(sched.send_genome_update_monthly(_ctx_with_bot()))

    run_mock.assert_called_once()
    cmd = run_mock.call_args.args[0]
    assert cmd == ["python3", "generate_constitutions.py"], f"Неверная команда: {cmd}"


@pytest.mark.unit
def test_block_b_regen_failure_sends_warning_no_crash(monkeypatch, fault_journal):
    """subprocess returncode≠0 → предупреждение в TG, job не падает."""
    monkeypatch.setattr(sched, "get_chat_id", lambda: 42)
    monkeypatch.setattr(gua, "run_monthly_update", lambda: {
        "changed": 1, "significant": 1, "narrative": "txt", "log_id": 1,
    })
    monkeypatch.setattr(sched, "send_long", AsyncMock())
    monkeypatch.setattr(sched, "db", MagicMock())
    monkeypatch.setattr(
        subprocess, "run",
        MagicMock(return_value=SimpleNamespace(returncode=1, stdout="", stderr="traceback"))
    )
    operator = MagicMock(return_value="telegram")
    monkeypatch.setattr(sched.notify, "notify_operator", operator)
    ctx = _ctx_with_bot()

    _run(sched.send_genome_update_monthly(ctx))  # не должно бросить

    calls_text = " ".join(str(c) for c in ctx.bot.send_message.call_args_list)
    operator.assert_not_called()
    records = [json.loads(line) for line in fault_journal.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["where"] == 'scheduled.send_genome_update_monthly'
    assert "code=1" in records[0]["text"], "regeneration failure must reach the journal"
    assert "code=1" not in calls_text and "ошибка" not in calls_text.lower()


# ── Block C: hypothesis review task ─────────────────────────────────────────

@pytest.mark.unit
def test_block_c_does_not_put_system_work_into_person_list(monkeypatch):
    """significant>0 → задача «пересмотреть гипотезы» человеку НЕ создаётся (решение владельца
    28.09: у неё не было исполнителя-человека; UC-B-04)."""
    monkeypatch.setattr(sched, "get_chat_id", lambda: 42)
    monkeypatch.setattr(gua, "run_monthly_update", lambda: {
        "changed": 5, "significant": 3, "narrative": "нарратив", "log_id": 11,
    })
    monkeypatch.setattr(sched, "send_long", AsyncMock())
    fake_db = MagicMock()
    monkeypatch.setattr(sched, "db", fake_db)
    _mock_subprocess_ok(monkeypatch)

    _run(sched.send_genome_update_monthly(_ctx_with_bot()))

    fake_db.save_task.assert_not_called()


@pytest.mark.unit
def test_block_c_not_called_when_no_significant(monkeypatch):
    """significant==0 → save_task НЕ вызван."""
    monkeypatch.setattr(sched, "get_chat_id", lambda: 42)
    monkeypatch.setattr(gua, "run_monthly_update", lambda: {
        "changed": 0, "significant": 0, "narrative": None, "log_id": None,
    })
    monkeypatch.setattr(sched, "send_long", AsyncMock())
    fake_db = MagicMock()
    monkeypatch.setattr(sched, "db", fake_db)

    _run(sched.send_genome_update_monthly(_ctx_with_bot()))

    fake_db.save_task.assert_not_called()


# ── Block D1: check_prs_updates_monthly ──────────────────────────────────────

@pytest.mark.unit
def test_prs_updates_empty_catalog_is_quiet(monkeypatch):
    """pgs_catalog пуст → download_and_import не вызван, уведомлений нет."""
    monkeypatch.setattr(sched, "get_chat_id", lambda: 42)

    catalog_r = MagicMock()
    catalog_r.fetchall.return_value = []
    fake_db = MagicMock()
    fake_db.get_conn.return_value = _make_conn_ctx([catalog_r])
    monkeypatch.setattr(sched, "db", fake_db)

    dl_mock = MagicMock()
    monkeypatch.setattr(gw, "download_and_import", dl_mock)
    monkeypatch.setattr(pp, "run", MagicMock())

    ctx = _ctx_with_bot()
    _run(sched.check_prs_updates_monthly(ctx))

    ctx.bot.send_message.assert_not_awaited()
    dl_mock.assert_not_called()


@pytest.mark.unit
def test_prs_updates_no_new_weights_is_quiet(monkeypatch):
    """n_inserted=0 → Phase H не запущен, уведомлений нет."""
    monkeypatch.setattr(sched, "get_chat_id", lambda: 42)

    catalog_r = MagicMock()
    catalog_r.fetchall.return_value = [("PGS000334", "AD")]
    fake_db = MagicMock()
    fake_db.get_conn.return_value = _make_conn_ctx([catalog_r])
    monkeypatch.setattr(sched, "db", fake_db)

    monkeypatch.setattr(gw, "download_and_import", MagicMock(return_value={"n_inserted": 0}))
    run_mock = MagicMock()
    monkeypatch.setattr(pp, "run", run_mock)

    ctx = _ctx_with_bot()
    _run(sched.check_prs_updates_monthly(ctx))

    ctx.bot.send_message.assert_not_awaited()
    run_mock.assert_not_called()


@pytest.mark.unit
def test_prs_updates_new_weights_triggers_phase_h_and_tg(monkeypatch):
    """n_inserted>0 → TG уведомление + pp.run вызван + diff-сообщение отправлено."""
    monkeypatch.setattr(sched, "get_chat_id", lambda: 42)

    # Три execute()-вызова по порядку: каталог → before_scores → genome_import_id
    catalog_r = MagicMock()
    catalog_r.fetchall.return_value = [("PGS000334", "AD")]
    before_r = MagicMock()
    before_r.fetchall.return_value = [("PGS000334", 0.40)]
    gid_r = MagicMock()
    gid_r.fetchone.return_value = (7,)
    fake_db = MagicMock()
    fake_db.get_conn.return_value = _make_conn_ctx([catalog_r, before_r, gid_r])
    monkeypatch.setattr(sched, "db", fake_db)

    monkeypatch.setattr(gw, "download_and_import", MagicMock(return_value={"n_inserted": 5}))
    run_mock = MagicMock(return_value={
        "results": {
            "PGS000334": {"raw_score": 0.45, "trait_label": "AD", "coverage_pct": 85.0}
        },
        "failed": [],
    })
    monkeypatch.setattr(pp, "run", run_mock)

    ctx = _ctx_with_bot()
    _run(sched.check_prs_updates_monthly(ctx))

    run_mock.assert_called_once()
    assert ctx.bot.send_message.await_count >= 2, (
        "Ожидалось ≥2 TG-сообщения: 'обнаружены обновления' + diff"
    )


@pytest.mark.unit
def test_register_adds_prs_updates_monthly_job():
    """register() регистрирует prs_updates_monthly как run_monthly, day=1 (Block D1)."""
    jq = MagicMock()
    app = SimpleNamespace(job_queue=jq)
    sched.register(app)

    monthly_calls = jq.run_monthly.call_args_list
    matched = [
        c for c in monthly_calls
        if c.kwargs.get("name") == "prs_updates_monthly"
        and c.kwargs.get("day") == 1
        and c.args and c.args[0] is sched.check_prs_updates_monthly
    ]
    assert matched, f"prs_updates_monthly не зарегистрирована: {monthly_calls}"


@pytest.mark.consistency
def test_genome_write_blocked_on_nonprimary(monkeypatch, tmp_path):
    """Запись в genetic_variants идёт через guarded get_conn: на non-primary
    без override — read-only → write бросает. Ловит split-brain через iCloud."""
    import health_db as hdb
    db_path = tmp_path / "h.db"
    monkeypatch.setattr(hdb, "DB_PATH", db_path)

    # 1) как primary — создаём схему
    monkeypatch.setattr(socket, "gethostname", lambda: _PRIMARY)
    monkeypatch.delenv("ALLOW_WRITE_NONPRIMARY", raising=False)
    hdb.init_db()

    # 2) non-primary с явным HEALTH_DATA_DIR (escape hatch) — read-only,
    #    запись в genome-таблицу → OperationalError. Без env get_conn вообще
    #    raise'ит (kill silent fallback 59ce993) — это покрыто отдельным тестом
    #    в tests/consistency/test_single_primary_db_writes.py.
    monkeypatch.setattr(socket, "gethostname", lambda: "MacBook-Pro")
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    conn = hdb.get_conn()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO genetic_variants(rsid) VALUES ('rs_test')")
        conn.commit()
    conn.close()
