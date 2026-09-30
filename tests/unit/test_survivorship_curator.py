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
