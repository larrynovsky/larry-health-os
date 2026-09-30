"""Wave 9.3HF: Hypothesis confirm/reject + cascade tests.

Главный тест — cascade «confirm hypothesis с payload.test → INSERT в tasks».
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_confirm_with_test_creates_task(dashboard_client):
    """Главный cascade: confirm hypothesis с payload.test → новая task."""
    client, db = dashboard_client
    hyp_id = db.add_hypothesis({
        "observation": "HRV упал на 30%",
        "test": "Сдать анализы ферритина и B12 в течение 7 дней",
        "trigger": "drift",
    })

    tasks_before = db.count("tasks")
    r = client.post(f"/api/hypotheses/{hyp_id}/confirm")
    assert r.status_code == 200

    # active=1 (confirmed hypothesis stays visible), resolution_type=confirmed
    row = db.fetchone("SELECT active, value FROM memory WHERE id=?", (hyp_id,))
    assert row["active"] == 1
    import json
    payload = json.loads(row["value"])
    # L1: status поле должно быть выставлено
    assert payload["status"] == "confirmed"
    # L3: resolved_as — action-time; resolution_type НЕ должен быть перезаписан
    assert payload["resolved_as"] == "confirmed"
    assert "resolution_type" not in payload  # creation-time поле не трогаем если не было
    assert payload["resolved_by"] == "dashboard"

    # Новая task создана
    assert db.count("tasks") == tasks_before + 1
    new_task = db.fetchone("""SELECT content, source, type, priority, status
                              FROM tasks ORDER BY id DESC LIMIT 1""")
    assert "ферритин" in new_task["content"]
    assert new_task["source"] == "dashboard_confirm"
    assert new_task["type"] == "followup"
    assert new_task["priority"] == "medium"
    assert new_task["status"] == "open"


def test_confirm_without_test_does_not_create_task(dashboard_client):
    """Confirm без payload.test НЕ создаёт task."""
    client, db = dashboard_client
    hyp_id = db.add_hypothesis({"observation": "no test field"})

    tasks_before = db.count("tasks")
    r = client.post(f"/api/hypotheses/{hyp_id}/confirm")
    assert r.status_code == 200
    assert db.count("tasks") == tasks_before  # никаких новых задач


def test_reject_does_not_create_task(dashboard_client):
    """Reject с payload.test тоже НЕ создаёт task (cascade только для confirm)."""
    client, db = dashboard_client
    hyp_id = db.add_hypothesis({
        "observation": "rejected case",
        "test": "должен быть проигнорирован при reject",
    })

    tasks_before = db.count("tasks")
    r = client.post(f"/api/hypotheses/{hyp_id}/reject")
    assert r.status_code == 200
    assert db.count("tasks") == tasks_before


def test_confirm_idempotent_on_inactive(dashboard_client):
    """Повторный confirm на already-inactive hypothesis → 200 пустой, no side-effect."""
    client, db = dashboard_client
    hyp_id = db.add_hypothesis({"observation": "x", "test": "y"}, active=0)

    tasks_before = db.count("tasks")
    r = client.post(f"/api/hypotheses/{hyp_id}/confirm")
    assert r.status_code == 200
    assert r.text == ""  # empty body для hx-swap=delete
    # Task НЕ создалась (idempotency)
    assert db.count("tasks") == tasks_before


def test_invalid_action_returns_400(dashboard_client):
    client, db = dashboard_client
    hyp_id = db.add_hypothesis({"observation": "x"})
    r = client.post(f"/api/hypotheses/{hyp_id}/foobar")
    assert r.status_code == 400


def test_nonexistent_hypothesis_returns_404(dashboard_client):
    client, _ = dashboard_client
    r = client.post("/api/hypotheses/999999/confirm")
    assert r.status_code == 404


def test_audit_log_records_both_actions(dashboard_client):
    """При confirm с cascade — audit записывает И confirm И create_from_hypothesis_confirm."""
    client, db = dashboard_client
    hyp_id = db.add_hypothesis({"observation": "x", "test": "сдать тест"})

    client.post(f"/api/hypotheses/{hyp_id}/confirm")
    audit = db.fetchall("""SELECT entity, action FROM dashboard_edits
                          ORDER BY id DESC LIMIT 5""")
    actions = [(r["entity"], r["action"]) for r in audit]
    # Должны быть обе записи (в каком-либо порядке)
    assert ("memory", "confirm") in actions
    assert ("tasks", "create_from_hypothesis_confirm") in actions
