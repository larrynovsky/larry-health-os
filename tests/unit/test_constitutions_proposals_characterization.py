"""
tests/unit/test_constitutions_proposals_characterization.py — пины доменов
constitutions и problem_list_proposals (Поток B добор, 2026-06-27).
Готовят функции к выносу в Потоке C.

behavior-preserving пины: «как есть» ≠ «как правильно».
"""
from __future__ import annotations

import health_db


# ── constitutions ────────────────────────────────────────────────────────────

def test_upsert_get_constitution(db):
    health_db.upsert_constitution("sleep", "body text", title="Sleep")
    c = health_db.get_constitution("sleep")
    assert c["body_md"] == "body text"
    assert c["title"] == "Sleep"


def test_upsert_constitution_upserts_by_domain(db):
    health_db.upsert_constitution("sleep", "v1")
    health_db.upsert_constitution("sleep", "v2")
    assert health_db.get_constitution("sleep")["body_md"] == "v2"
    domains = [c["domain"] for c in health_db.list_constitutions()]
    assert domains.count("sleep") == 1


def test_get_constitution_missing_returns_none(db):
    assert health_db.get_constitution("nope") is None


def test_list_constitutions_excludes_body_has_size(db):
    health_db.upsert_constitution("sleep", "abcde", title="S")
    e = [x for x in health_db.list_constitutions() if x["domain"] == "sleep"][0]
    assert "body_md" not in e
    assert e["size_bytes"] == 5


# ── problem_list_proposals ───────────────────────────────────────────────────

def test_get_pending_proposals_includes_new(db):
    health_db.save_problem_proposal("gp", [{"action": "add", "problem_id": "p1"}])
    pending = health_db.get_pending_proposals()
    assert len(pending) >= 1
    assert pending[0]["status"] == "pending"


def test_reject_proposal_removes_from_pending(db):
    pid = health_db.save_problem_proposal("gp", [])
    health_db.reject_proposal(pid, note="no")
    assert all(p["id"] != pid for p in health_db.get_pending_proposals())


def test_apply_empty_proposal_returns_zero(db):
    pid = health_db.save_problem_proposal("gp", [])
    assert health_db.apply_proposal(pid) == 0
