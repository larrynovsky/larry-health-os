"""Характеризация night_cycle — маршрутизация падений в парковку/гашение и пульс.
night_investigator.investigate замокан (маршрут проверяем детерминированно, без
LLM); стор, integrity и heartbeat уведены в tmp."""
import json

import pytest

import night_cycle as nc
import night_investigator
import parked_decisions as pd
from _time_inject import set_test_clock, clear_test_clock


@pytest.fixture
def env(tmp_path, monkeypatch):
    integ = tmp_path / "integrity_latest.json"
    integ.write_text(json.dumps({"failures": [
        "PGS reference-БД нечитаема: database is locked",
        "pytest: test_clone_is_skipped устарел",
    ], "code_failures": ["канон: строка ждёт вердикт владельца"]}), encoding="utf-8")
    monkeypatch.setenv("HEALTH_INTEGRITY_LATEST", str(integ))
    monkeypatch.setenv("HEALTH_PARKED_DB", str(tmp_path / "parked.json"))
    monkeypatch.setenv("HEALTH_NIGHT_CYCLE_RECEIPT", str(tmp_path / "hb.json"))
    # Третий источник цикла (нити без движения, BL-STALLED-THREADS-1) уводится в пустой
    # указатель. Иначе эти тесты читали бы НАСТОЯЩИЙ docs/handoff и настоящую историю git,
    # и их вердикт менялся бы от того, сколько нитей стоит в проекте сегодня (§20).
    monkeypatch.setenv("HEALTH_HANDOFF_INDEX", str(tmp_path / "нет-указателя.md"))
    # Четвёртый источник (возврат красного потока) уводится в пустой список отчётов.
    # Иначе run() читал бы НАСТОЯЩУЮ таблицу agent_reports, и две красные ночи в проде
    # завели бы здесь лишнюю карточку — вердикт теста менялся бы от состояния базы (§20).
    monkeypatch.setattr(__import__("agent_reports_db"), "get_agent_report",
                        lambda name, date_str=None, n=1: [])
    # Свежесть входа красной серии судится по ЖИВОМУ плисту машины (с 23.09): на Studio
    # плист есть, на MacBook нет — без явного шва вердикт зависел бы от машины (§20).
    # Предмет свежести — tests/unit/test_nightly_suite_liveness.py, здесь вход свеж.
    monkeypatch.setattr(__import__("morning_test_summary"), "summary_covers_last_run",
                        lambda *a, **k: True)
    set_test_clock("2026-08-02T09:00")

    def fake_investigate(failure):
        s = failure["summary"]
        if "PGS" in s:
            return {"class": "transient", "diagnosis": "лок снят"}
        if "pytest" in s:
            return {"class": "dev_fix", "diagnosis": "тест привязан к каталогу"}
        return {"class": "owner_decision", "diagnosis": "принадлежность канону",
                "park_reason": "домен владельца", "owner_ask": {
                    "subject": "Учебная работа стоит.", "question": "Продолжить учебную работу?", "options": [
                        {"label": "Продолжить", "cost": "нужен час"},
                        {"label": "Отложить", "cost": "результата пока не будет"}]}}
    monkeypatch.setattr(night_investigator, "investigate", fake_investigate)
    try:
        yield tmp_path
    finally:
        clear_test_clock()


def test_routes_transient_devfix_owner(env):
    s = nc.run()
    assert s == {"ran_at": s["ran_at"], "seen": 3, "parked": 2, "suppressed": 1,
                 "warn_seen": 0, "warn_parked": 0, "warn_standing": 0, "warn_unready": 0,
                 "defaults_applied": 0, "defaults_dropped": 0, "off_desk_retired": None,
                 # Третий и четвёртый источники (нити без движения; возврат красного) в
                 # сводке ЕСТЬ и при пустом входе: нули отличают «производитель отработал
                 # вхолостую» от «его не позвали».
                 "stalled_seen": 0, "stalled_new": 0, "stalled_mailed": None, "stalled_retired": 0,
                 "red_nights": 0, "red_returned": False}
    kinds = {g["id"]: g["kind"] for g in pd.list_open()}
    assert "dev_fix" in kinds.values() and "owner_decision" in kinds.values()
    assert len(kinds) == 2, "транзиент не паркуется"


def test_idempotent_no_duplicates(env):
    nc.run()
    nc.run()
    assert len(pd.list_open()) == 2, "повторный проход не плодит дублей"


def test_heartbeat_written(env):
    nc.run()
    hb = json.loads((env / "hb.json").read_text(encoding="utf-8"))
    assert hb["seen"] == 3 and hb["parked"] == 2 and "ran_at" in hb


def test_missing_integrity_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_INTEGRITY_LATEST", str(tmp_path / "nope.json"))
    monkeypatch.setenv("HEALTH_PARKED_DB", str(tmp_path / "p.json"))
    monkeypatch.setenv("HEALTH_NIGHT_CYCLE_RECEIPT", str(tmp_path / "h.json"))
    set_test_clock("2026-08-02T09:00")
    try:
        assert nc.run()["seen"] == 0
    finally:
        clear_test_clock()
