"""Wave 9.3HH: Status-actions для protocols/problems/experiments
+ audit page (inline-edit консультаций/периодов снят со страницами, 2026-09-26)."""
from __future__ import annotations


import pytest

pytestmark = pytest.mark.integration


# ── Protocols retire ─────────────────────────────────────────────────────

def test_protocol_retire(dashboard_client):
    client, db = dashboard_client
    pid = db.add_protocol("test protocol", status="active")
    r = client.post(f"/api/protocols/{pid}/retire")
    assert r.status_code == 200
    row = db.fetchone("SELECT status, retired_at FROM protocols WHERE id=?", (pid,))
    assert row["status"] == "retired"
    assert row["retired_at"] is not None


def test_protocol_retire_idempotent(dashboard_client):
    client, db = dashboard_client
    pid = db.add_protocol("already retired", status="retired")
    r = client.post(f"/api/protocols/{pid}/retire")
    assert r.status_code == 200
    assert r.text == ""


# ── Problems archive/reopen ──────────────────────────────────────────────

def test_problem_archive(dashboard_client):
    client, db = dashboard_client
    db.add_problem("test_problem", "test title", status="active_monitoring")
    pid = db.fetchone("SELECT id FROM problem_list WHERE problem_id=?", ("test_problem",))["id"]
    r = client.post(f"/api/problems/{pid}/archive")
    assert r.status_code == 200
    row = db.fetchone("SELECT status FROM problem_list WHERE id=?", (pid,))
    assert row["status"] == "resolved"


def test_problem_reopen(dashboard_client):
    client, db = dashboard_client
    db.add_problem("resolved_p", "test", status="resolved")
    pid = db.fetchone("SELECT id FROM problem_list WHERE problem_id=?", ("resolved_p",))["id"]
    r = client.post(f"/api/problems/{pid}/reopen")
    assert r.status_code == 200
    row = db.fetchone("SELECT status FROM problem_list WHERE id=?", (pid,))
    assert row["status"] == "active_monitoring"


# ── Experiments complete/cancel ─────────────────────────────────────────

# test_experiment_complete/cancel сняты (BL-EXP-1, 2026-07-10):
# эндпоинт /api/experiments/{id}/{action} и таблица experiments ретайрены.


# ── Consultations inline-edit ────────────────────────────────────────────

def test_audit_page_shows_recent_edits(dashboard_client):
    client, db = dashboard_client
    # Сделать какую-то правку чтобы был хоть один audit row
    pid = db.add_protocol("for audit")
    client.post(f"/api/protocols/{pid}/retire")

    r = client.get("/audit")
    assert r.status_code == 200
    assert "retire" in r.text


def test_audit_page_with_filter(dashboard_client):
    client, db = dashboard_client
    r = client.get("/audit?entity=memory")
    assert r.status_code == 200
