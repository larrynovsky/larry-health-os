"""План доделок п.4 (07.11.2026): задача «сдать анализ» закрывается результатом, не словом.

Отрицательные случаи — из независимого ревью 07.11 (находки 1–4). Синтетика, даты 2040 года.
"""
from __future__ import annotations

from datetime import date

import pytest

import agent_reply
import gp_context
import health_db
import labs_db
import task_agent
import task_route

TODAY = date(2040, 1, 20)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "t.db")
    health_db.init_db()
    monkeypatch.setattr(task_agent, "get_today", lambda: TODAY)
    monkeypatch.setattr(labs_db, "get_recent_labs", lambda *a, **k: [])   # annotate_lab_recency
    latest = {}
    monkeypatch.setattr(labs_db, "latest_result_by_specimen", lambda: latest)
    import config_db
    conf = {"schedule.active_tz": "Asia/Tokyo"}
    monkeypatch.setattr(config_db, "get_config", lambda k, d=None, **kw: conf.get(k, d))
    latest_conf.clear(); latest_conf.update(conf=conf)
    return latest


latest_conf: dict = {}


def _task(content, created="2040-01-10 08:00:00", fingerprint="auto", status="open"):
    import lab_canon
    if fingerprint == "auto":   # ключ отчёта, как его ставит экстрактор GP
        fingerprint = "lab:" + ",".join(sorted(lab_canon.analytes_mentioned(gp_context.strip_lab_recency(content))))
    tid = health_db.save_task("gp", "lab_test", content, fingerprint=fingerprint)
    with health_db.get_conn() as c:
        c.execute("UPDATE tasks SET created_at=?, status=? WHERE id=?", (created, status, tid))
    return tid


def _status(tid):
    return health_db.get_task(tid)["status"]


def test_all_results_after_creation_close(env):
    tid = _task("Сдать кровь: ферритин и CRP")
    env.update(Ferritin={"blood": "2040-01-12"}, CRP={None: "2040-01-12"})
    assert task_agent.close_lab_tasks_by_results() == [tid] and _status(tid) == "completed"


def test_same_day_result_does_not_close(env):
    """Находка 1: забор утром, задача вечером того же дня — результат не ответ на задачу."""
    tid = _task("Сдать ферритин", created="2040-01-10 18:00:00")
    env.update(Ferritin={"blood": "2040-01-10"})
    assert task_agent.close_lab_tasks_by_results() == [] and _status(tid) == "open"


def test_future_dated_result_does_not_close(env):
    tid = _task("Сдать ферритин")
    env.update(Ferritin={"blood": "2099-01-01"})
    assert task_agent.close_lab_tasks_by_results() == []


def test_two_specimens_in_history_do_not_close(env):
    """Находка 2: глюкоза мочи — не результат глюкозы крови; материал по тексту не установить."""
    tid = _task("Сдать глюкозу")
    env.update(Glucose={"blood": "2039-06-01", "urine": "2040-01-12"})
    assert task_agent.close_lab_tasks_by_results() == [] and _status(tid) == "open"


def test_unrecognized_panel_in_fingerprint_does_not_close(env):
    """Находка 3: «ОАК и ферритин» с ключом lab:CBC,Ferritin — один ферритин задачу не закрывает."""
    tid = _task("Сдать ОАК и ферритин", fingerprint="lab:CBC,Ferritin")
    env.update(Ferritin={"blood": "2040-01-12"})
    assert task_agent.close_lab_tasks_by_results() == [] and _status(tid) == "open"
    env.update(CBC={None: "2040-01-12"})       # строка под сырым именем — не делает панель распознанной
    assert task_agent.close_lab_tasks_by_results() == [] and _status(tid) == "open"


def test_partial_results_keep_open(env):
    tid = _task("Сдать кровь: ферритин и CRP")
    env.update(Ferritin={"blood": "2040-01-12"}, CRP={"blood": "2039-12-01"})
    assert task_agent.close_lab_tasks_by_results() == [] and _status(tid) == "open"


