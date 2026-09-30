"""Wave 9.3HG: Tasks done + snooze + auto-reawake tests."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.integration


def test_task_done(dashboard_client):
    client, db = dashboard_client
    tid = db.add_task("сдать анализы")
    r = client.post(f"/api/tasks/{tid}/done")
    assert r.status_code == 200
    row = db.fetchone("SELECT status, resolved_at, resolution_type FROM tasks WHERE id=?", (tid,))
    assert row["status"] == "completed"
    assert row["resolved_at"] is not None
    assert row["resolution_type"] == "completed_via_dashboard"


def test_task_snooze_sets_deadline(dashboard_client):
    client, db = dashboard_client
    tid = db.add_task("отложенная задача")
    r = client.post(f"/api/tasks/{tid}/snooze?days=7")
    assert r.status_code == 200
    row = db.fetchone("SELECT status, deadline FROM tasks WHERE id=?", (tid,))
    assert row["status"] == "snoozed"
    expected = (date.today() + timedelta(days=7)).isoformat()
    assert row["deadline"] == expected


def test_snooze_invalid_days_400(dashboard_client):
    client, db = dashboard_client
    tid = db.add_task("test")
    r = client.post(f"/api/tasks/{tid}/snooze?days=99999")
    assert r.status_code == 400


def test_tasks_route_hides_future_snoozed(dashboard_client):
    """Snoozed task с deadline в будущем НЕ показывается в основном списке."""
    client, db = dashboard_client
    future_date = (date.today() + timedelta(days=30)).isoformat()
    tid = db.add_task("hidden snooze", status="snoozed", deadline=future_date)

    r = client.get("/tasks")
    assert r.status_code == 200
    # task content не в HTML
    assert "hidden snooze" not in r.text


def test_tasks_route_shows_past_snoozed_auto_reawake(dashboard_client):
    """Snoozed task с deadline в прошлом ВСПЛЫВАЕТ в основной список."""
    client, db = dashboard_client
    past_date = (date.today() - timedelta(days=1)).isoformat()
    tid = db.add_task("reawake me", status="snoozed", deadline=past_date)

    r = client.get("/tasks")
    assert r.status_code == 200
    # task content ПРИСУТСТВУЕТ в HTML (всплыла)
    assert "reawake me" in r.text


def test_task_edit_content_roundtrip(dashboard_client):
    """Inline-edit content задачи."""
    client, db = dashboard_client
    tid = db.add_task("старый текст")

    # GET edit
    r1 = client.get(f"/api/tasks/{tid}/edit")
    assert r1.status_code == 200
    assert "старый текст" in r1.text

    # POST save
    r2 = client.post(f"/api/tasks/{tid}", data={"value": "новый текст"})
    assert r2.status_code == 200

    row = db.fetchone("SELECT content FROM tasks WHERE id=?", (tid,))
    assert row["content"] == "новый текст"


def test_task_done_idempotent_on_completed(dashboard_client):
    """Повторный done на completed task → 200 empty, не падает."""
    client, db = dashboard_client
    tid = db.add_task("test", status="completed")
    r = client.post(f"/api/tasks/{tid}/done")
    assert r.status_code == 200
    assert r.text == ""
