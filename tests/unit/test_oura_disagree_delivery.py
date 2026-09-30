"""tests/unit/test_oura_disagree_delivery.py — A1-долг: доставка Oura-расхождений.

Было detection-without-delivery: run_shadow() находил факты, противоречащие Oura, но
disagree уходил в лог, не человеку. Фикс: propose_oura_disagreements() пишет SUPERSEDE-
предложения в существующий Telegram-гейт. Идемпотентно (не дублирует / не воскрешает rejected).
"""
from __future__ import annotations
import pytest

pytestmark = pytest.mark.unit


def _seed_fact(value: str) -> int:
    import health_db
    with health_db.get_conn() as c:
        cur = c.execute("INSERT INTO memory_facts (mem_class, value, active) VALUES ('state', ?, 1)",
                        (value,))
        return cur.lastrowid


def _pending_super_for(fact_id: int) -> list:
    import health_db
    with health_db.get_conn() as c:
        return c.execute(
            "SELECT id, rationale FROM memory_consolidation_proposals "
            "WHERE fact_id=? AND action='SUPERSEDE' AND status='pending'", (fact_id,)).fetchall()


def test_disagree_becomes_supersede_proposal(db, monkeypatch):
    import memory_truthcheck as t
    import memory_consolidation as mc
    mc._ensure_proposals_table()
    fid = _seed_fact("2026-07-05: спал 8 часов")
    monkeypatch.setattr(t, "run_shadow", lambda: {
        "disagree": [(fid, [("total_h", 8.0, 5.1, False)], "2026-07-05: спал 8 часов")],
        "confirm": [], "unverifiable": 0, "counts": {}})
    r = t.propose_oura_disagreements()
    assert r["proposed"] == 1 and r["skipped"] == 0
    rows = _pending_super_for(fid)
    assert len(rows) == 1
    assert "Oura" in rows[0]["rationale"] and "8.0" in rows[0]["rationale"]


def test_idempotent_no_duplicate(db, monkeypatch):
    import memory_truthcheck as t
    import memory_consolidation as mc
    mc._ensure_proposals_table()
    fid = _seed_fact("2026-07-05: спал 8 часов")
    monkeypatch.setattr(t, "run_shadow", lambda: {
        "disagree": [(fid, [("total_h", 8.0, 5.1, False)], "2026-07-05: спал 8 часов")],
        "confirm": [], "unverifiable": 0, "counts": {}})
    t.propose_oura_disagreements()
    r2 = t.propose_oura_disagreements()
    assert r2["proposed"] == 0 and r2["skipped"] == 1, "повтор не дублирует"
    assert len(_pending_super_for(fid)) == 1


def test_rejected_not_revived(db, monkeypatch):
    """Человек сказал «оставить» (rejected) → не пред­лагаем снова."""
    import memory_truthcheck as t
    import memory_consolidation as mc
    import health_db
    mc._ensure_proposals_table()
    fid = _seed_fact("2026-07-05: спал 8 часов")
    monkeypatch.setattr(t, "run_shadow", lambda: {
        "disagree": [(fid, [("total_h", 8.0, 5.1, False)], "2026-07-05: спал 8 часов")],
        "confirm": [], "unverifiable": 0, "counts": {}})
    t.propose_oura_disagreements()
    with health_db.get_conn() as c:
        c.execute("UPDATE memory_consolidation_proposals SET status='rejected' WHERE fact_id=?", (fid,))
    r = t.propose_oura_disagreements()
    assert r["proposed"] == 0 and r["skipped"] == 1, "rejected не воскрешается"
