"""План доделок п.1 (07.11.2026): срок анализа — один дом, labs_db.tenant_lab_schedule;
срок задачи на анализ ставит расписание, а не модель."""
from datetime import date

import pytest

import labs_db
import task_agent

TODAY = date(2026, 11, 7)


@pytest.fixture
def due(monkeypatch):
    monkeypatch.setattr(task_agent, "get_today", lambda: TODAY)
    table = {}
    monkeypatch.setattr(labs_db, "lab_due", lambda names, day: {k: v for k, v in table.items() if k in names})
    return table


def task(content, deadline="2026-11-10", early=None):
    return {"type": "lab_test", "content": content, "deadline": deadline, "reason": "r", "early_reason": early}


def test_bundle_takes_earliest_planned_date(due):
    due["Ferritin"] = {"due": date(2026, 12, 20)}
    due["CRP"] = {"due": date(2026, 11, 27)}
    t = task_agent.deadline_from_schedule(task("Сдать кровь: ферритин и CRP"))
    assert t["deadline"] == "2026-11-27"


def test_overdue_is_today_not_past(due):
    due["CRP"] = {"due": date(2026, 9, 1)}
    assert task_agent.deadline_from_schedule(task("Сдать CRP"))["deadline"] == TODAY.isoformat()


def test_model_date_without_reason_is_overridden(due):
    due["Ferritin"] = {"due": date(2027, 2, 25)}
    t = task_agent.deadline_from_schedule(task("Сдать ферритин", deadline="2026-11-10"))
    assert t["deadline"] == "2027-02-25"


def test_early_with_reason_keeps_model_date_and_says_so(due):
    due["Ferritin"] = {"due": date(2027, 2, 25)}
    t = task_agent.deadline_from_schedule(task("Сдать ферритин", early="контроль после старта препарата железа"))
    assert t["deadline"] == "2026-11-10"
    assert "2027-02-25" in t["reason"] and "препарата железа" in t["reason"]


def test_unscheduled_keeps_model_date_with_mark(due):
    t = task_agent.deadline_from_schedule(task("Сдать кал на эластазу"))
    assert t["deadline"] == "2026-11-10" and t["reason"] != "r"


def test_schedule_merge_rule(monkeypatch):
    import clinical_kb
    monkeypatch.setattr(clinical_kb, "active_conditions", lambda *a, **k: set())
    monkeypatch.setattr(labs_db, "effective_freshness", lambda conds: {
        "ALT": {"days": 90, "priority": "high"}, "TSH": {"days": 365, "priority": "low"}})
    monkeypatch.setattr(labs_db, "get_effective_lab_schedule", lambda: {
        "ALT": {"interval_days": 180, "priority": "medium", "source": "code_seed", "note": ""},
        "TSH": {"interval_days": 120, "priority": "high", "source": "encounter:1", "note": ""},
        "CEA": {"interval_days": 100, "priority": "critical", "source": "encounter:2", "note": ""},
        "Amylase": {"interval_days": 180, "priority": "low", "source": "code_seed", "note": ""}})
    s = labs_db.tenant_lab_schedule()
    assert s["ALT"]["interval_days"] == 90 and s["ALT"]["source"] == "default"   # code_seed не перекрывает дефолт
    assert s["TSH"]["interval_days"] == 120                                       # правка врача главнее дефолта
    assert s["CEA"]["interval_days"] == 100                                       # только-БД, не code_seed
    assert "Amylase" not in s                                                     # только-code_seed — нет


def test_lab_due_from_last_point(monkeypatch):
    monkeypatch.setattr(labs_db, "tenant_lab_schedule", lambda: {
        "CEA": {"interval_days": 100, "priority": "critical", "source": "encounter:2", "note": ""}})
    monkeypatch.setattr(labs_db, "get_recent_labs", lambda **k: [
        {"test_name": "CEA", "date": "2026-06-01"}, {"test_name": "CEA", "date": "2026-02-01"}])
    d = labs_db.lab_due({"CEA", "Ferritin"}, TODAY)
    assert set(d) == {"CEA"} and d["CEA"]["due"] == date(2026, 9, 9)
