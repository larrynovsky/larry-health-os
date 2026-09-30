"""literature_curator — 4-way эскалация (SX-17c)."""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


def _seed_reading_finding(db):
    db.execute(
        "INSERT INTO agent_reports (date, agent_type, agent_name, has_findings, findings, pubmed_ids) "
        "VALUES (?, 'publication_reading', 'test', 1, ?, ?)",
        (date.today().isoformat(),
         json.dumps([{
             "pmid": "PM_LIT_1", "status": "kept",
             "title": "Effect of X on Y in survivors", "year": "2024",
             "journal": "J", "topic_id": "t1", "domain": "test",
             "claim": "X reduces Y", "population": "post-surgery",
             "evidence_level": "rct", "applicability_to_me": "direct",
             "relevance_assessment": "applies to you",
         }]),
         json.dumps(["PM_LIT_1"])),
    )


def test_curator_escalates_to_note_when_decision_says_note(db, anthropic_mock):
    _seed_reading_finding(db)
    anthropic_mock.script(
        match=lambda p: "Пять вариантов" in p or "curator" in p.lower() or "Четыре варианта" in p,
        response=json.dumps({
            "action": "note", "rationale": "interesting bg",
            "summary": "Lit summary",
            "linked_problem_id": None, "task_type": None,
            "task_deadline_days": None,
        }),
    )
    import literature_curator as lc
    result = lc.run(max_per_run=1)
    assert result["actions"].get("note", 0) == 1
    rows = db.fetchall("SELECT * FROM memory WHERE category='literature_note'")
    assert len(rows) >= 1


def test_curator_escalates_to_task(db, anthropic_mock):
    _seed_reading_finding(db)
    anthropic_mock.script(
        match=lambda p: "Четыре варианта" in p or "curator" in p.lower(),
        response=json.dumps({
            "action": "task", "rationale": "sdat FE-1",
            "summary": "sdat fecal elastase",
            "linked_problem_id": None,
            "task_type": "lab_test", "task_deadline_days": 14,
        }),
    )
    import literature_curator as lc
    result = lc.run(max_per_run=1)
    assert result["actions"].get("task", 0) == 1
    rows = db.fetchall("SELECT * FROM tasks WHERE source='literature_curator'")
    assert len(rows) >= 1
