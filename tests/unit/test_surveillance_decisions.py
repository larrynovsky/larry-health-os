"""Решения врача по наблюдению — дом и его читатели (нить treatment-facts, 2026-08-30).

Решение врача об отсрочке должно быть доступно читателям, иначе куратор повторно
предложит отклонённое наблюдение. Здесь придуманы тема, врач и даты:
запись/чтение/retire по теме; блок GP видит решение; куратор дедуплицирует
предложения (судья замокан) и направляет «без problem_id» в заметку, не proposal.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_record_read_and_retire_by_topic(db, clock):
    clock.set("2021-03-15")
    import health_db as hdb
    a = hdb.record_surveillance_decision("bone_density", "Денситометрия / DEXA", "defer",
                                         "эндокринолог", "owner_word:2021-03-15", valid_until="2021-12-31")
    active = hdb.active_surveillance_decisions()
    assert [d["id"] for d in active] == [a]
    b = hdb.record_surveillance_decision("bone_density", "Денситометрия", "do", "эндокринолог", "owner_word:2022-01-10",
                                         decided_on="2022-01-10")
    active = hdb.active_surveillance_decisions(on_date="2022-01-10")
    assert [d["id"] for d in active] == [b], "прежнее решение по той же теме обязано уйти в retired"
    clock.set("2022-02-01")
    assert hdb.active_surveillance_decisions() and all(d["retired_at"] is None for d in hdb.active_surveillance_decisions())


def test_expired_decision_is_not_active(db, clock):
    clock.set("2022-01-01")
    import health_db as hdb
    hdb.record_surveillance_decision("bone_density", "Денситометрия", "defer", "эндокринолог", "owner_word",
                                     decided_on="2021-03-15", valid_until="2021-12-31")
    assert hdb.active_surveillance_decisions() == []


def test_gp_block_shows_decision_and_is_empty_without(db, clock):
    clock.set("2021-03-15")
    import gp_context as g, health_db as hdb
    assert g._build_surveillance_decisions_block() == []
    hdb.record_surveillance_decision("bone_density", "Денситометрия / DEXA", "defer",
                                     "эндокринолог", "owner_word:2021-03-15", valid_until="2021-12-31")
    block = "\n".join(g._build_surveillance_decisions_block())
    assert "РЕШЕНИЯ ВРАЧА" in block and "Денситометрия" in block and "эндокринолог" in block and "2021-12-31" in block


def test_curator_dedups_proposal_against_decision(db, clock, monkeypatch):
    clock.set("2021-03-15")
    import health_db as hdb, literature_curator as lc, hypothesis_semantic_check as sc
    hdb.record_surveillance_decision("bone_density", "Денситометрия / DEXA", "defer",
                                     "эндокринолог", "owner_word:2021-03-15", valid_until="2021-12-31")
    seen = {}
    def fake_compare(cand, existing):
        seen["existing"] = existing
        return (True, existing[0]["memory_id"], "та же тема: денситометрия")
    monkeypatch.setattr(sc, "_haiku_compare", fake_compare)
    saved = []
    monkeypatch.setattr(hdb, "save_problem_proposal", lambda **kw: saved.append(kw) or 1)
    decision = {"summary": "Внедрить денситометрию и витамин D в план наблюдения", "rationale": "риск остеопении",
                "linked_problem_id": "P001"}
    rid = lc._execute_proposal({"pmid": "1"}, decision)
    assert rid is None and decision.get("_dedup") and saved == []
    assert any("решение врача" in str(e["status"]) for e in seen["existing"]), "решение врача обязано быть среди кандидатов дедупа"


def test_curator_routes_proposal_without_problem_to_note(db, clock, monkeypatch):
    clock.set("2021-03-15")
    import health_db as hdb, literature_curator as lc, hypothesis_semantic_check as sc
    monkeypatch.setattr(sc, "_haiku_compare", lambda c, e: (False, None, "no"))
    saved = []
    monkeypatch.setattr(hdb, "save_problem_proposal", lambda **kw: saved.append(kw) or 1)
    notes = []
    monkeypatch.setattr(lc, "_execute_note", lambda f, d: notes.append(d) or 7)
    decision = {"summary": "Формализовать наблюдение плотности костей", "rationale": "…", "linked_problem_id": None}
    rid = lc._execute_proposal({"pmid": "2"}, decision)
    assert rid == 7 and notes and saved == [] and decision.get("_routed") == "note"
