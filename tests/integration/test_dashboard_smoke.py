"""Wave 9.3HD: Smoke tests для всех dashboard routes.

Проверяет что все endpoints не падают на 500 при типичных запросах.
Не валидирует контент глубоко — только status codes и базовый sanity.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


# ── GET endpoints ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/", "/profile", "/hypotheses", "/protocols", "/tasks",
    "/problems", "/proposals",
    "/labs", "/constitutions", "/audit",
    "/medical-record",
])
def test_all_get_routes_return_200(dashboard_client, path):
    client, _ = dashboard_client
    r = client.get(path)
    assert r.status_code == 200, f"{path} returned {r.status_code}"


def test_api_ping_returns_pong_chip(dashboard_client):
    client, _ = dashboard_client
    r = client.get("/api/ping")
    assert r.status_code == 200
    assert "pong" in r.text


def test_constitutions_404_on_nonexistent_slug(dashboard_client):
    client, _ = dashboard_client
    r = client.get("/constitutions/nonexistent_slug_xyz")
    assert r.status_code == 404


# ── API edge cases ─────────────────────────────────────────────────────────

def test_api_profile_404_on_unknown_key(dashboard_client):
    client, _ = dashboard_client
    r = client.get("/api/profile/no.such.key/edit")
    assert r.status_code == 404


def test_api_hypothesis_invalid_action_400(dashboard_client):
    client, _ = dashboard_client
    r = client.post("/api/hypotheses/1/foobar")
    assert r.status_code == 400


def test_api_problem_invalid_action_400(dashboard_client):
    client, _ = dashboard_client
    r = client.post("/api/problems/1/foobar")
    assert r.status_code == 400


def test_api_proposal_invalid_action_400(dashboard_client):
    client, _ = dashboard_client
    r = client.post("/api/proposals/1/foobar")
    assert r.status_code == 400


def test_hae_ingest_requires_token(dashboard_client):
    """WRITE-эндпоинт HAE-ingest без токена — отклонён (401 неверный / 503 не настроен)."""
    client, _ = dashboard_client
    r = client.post("/hae/ingest", json={"data": {"metrics": []}})
    assert r.status_code in (401, 503), f"без токена должно отклоняться, got {r.status_code}"


def test_hae_ecg_requires_token(dashboard_client):
    """WRITE-эндпоинт HAE-ECG без токена — отклонён (401 неверный / 503 не настроен)."""
    client, _ = dashboard_client
    r = client.post("/hae/ecg", json={"data": {"ecg": []}})
    assert r.status_code in (401, 503), f"без токена должно отклоняться, got {r.status_code}"


def test_api_task_snooze_invalid_days_400(dashboard_client):
    client, _ = dashboard_client
    # 99999 — out of range 1..365
    r = client.post("/api/tasks/1/snooze?days=99999")
    assert r.status_code in (400, 404)  # 404 если task=1 не существует, 400 если есть


