"""survivorship_curator — 4-way эскалация (SX-17f); constitution_conflict снят 28.09.2026."""
from __future__ import annotations

import json
from datetime import date

import pytest

pytestmark = pytest.mark.unit


def _seed_analyzer_output(db):
    findings = {
        "per_instrument": {
            "isi": [{"type": "shadow_check", "instrument": "isi",
                      "subscale": "total", "pro_value": 10,
                      "pro_date": str(date.today()), "proxy_values": {"hrv": {"mean": 18, "n": 7}},
                      "note": "test shadow"}]
        },
        "global": [],
    }
    db.execute(
        "INSERT INTO agent_reports (date, agent_type, agent_name, has_findings, findings, pubmed_ids) "
        "VALUES (?, 'survivorship_analysis', 'test', 1, ?, '[]')",
        (date.today().isoformat(), json.dumps(findings)),
    )


def test_curator_escalates_to_note(db, anthropic_mock):
    _seed_analyzer_output(db)
    anthropic_mock.script(
        match=lambda p: "Четыре варианта" in p,
        response=json.dumps({
            "action": "note", "rationale": "interesting", "summary": "summary text",
            "linked_problem_id": None, "linked_constitution": None,
            "task_type": None, "task_deadline_days": None,
        }),
    )
    import survivorship_curator as sc
    result = sc.run(max_per_run=1)
    assert result["actions"].get("note", 0) == 1
    rows = db.fetchall("SELECT * FROM memory WHERE category='survivorship_note'")
    assert len(rows) >= 1


def test_old_constitution_conflict_answer_becomes_note(db, anthropic_mock):
    """28.09.2026: находка куратора короче горизонта конституции — конфликт не создаётся,
    старый ответ модели читается как note (сигнал остаётся для месячного консилиума)."""
    import json as _j, survivorship_curator as sc
    anthropic_mock.script(
        match=lambda p: "Четыре варианта" in p,
        response=_j.dumps({
            "action": "constitution_conflict", "rationale": "учебная причина",
            "summary": "учебная сводка", "linked_problem_id": None,
            "task_type": None, "task_deadline_days": None}))
    _seed_analyzer_output(db)
    result = sc.run(max_per_run=1)
    assert result["actions"].get("note", 0) == 1 and "constitution_conflict" not in result["actions"]
    assert not db.fetchall("SELECT * FROM memory WHERE category='constitution_conflict'")
    assert db.fetchall("SELECT * FROM memory WHERE category='survivorship_note'")


def test_report_summary_names_last_lab_date(db, anthropic_mock, clock, monkeypatch):
    """13.10: сводка совета «сдать CRP, TSH, B12» уезжала в отчёт куратора без дат
    последней сдачи, и ночной check_reco_repeats_fresh_lab краснел. Задача получала дату
    в tasks_db.save_task, отчёт — нет. Синтетический ряд, окна из учебного расписания."""
    from datetime import timedelta
    today = date.today()                 # _seed_analyzer_output датирует находку date.today()
    clock.set(today.isoformat())
    import gp_context as gc
    sched = {"CRP": {"interval_days": 90}, "TSH": {"interval_days": 365},
             "Vitamin_B12": {"interval_days": 105}}
    b12 = today - timedelta(days=88)
    labs = [{"test_name": "CRP", "date": str(today - timedelta(days=45))},
            {"test_name": "TSH", "date": str(today - timedelta(days=142))},
            {"test_name": "Vitamin_B12", "date": str(b12)}]
    monkeypatch.setattr(gc._ldb, "get_recent_labs", lambda n: labs)
    monkeypatch.setattr(gc._ldb, "get_effective_lab_schedule", lambda: sched)
    _seed_analyzer_output(db)
    anthropic_mock.script(
        match=lambda p: "Четыре варианта" in p,
        response=json.dumps({
            "action": "task", "rationale": "контроль",
            "summary": "Сдать CRP, TSH и витамин B12 в течение 14 дней",
            "linked_problem_id": None, "task_type": "lab_test", "task_deadline_days": 14}))
    import survivorship_curator as sc
    sc.run(max_per_run=1)
    rows = db.fetchall("SELECT findings FROM agent_reports WHERE agent_name='survivorship_curator'")
    assert rows
    text = rows[0]["findings"]
    last = {r["test_name"]: r["date"] for r in labs}
    assert gc.recommendations_without_evidence(text, last, sched) == []
    assert b12.strftime("%d.%m.%Y") in text
