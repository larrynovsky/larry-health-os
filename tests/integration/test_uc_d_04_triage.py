"""
UC-D-04 — `triage_agent` чинит WARN, не лечит FAIL, идемпотентен по дню.

Источник: USE_CASES.md §4.D → UC-D-04.
Реализация: `triage_agent.py:48 run_triage`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def triage_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Изолированное окружение triage_agent:
    - tmp logs-директория;
    - перехват subprocess.Popen, notify, дома вопросов (_ask) и notify_operator.
    """
    import triage_agent as ta

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    # Подменяем SCRIPT_DIR (он используется для logs/, flag-файла)
    monkeypatch.setattr(ta, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(ta, "LOG_FILE", logs_dir / "triage.log")

    # Перехват Popen
    popen_calls: list[list[str]] = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            popen_calls.append(list(args))

        def communicate(self, *a, **kw):
            return (b"", b"")

    monkeypatch.setattr("subprocess.Popen", FakePopen)

    # Перехват TG
    tg_messages: list[str] = []
    monkeypatch.setattr(ta.notify, "notify", lambda msg, *a, **k: tg_messages.append(msg))
    # вопросы человеку уходят в дом вопросов (_ask), не в Telegram (28.09, triage-questions)
    asked: list = []
    monkeypatch.setattr(ta, "_ask", lambda qs, log=None: asked.extend(qs) or len(qs))
    operator_messages: list[str] = []
    monkeypatch.setattr(ta.notify, "notify_operator",
                        lambda msg: operator_messages.append(msg) or "telegram")

    faults = []
    monkeypatch.setattr(ta.notify, "fault", lambda *a, **kw: faults.append((a, kw)))

    return {
        "faults": faults,
        "ta": ta,
        "tmp": tmp_path,
        "logs": logs_dir,
        "popen": popen_calls,
        "tg": tg_messages,
        "asked": asked,
        "operator": operator_messages,
        "integrity_path": logs_dir / "integrity_latest.json",
    }


def _write_integrity(env, failures: list = None, warnings: list = None):
    import health_db
    payload = {
        "failures": failures or [],
        "warnings": warnings or [],
        "pass": 10, "fail": len(failures or []), "warn": len(warnings or []),
        # C-19: читатель (latest_verdict) принимает вердикт только о СВОЕЙ базе.
        "db_path": str(health_db.DB_PATH),
    }
    env["integrity_path"].write_text(json.dumps(payload), encoding="utf-8")


# ── B (negative) ────────────────────────────────────────────────────────────


def test_no_integrity_log_returns_empty(triage_env):
    """integrity_latest.json не существует → graceful no-op."""
    res = triage_env["ta"].run_triage(send_user_questions=True)
    assert res["auto_fixed"] == []
    assert res["needs_user"] == []
    assert triage_env["popen"] == []


def test_fail_does_not_trigger_autofix(triage_env):
    """FAIL в integrity не должен лечиться triage'ом — только WARN."""
    _write_integrity(triage_env,
                      failures=[["BD недоступна", "OperationalError"]])
    res = triage_env["ta"].run_triage(send_user_questions=True)
    # Никаких Popen для FAIL — FAIL блокирует отчёт через run_checks --scheduled
    assert triage_env["popen"] == [], \
        f"FAIL не должен запускать Popen, а Popen стартовал: {triage_env['popen']}"


# ── E (cross-check): автофиксы ──────────────────────────────────────────────


def test_warn_gp_report_triggers_popen(triage_env):
    """WARN «GP-отчёт отсутствует» → Popen(gp_agent.py weekly)."""
    _write_integrity(triage_env, failures=[
        ["GP-отчёт отсутствует или короткий", "0 chars"]
    ])
    triage_env["ta"].run_triage(send_user_questions=False)
    assert any("gp_agent" in " ".join(c) for c in triage_env["popen"]), \
        f"gp_agent.py weekly не запущен. Popen calls: {triage_env['popen']}"


def test_warn_genome_update_stays_in_artifact_not_autofixed(triage_env):
    """WARN «genome_update никогда» → артефакт ночного цикла, НЕ Popen.

    Контракт изменён 2026-06-29 (CLAUDE.md §13 Diagnose-don't-Repair): раньше
    triage спавнил genome_update_agent.py через Popen, но короткоживущий
    morning_report → launchd SIGKILL осиротевшего процесса → 16+ дней фейк-
    «вылечено» при пустой genome_update_log. Теперь staleness читает ночной цикл.
    """
    _write_integrity(triage_env,
                      warnings=[["genome_update_agent не запускался",
                                 "ни разу"]])
    res = triage_env["ta"].run_triage(send_user_questions=True)
    # НЕ должно быть Popen генома
    assert not any("genome_update_agent" in " ".join(c) for c in triage_env["popen"]), \
        f"genome не должен спавниться (Diagnose-don't-Repair): {triage_env['popen']}"
    assert res["auto_fixed"] == []
    assert res["needs_user"] == [] and triage_env["tg"] == []
    assert triage_env["operator"] == []
    assert triage_env["faults"] == [], "triage must not re-journal integrity findings"
    artifact = json.loads(triage_env["integrity_path"].read_text(encoding="utf-8"))
    assert "genome_update" in artifact["warnings"][0][0]


def test_warn_old_labs_asks_user(triage_env):
    """WARN про устаревшие анализы → вопрос в дом вопросов (с обратным адресом), не TG-строка.
    Чужой тенант ([health_partner] у владельца) человеку этой базы не задаётся."""
    _write_integrity(triage_env,
                      warnings=[["анализы требуют обновления: 200д", "последний: 2025-10-10"],
                                ["[health_partner] анализы устарели: 434д (порог 270д)", ""]])
    triage_env["ta"].run_triage(send_user_questions=True)
    assert triage_env["tg"] == [], "вопрос человеку больше не едет строкой в Telegram"
    assert [q["kind"] for q in triage_env["asked"]] == ["labs"], triage_env["asked"]
    assert "2025-10-10" in triage_env["asked"][0]["content"]


# ── B (idempotency) ─────────────────────────────────────────────────────────


def test_idempotent_per_day(triage_env):
    """Повторный вызов в тот же день — no-op (через flag).

    Через GP-провал: genome больше не спавнит Popen (см. выше), а GP — спавнит,
    поэтому идемпотентность Popen проверяем именно на нём.
    """
    _write_integrity(triage_env,
                      failures=[["GP-отчёт отсутствует или короткий", "0 chars"]])

    # Первый вызов
    triage_env["ta"].run_triage(send_user_questions=False)
    first_count = len(triage_env["popen"])
    assert first_count >= 1, "первый вызов должен был запустить Popen"

    # Второй вызов в тот же день — flag-файл блокирует
    triage_env["ta"].run_triage(send_user_questions=False)
    assert len(triage_env["popen"]) == first_count, \
        "второй вызов триггернул Popen — нет идемпотентности"


def test_idempotent_flag_filename_uses_today(triage_env):
    """Flag-файл именован по дате через get_today() — должен быть сегодня."""
    _write_integrity(triage_env, warnings=[["x", "y"]])
    triage_env["ta"].run_triage(send_user_questions=False)

    from _time_inject import get_today
    expected_flag = triage_env["logs"] / f"triage_done_{get_today()}.flag"
    assert expected_flag.exists(), \
        f"flag-файл {expected_flag.name} не создан"


def test_clock_inject_changes_flag_filename(triage_env, clock):
    """Замороженное время → flag-файл с замороженной датой."""
    clock.set("2030-01-15")
    _write_integrity(triage_env, warnings=[["x", "y"]])
    triage_env["ta"].run_triage(send_user_questions=False)

    flag = triage_env["logs"] / "triage_done_2030-01-15.flag"
    assert flag.exists(), "clock injection в triage_agent не работает"


def test_failure_stays_in_artifact_for_night_cycle(triage_env):
    """Доставка технического провала: ночной цикл читает исходный артефакт."""
    _write_integrity(triage_env, failures=[
        ["единственный канонический health.db", "split-brain risk"]
    ])
    res = triage_env["ta"].run_triage(send_user_questions=True)
    assert res["auto_fixed"] == [] and triage_env["popen"] == []
    assert res["needs_user"] == [] and triage_env["tg"] == []
    assert triage_env["operator"] == []
    assert triage_env["faults"] == [], "triage must not re-journal integrity findings"
    artifact = json.loads(triage_env["integrity_path"].read_text(encoding="utf-8"))
    assert "health.db" in artifact["failures"][0][0]


def test_gp_failure_not_duplicated_to_user(triage_env):
    """GP-провал авто-фиксится → НЕ дублируется в needs_user."""
    _write_integrity(triage_env, failures=[
        ["GP-отчёт отсутствует или короткий", "0 chars"]
    ])
    res = triage_env["ta"].run_triage(send_user_questions=True)
    assert not any("GP-отчёт" in q for q in res["needs_user"]), res["needs_user"]


def test_stale_artifact_with_require_today_sets_no_marker(triage_env, monkeypatch):
    """Гонка 07:50/08:00 (2026-09-01): триаж по плисту в 08:00 читал ВЧЕРАШНИЙ
    артефакт и ставил маркер дня — цепочка из run_checks в 08:27 становилась no-op,
    владелец получал вчерашние находки под сегодняшней датой. Боевой вход
    (require_today=True) на вчерашнем артефакте: ничего не шлёт, маркер НЕ ставит;
    затем сегодняшний артефакт доставляется."""
    import json
    from datetime import timedelta
    ta = triage_env["ta"]
    today = ta.get_today()
    _write_integrity(triage_env, warnings=[["вчерашняя находка", "x"]])
    payload = json.loads(triage_env["integrity_path"].read_text(encoding="utf-8"))
    payload["date"] = str(today - timedelta(days=1))
    triage_env["integrity_path"].write_text(json.dumps(payload), encoding="utf-8")

    res = ta.run_triage(send_user_questions=True, require_today=True)
    assert res["needs_user"] == [] and triage_env["tg"] == []
    assert triage_env["operator"] == []
    assert not (triage_env["tmp"] / "logs" / f"triage_done_{today}.flag").exists(), \
        "маркер поставлен на вчерашнем артефакте — цепочка потеряет день"

    payload["date"] = str(today)
    payload["warnings"] = [["сегодняшняя находка", "y"]]
    triage_env["integrity_path"].write_text(json.dumps(payload), encoding="utf-8")
    res = ta.run_triage(send_user_questions=True, require_today=True)
    assert res["needs_user"] == [] and triage_env["tg"] == []
    assert triage_env["operator"] == []
    assert triage_env["faults"] == [], "triage must not re-journal integrity findings"
    artifact = json.loads(triage_env["integrity_path"].read_text(encoding="utf-8"))
    assert "сегодняшняя" in artifact["warnings"][0][0]
    assert (triage_env["logs"] / f"triage_done_{today}.flag").exists()
