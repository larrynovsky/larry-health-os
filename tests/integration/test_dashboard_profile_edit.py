"""Wave 9.3HE: Profile inline-edit tests.

Покрывает 4 кейса:
1. Round-trip value_text edit
2. value_json edit с валидным JSON
3. value_json edit с НЕвалидным JSON → 422 + value не меняется
4. GET edit на несуществующий key → 404
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_profile_edit_value_text_roundtrip(dashboard_client):
    client, db = dashboard_client
    db.add_profile("test.weight", value_text="75", category="identity")

    # GET edit returns input
    r1 = client.get("/api/profile/test.weight/edit")
    assert r1.status_code == 200
    assert "input" in r1.text
    assert "75" in r1.text

    # POST save
    r2 = client.post("/api/profile/test.weight", data={"value": "76"})
    assert r2.status_code == 200
    assert "76" in r2.text

    # БД обновилась
    row = db.fetchone("SELECT value_text, updated_by FROM patient_profile WHERE key=?",
                     ("test.weight",))
    assert row["value_text"] == "76"
    assert row["updated_by"] == "dashboard"

    # Audit запись создалась
    audit = db.fetchone("""SELECT entity, action, field, old_value, new_value
                            FROM dashboard_edits ORDER BY id DESC LIMIT 1""")
    assert audit["entity"] == "patient_profile"
    assert audit["action"] == "edit_field"
    assert audit["field"] == "value_text"
    assert audit["old_value"] == "75"
    assert audit["new_value"] == "76"


def test_profile_edit_value_json_valid(dashboard_client):
    client, db = dashboard_client
    db.add_profile("test.medical", value_json='{"hba1c": 5.4}', category="medical")

    new_json = '{"hba1c": 5.6, "glucose": 90}'
    r = client.post("/api/profile/test.medical", data={"value": new_json})
    assert r.status_code == 200

    row = db.fetchone("SELECT value_json FROM patient_profile WHERE key=?",
                     ("test.medical",))
    import json
    saved = json.loads(row["value_json"])
    assert saved == {"hba1c": 5.6, "glucose": 90}


def test_profile_edit_value_json_invalid_returns_422(dashboard_client):
    client, db = dashboard_client
    db.add_profile("test.bad_json", value_json='{"valid": true}', category="test")

    r = client.post("/api/profile/test.bad_json", data={"value": "{invalid json"})
    assert r.status_code == 422

    # БД НЕ обновилась
    row = db.fetchone("SELECT value_json FROM patient_profile WHERE key=?",
                     ("test.bad_json",))
    assert '"valid": true' in row["value_json"]


def test_profile_edit_unknown_key_returns_404(dashboard_client):
    client, _ = dashboard_client
    r = client.post("/api/profile/no.such.key", data={"value": "x"})
    assert r.status_code == 404
