"""Новая проблема приходит владельцу уже с простым текстом и ложится в медкарту с ним (27.09).

До этого plain_summary писал только разовый акт волны 4; писатели предложений его не несли,
и каждая новая проблема снова читалась человеку медицинским заголовком.
"""
import json

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def calls(monkeypatch):
    import problems_db as pdb
    seen = []

    def fake(text):
        seen.append(text)
        return "Простое описание"
    monkeypatch.setattr(pdb, "_plain_llm", fake)
    return seen


def _add(title="Iron deficiency without anaemia", desc="IDWA, ferritin low"):
    return [{"action": "add", "problem_id": None,
             "new_value": {"title": title, "description": desc, "status": "active"}}]


def test_add_proposal_carries_plain_and_apply_writes_it(db, calls):
    import problems_db as pdb
    import proposals_db as prdb
    pid = pdb.save_problem_proposal("document", _add())
    prop = [p for p in prdb.get_pending_proposals() if p["id"] == pid][0]
    assert json.loads(prop["proposed"])[0]["plain_summary"] == "Простое описание"
    assert "Простыми словами: Простое описание" in prdb.format_proposal_card(prop)
    prdb.apply_proposal(pid)
    row = db.fetchone("SELECT plain_summary FROM problem_list WHERE title=?", ("Iron deficiency without anaemia",))
    assert row["plain_summary"] == "Простое описание"


def test_repeat_does_not_call_model_again(db, calls):
    import problems_db as pdb
    pdb.save_problem_proposal("gp_weekly", _add())
    pdb.save_problem_proposal("gp_weekly", _add())
    assert len(calls) == 1


def test_onboarding_uses_persons_own_words(db, calls):
    import problems_db as pdb
    import proposals_db as prdb
    pid = pdb.save_problem_proposal("onboarding", _add(title="болит колено", desc="болит колено"))
    prop = [p for p in prdb.get_pending_proposals() if p["id"] == pid][0]
    assert json.loads(prop["proposed"])[0]["plain_summary"] == "болит колено" and calls == []


def test_model_failure_leaves_it_empty_not_invented(db, monkeypatch):
    import problems_db as pdb
    import proposals_db as prdb

    def boom(text):
        raise RuntimeError("нет сети")
    monkeypatch.setattr(pdb, "_plain_llm", boom)
    pid = pdb.save_problem_proposal("document", _add())
    prop = [p for p in prdb.get_pending_proposals() if p["id"] == pid][0]
    assert json.loads(prop["proposed"])[0]["plain_summary"] is None