def test_recency_tail_is_not_an_ask(env):
    assert gp_context.strip_lab_recency("Сдать CRP\n(последний раз сдано: Ferritin — 01.01.2040)") == "Сдать CRP"
    tid = _task("Сдать CRP\n(последний раз сдано: Ferritin — 01.01.2040)")
    env.update(CRP={"blood": "2040-01-12"})
    assert task_agent.close_lab_tasks_by_results() == [tid]


def test_assistant_word_does_not_close_lab_but_dismiss_does(env, monkeypatch):
    monkeypatch.setattr(agent_reply, "allowed_peers", lambda: {"assistant-example"})
    lab = _task("Сдать ферритин")
    deed = health_db.save_task("gp", "action", "Записаться к врачу")
    letter = {"peer": "assistant-example", "wrapped": f"задача #{lab}: сделано\nзадача #{deed}: сделано"}
    assert task_route.close_from_letter(letter) == [deed] and _status(lab) == "open"
    letter = {"peer": "assistant-example", "wrapped": f"задача #{lab}: не нужно"}
    assert task_route.close_from_letter(letter) == [lab] and _status(lab) == "dismissed"


def test_task_without_creation_date_closable_by_word(env):
    """Находка 4: без даты создания результатом не закрыть — значит, слово ассистента может."""
    task = {"type": "lab_test", "content": "Сдать ферритин", "fingerprint": "lab:Ferritin"}
    assert task_agent.lab_task_closable_by_result({**task, "created_at": "2040-01-10 08:00:00"})
    assert not task_agent.lab_task_closable_by_result({**task, "created_at": None})


def test_bad_zone_value_is_loud_not_crash(env, monkeypatch):
    """Ревью №3: число или объект в schedule.active_tz — не падение, а громкий сбой и ожидание."""
    import notify
    faults = []
    monkeypatch.setattr(notify, "fault", lambda m, **k: faults.append(m))
    for bad in (123, {"x": 1}, "Not/AZone"):
        latest_conf["conf"]["schedule.active_tz"] = bad
        assert task_agent._tenant_zone() is None
    assert faults   # хотя бы один громкий сигнал (дальше — дроссель раз в сутки)


def test_without_report_key_not_auto_closed(env):
    """Повторное ревью, находка 2: без ключа lab:… полноту просьбы не установить."""
    tid = _task("Сдать ОАК и ферритин", fingerprint=None)
    env.update(Ferritin={"blood": "2040-01-12"})
    assert task_agent.close_lab_tasks_by_results() == [] and _status(tid) == "open"


def test_creation_date_in_tenant_zone(env):
    """Повторное ревью, находка 1: 22:30 UTC 10-го — 11-е в Токио; результат 11-го не после задачи."""
    tid = _task("Сдать ферритин", created="2040-01-10 22:30:00")
    env.update(Ferritin={"blood": "2040-01-11"})
    assert task_agent.close_lab_tasks_by_results() == [] and _status(tid) == "open"
    latest_conf["conf"]["schedule.active_tz"] = None          # пояс неизвестен — не закрывать вовсе
    env.update(Ferritin={"blood": "2040-01-15"})
    assert task_agent.close_lab_tasks_by_results() == []


def test_snoozed_task_closed_by_result(env):
    """Повторное ревью, находка 5: отложенная задача тоже снимается результатом."""
    tid = _task("Сдать ферритин", status="snoozed")
    env.update(Ferritin={"blood": "2040-01-12"})
    assert task_agent.close_lab_tasks_by_results() == [tid]


def test_tenant_zone_not_process_zone(env):
    """Пояс — тенанта, не процесса: 23:30 UTC 10-го в Нью-Йорке ещё 10-е, результат 11-го — после задачи."""
    latest_conf["conf"]["schedule.active_tz"] = "America/New_York"
    tid = _task("Сдать ферритин", created="2040-01-10 23:30:00")
    env.update(Ferritin={"blood": "2040-01-11"})
    assert task_agent.close_lab_tasks_by_results() == [tid]
