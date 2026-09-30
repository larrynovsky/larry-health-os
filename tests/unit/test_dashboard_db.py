"""Sprint 5b unit-тесты для dashboard_db (low-level DB layer)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# ── _ensure_dashboard_edits_table ──────────────────────────────────────────

def test_ensure_dashboard_edits_table_creates(db):
    from dashboard_db import _ensure_dashboard_edits_table
    _ensure_dashboard_edits_table()
    rows = db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='dashboard_edits'"
    )
    assert len(rows) == 1


def test_ensure_dashboard_edits_table_idempotent(db):
    """Повторный вызов не падает."""
    from dashboard_db import _ensure_dashboard_edits_table
    _ensure_dashboard_edits_table()
    _ensure_dashboard_edits_table()
    _ensure_dashboard_edits_table()
    rows = db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_dashboard_edits_entity'"
    )
    assert len(rows) == 1


# ── _q (read) ──────────────────────────────────────────────────────────────

def test_q_returns_rows(db):
    from dashboard_db import _q
    db.add_problem("P001", "Test problem", priority=1)
    rows = _q("SELECT problem_id, title FROM problem_list WHERE priority=?", (1,))
    assert len(rows) == 1
    assert rows[0]["problem_id"] == "P001"
    assert rows[0]["title"] == "Test problem"


def test_q_empty_result(db):
    from dashboard_db import _q
    rows = _q("SELECT * FROM problem_list WHERE problem_id='NONEXISTENT'")
    assert rows == []


def test_q_returns_readonly_connection(db):
    """_q использует mode=ro URI — попытка write должна упасть."""
    import sqlite3
    from dashboard_db import _conn
    with _conn() as c:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            c.execute("INSERT INTO problem_list (problem_id, title, first_seen, last_updated) "
                      "VALUES ('X', 'Y', '2026-01-01', '2026-01-01')")


# ── _w (write) ─────────────────────────────────────────────────────────────

def test_w_inserts_row(db):
    from dashboard_db import _w, _ensure_dashboard_edits_table
    _ensure_dashboard_edits_table()
    last_id = _w(
        "INSERT INTO dashboard_edits (entity, entity_id, action) VALUES (?, ?, ?)",
        ("test_entity", "1", "test_action"),
    )
    assert last_id == 1
    rows = db.fetchall("SELECT * FROM dashboard_edits WHERE id=?", (last_id,))
    assert len(rows) == 1
    assert rows[0]["entity"] == "test_entity"


def test_w_returns_lastrowid(db):
    from dashboard_db import _w, _ensure_dashboard_edits_table
    _ensure_dashboard_edits_table()
    id1 = _w("INSERT INTO dashboard_edits (entity, entity_id, action) VALUES (?, ?, ?)",
             ("e", "1", "a"))
    id2 = _w("INSERT INTO dashboard_edits (entity, entity_id, action) VALUES (?, ?, ?)",
             ("e", "2", "a"))
    assert id2 == id1 + 1


# ── _log_edit ──────────────────────────────────────────────────────────────

def test_log_edit_writes_audit_row(db):
    from dashboard_db import _log_edit, _ensure_dashboard_edits_table
    _ensure_dashboard_edits_table()
    _log_edit("tasks", 42, "complete",
              field="status", old_value="open", new_value="completed",
              actor="dashboard")
    rows = db.fetchall("SELECT * FROM dashboard_edits WHERE entity_id='42'")
    assert len(rows) == 1
    r = rows[0]
    assert r["entity"] == "tasks"
    assert r["action"] == "complete"
    assert r["field"] == "status"
    assert r["old_value"] == "open"
    assert r["new_value"] == "completed"
    assert r["actor"] == "dashboard"


def test_log_edit_default_actor(db):
    from dashboard_db import _log_edit, _ensure_dashboard_edits_table
    _ensure_dashboard_edits_table()
    _log_edit("x", 1, "a")
    rows = db.fetchall("SELECT actor FROM dashboard_edits")
    assert rows[0]["actor"] == "dashboard"


def test_log_edit_with_none_values(db):
    """field/old/new могут быть None — пишутся как NULL."""
    from dashboard_db import _log_edit, _ensure_dashboard_edits_table
    _ensure_dashboard_edits_table()
    _log_edit("x", 1, "create", field=None, old_value=None, new_value=None)
    rows = db.fetchall("SELECT * FROM dashboard_edits")
    assert rows[0]["field"] is None
    assert rows[0]["old_value"] is None


def test_log_edit_entity_id_coerced_to_string(db):
    """entity_id int → str(entity_id)."""
    from dashboard_db import _log_edit, _ensure_dashboard_edits_table
    _ensure_dashboard_edits_table()
    _log_edit("x", 12345, "a")
    rows = db.fetchall("SELECT entity_id FROM dashboard_edits")
    assert rows[0]["entity_id"] == "12345"  # str, not int
