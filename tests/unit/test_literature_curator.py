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


def test_proposal_with_unknown_problem_id_becomes_note(monkeypatch):
    """01.10: модель вписала номер ГИПОТЕЗЫ (203069) как problem_id → карточка «этой проблемы
    нет». Несуществующий id — мысль без адресата: заметка, не предложение."""
    import literature_curator as lc
    monkeypatch.setattr(lc, "_duplicate_of_prior", lambda d: (False, ""))
    monkeypatch.setattr(lc.db, "get_problem_list", lambda *a, **k: [{"problem_id": "P007"}])
    notes, props = [], []
    monkeypatch.setattr(lc, "_execute_note", lambda f, d: notes.append(d) or 1)
    monkeypatch.setattr(lc.db, "save_problem_proposal", lambda **k: props.append(k) or 2)
    lc._execute_proposal({"pmid": "1"}, {"linked_problem_id": 203069, "summary": "s"})
    lc._execute_proposal({"pmid": "2"}, {"linked_problem_id": "P007", "summary": "s"})
    assert len(notes) == 1 and len(props) == 1, (notes, props)
