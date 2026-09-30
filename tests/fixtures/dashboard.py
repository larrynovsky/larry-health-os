"""
tests/fixtures/dashboard.py — fixture `dashboard_client` для тестирования
FastAPI dashboard через TestClient.

Зависит от fixture `db` (tests/fixtures/db.py): тот патчит health_db.DB_PATH,
а dashboard после Wave 9.3HA читает health_db.DB_PATH лениво — автоматически
унаследует test-БД.

Использование:

    def test_profile_edit(dashboard_client):
        client, db = dashboard_client
        db.add_profile("test.x", "a", category="test")
        r = client.post("/api/profile/test.x", data={"value": "b"})
        assert r.status_code == 200
"""
from __future__ import annotations

import pytest


@pytest.fixture
def dashboard_client(db):
    """TestClient над FastAPI app dashboard на tmp-БД."""
    from fastapi.testclient import TestClient
    import dashboard as dash

    # TestClient управляет lifespan через context manager.
    # __enter__ triggers startup (миграция dashboard_edits на test-БД).
    with TestClient(dash.app) as client:
        yield client, db
